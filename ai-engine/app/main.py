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

from .assessment.emit import EmptyAssessmentError
from .assessment.router import router as assessment_router
from .chaptering.pipeline import EmptyTranscriptError
from .chaptering.router import router as chaptering_router
from .config import settings
from .ingestion.router import router as ingestion_router
from .interactive_video.router import router as interactive_video_router
from .microlesson.emit import EmptyLessonError
from .microlesson.router import router as microlesson_router
from .course.router import router as course_router
from .narration.router import router as narration_router
from .summarization.llm_client import LLMTimeout, LLMUnavailable
from .summarization.pipeline import EmptyDocumentError
from .summarization.router import router as summarization_router
from .transcription.router import router as transcription_router
from .transcription.stt_client import STTTimeout, STTUnavailable

logger = logging.getLogger("ai_engine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s (env=%s)", settings.app_name, settings.version, settings.environment)
    # Two shared clients, reused across requests for connection pooling. The probe
    # client has a tight timeout (a slow /ready should fail fast); the LLM client
    # has a long one because generating on a model takes many seconds.
    app.state.http_client = httpx.AsyncClient(timeout=2.0)
    app.state.llm_client = httpx.AsyncClient(timeout=settings.llm_request_timeout)
    # Transcribing long media is far slower than a chat completion, so its client
    # gets its own, much larger timeout.
    app.state.stt_client = httpx.AsyncClient(timeout=settings.stt_request_timeout)
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await app.state.llm_client.aclose()
        await app.state.stt_client.aclose()
        logger.info("Stopping %s", settings.app_name)


#: Shown at the top of /docs. The interactive documentation is the first thing a
#: reviewer, a mentor or a partner LMS team opens, and an endpoint list with no
#: explanation makes thirty-three routes look like thirty-three unrelated features
#: rather than one pipeline with four entry points into it.
API_DESCRIPTION = """
Turns a **document or a recording** into teaching material an LMS can open:
a lesson, a quiz, subtitles and an interactive video — packaged as **H5P**,
**SCORM 1.2** or a **standalone HTML file**.

### How the pipeline fits together

```
a document ─┐
a recording ─┼─→  understand  ─→  teach   ─→  check   ─→  package
typed notes ─┘    (insights)      (lesson)    (quiz)      (H5P · SCORM · HTML)
```

Every module below is one step of that, and each is callable on its own.
**`/course/…` runs all of them at once**, which is what most callers want.

### Two rules that shape every response here

**Nothing is invented.** Every question is grounded in a passage of the source and
carries the reference back to it. Where a target format cannot express something,
the engine says so in `warnings` rather than approximating it silently.

**Structure is computed, never generated.** How many steps a lesson has, and where a
recording's chapters fall, are decided in Python from the source itself. The model
only writes the words inside a structure it was given — so the same input produces
the same shape every time, which is what makes any of this testable.

### Reading a course response

A `200` from `/course/…` does **not** mean every stage succeeded. It means the build
ran and reported. Check `stages`: each one is `produced`, `skipped` (you did not ask)
or `failed` (you asked and it could not be done, with the reason). A document that
supports no groundable question still returns its lesson.
"""

#: One line per module, shown above each group in /docs. Ordered as the pipeline
#: runs rather than alphabetically, so the page reads top to bottom as the flow.
API_TAGS = [
    {"name": "course",
     "description": "**Start here.** One upload, one finished course — every module "
                    "below run in order, with a report on each. Returns either the "
                    "course as data, or one archive ready to hand to an LMS."},
    {"name": "ingestion",
     "description": "Parse a file into structured pages and blocks. Seven formats: "
                    "PDF, PPTX, DOCX, CSV, HTML, Markdown and plain text."},
    {"name": "summarization",
     "description": "A summary, a glossary and an outline of a parsed document."},
    {"name": "narration",
     "description": "A narration script a teacher could read aloud. Text only — this "
                    "produces no audio."},
    {"name": "assessment",
     "description": "Questions grounded in the source, and their packages. Four types: "
                    "multiple choice, fill in the blank, match the pairs, and short "
                    "answer marked against a scheme built at generation time."},
    {"name": "transcription",
     "description": "Speech to a timed transcript, with WebVTT and SRT subtitles."},
    {"name": "chaptering",
     "description": "Split a recording at its own natural pauses. The model titles the "
                    "chapters; it never decides where they fall."},
    {"name": "interactive video",
     "description": "A recording as an H5P Interactive Video, with a knowledge check at "
                    "the end of each chapter."},
    {"name": "micro-lesson",
     "description": "A short structured lesson, and its three packaged forms: H5P Course "
                    "Presentation, a self-contained HTML deck, and SCORM 1.2."},
    {"name": "system",
     "description": "Liveness, readiness and service metadata."},
]


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        summary="Local-first, LMS-agnostic AI engine: documents and media into portable micro-learning.",
        description=API_DESCRIPTION,
        openapi_tags=API_TAGS,
        lifespan=lifespan,
    )
    # Populated on startup; these guards keep calls before lifespan runs from crashing.
    app.state.http_client = None
    app.state.llm_client = None
    app.state.stt_client = None

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

    @app.exception_handler(EmptyTranscriptError)
    async def _on_empty_transcript(request: Request, exc: EmptyTranscriptError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(EmptyLessonError)
    async def _on_empty_lesson(request: Request, exc: EmptyLessonError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(EmptyAssessmentError)
    async def _on_empty_assessment(request: Request, exc: EmptyAssessmentError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(LLMUnavailable)
    async def _on_llm_unavailable(request: Request, exc: LLMUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(LLMTimeout)
    async def _on_llm_timeout(request: Request, exc: LLMTimeout) -> JSONResponse:
        return JSONResponse(status_code=504, content={"detail": str(exc)})

    # The STT gateway fails the same way the model gateway does, mapped the same way.
    @app.exception_handler(STTUnavailable)
    async def _on_stt_unavailable(request: Request, exc: STTUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(STTTimeout)
    async def _on_stt_timeout(request: Request, exc: STTTimeout) -> JSONResponse:
        return JSONResponse(status_code=504, content={"detail": str(exc)})

    app.include_router(ingestion_router)
    app.include_router(summarization_router)
    app.include_router(narration_router)
    app.include_router(assessment_router)
    app.include_router(transcription_router)
    app.include_router(chaptering_router)
    app.include_router(interactive_video_router)
    app.include_router(microlesson_router)
    app.include_router(course_router)
    return app


app = create_app()
