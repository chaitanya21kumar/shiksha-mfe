"""FastAPI gateway for the LMS AI Engine.

Phase 1 is intentionally small: an app factory plus the system probes
(``/health`` for liveness, ``/ready`` for readiness). Feature routers
(document ingestion, summarisation, …) are added in their own modules as each
module lands.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import settings
from .ingestion.router import router as ingestion_router
from .summarization.llm_client import LLMTimeout, LLMUnavailable
from .summarization.pipeline import EmptyDocumentError
from .summarization.router import router as summarization_router

logger = logging.getLogger("ai_engine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s (env=%s)", settings.app_name, settings.version, settings.environment)
    # Two shared clients, reused across requests for connection pooling. The probe
    # client has a tight timeout (a slow /ready should fail fast); the LLM client
    # has a long one because generating on a model takes many seconds.
    app.state.http_client = httpx.AsyncClient(timeout=2.0)
    app.state.llm_client = httpx.AsyncClient(timeout=settings.llm_request_timeout)
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await app.state.llm_client.aclose()
        logger.info("Stopping %s", settings.app_name)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        summary="Local-first, LMS-agnostic AI engine: documents and media into portable micro-learning.",
        lifespan=lifespan,
    )
    # Populated on startup; these guards keep calls before lifespan runs from crashing.
    app.state.http_client = None
    app.state.llm_client = None

    @app.get("/", tags=["system"])
    async def root() -> dict:
        return {
            "service": settings.app_name,
            "version": settings.version,
            "docs": "/docs",
            "health": "/health",
            "ready": "/ready",
        }

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        """Liveness: the process is up. Touches no external dependency."""
        return {
            "status": "ok",
            "service": settings.app_name,
            "version": settings.version,
            "environment": settings.environment,
        }

    @app.get("/ready", tags=["system"])
    async def ready() -> JSONResponse:
        """Readiness: can we reach the model gateway? (lists models on the OpenAI-compatible endpoint)."""
        components: dict[str, str] = {}
        client: httpx.AsyncClient | None = app.state.http_client
        try:
            if client is None:
                raise RuntimeError("HTTP client not initialised")
            resp = await client.get(
                f"{settings.llm_base_url}/models",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            )
            resp.raise_for_status()
            components["llm"] = "ok"
        except Exception:  # noqa: BLE001 - probe must never raise; report instead
            components["llm"] = "unreachable"

        is_ready = all(state == "ok" for state in components.values())
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={"ready": is_ready, "components": components},
        )

    # Map the summarisation pipeline's domain errors to the documented responses,
    # so the endpoints stay thin and never raise transport-layer exceptions themselves.
    @app.exception_handler(EmptyDocumentError)
    async def _on_empty_document(request: Request, exc: EmptyDocumentError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(LLMUnavailable)
    async def _on_llm_unavailable(request: Request, exc: LLMUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(LLMTimeout)
    async def _on_llm_timeout(request: Request, exc: LLMTimeout) -> JSONResponse:
        return JSONResponse(status_code=504, content={"detail": str(exc)})

    app.include_router(ingestion_router)
    app.include_router(summarization_router)
    return app


app = create_app()
