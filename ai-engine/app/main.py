"""FastAPI gateway for the LMS AI Engine.

Phase 1 is intentionally small: an app factory plus the system probes
(``/health`` for liveness, ``/ready`` for readiness). Feature routers
(document ingestion, assessment, multimedia, studio) are added in their
own modules as each module lands.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import settings
from .ingestion.router import router as ingestion_router

logger = logging.getLogger("ai_engine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s (env=%s)", settings.app_name, settings.version, settings.environment)
    yield
    logger.info("Stopping %s", settings.app_name)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        summary="Local-first, LMS-agnostic AI engine: documents and media into portable micro-learning.",
        lifespan=lifespan,
    )

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
        """Readiness: can we reach what we depend on? (currently the Ollama model gateway)."""
        components: dict[str, str] = {}
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{settings.ollama_base_url}/api/version")
                resp.raise_for_status()
            components["ollama"] = "ok"
        except Exception:  # noqa: BLE001 - probe must never raise; report instead
            components["ollama"] = "unreachable"

        is_ready = all(state == "ok" for state in components.values())
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={"ready": is_ready, "components": components},
        )

    app.include_router(ingestion_router)
    return app


app = create_app()
