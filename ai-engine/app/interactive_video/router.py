"""HTTP surface for the interactive video layer (Module C.3).

- ``POST /interactive-video`` takes a `ChapteredTranscript` plus the URL the media
  is served from, and returns an importable ``.h5p``.
- ``POST /interactive-video/file`` takes a media upload and runs the whole chain —
  transcribe, chapter, generate a knowledge check per chapter, package.

The media is referenced by URL rather than bundled (ADR-0009), so the upload here
is what gets *transcribed*; ``video_url`` is where the learner's LMS will stream it
from. They are usually the same recording in two places, and the endpoint asks for
both rather than guessing one from the other.

Warnings ride in the ``X-Package-Warnings`` header, as they do for the other
packaging endpoints, because the body is a file.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError

from ..assessment.pipeline import ALL_TYPES, QuestionType
from ..chaptering.pipeline import generate_chapters
from ..chaptering.schema import ChapteredTranscript
from ..config import settings
from ..summarization.pipeline import GenerationConfig
from ..summarization.router import get_llm_client
from ..transcription.pipeline import TranscriptionConfig
from ..transcription.router import get_stt_client
from ..transcription.service import transcribe_upload
from .emit import emit_interactive_video
from .pipeline import build_interactive_video
from .schema import VideoSource

router = APIRouter(tags=["interactive video"])

#: Interactive Video's own whitelist has no room for H5P.Essay, so short answers
#: are not offered here — asking for them would only produce warnings.
_EMBEDDABLE_TYPES: tuple[QuestionType, ...] = tuple(
    t for t in ALL_TYPES if t != "short_answer"
)

_ERROR_RESPONSES = {
    400: {"description": "The transcript has no speech to build a video from"},
    413: {"description": "The media file is larger than the configured ceiling"},
    415: {"description": "Unsupported media type"},
    503: {"description": "A model gateway is unreachable, rejected the key, or is rate-limited"},
    504: {"description": "Generation timed out"},
}

_VIDEO_URL_QUERY = Query(
    description="Public http(s) URL the LMS will stream the media from. The package "
    "references the media rather than embedding it."
)
_TYPES_QUERY = Query(
    description="Question types for the knowledge checks (any of mcq, match, fill_blank). "
    "short_answer is not offered: Interactive Video does not accept H5P.Essay."
)


def _generation_config() -> GenerationConfig:
    return GenerationConfig(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        provider=settings.llm_provider,
        temperature=settings.llm_temperature,
        max_source_chars=settings.max_source_chars,
    )


def _transcription_config() -> TranscriptionConfig:
    return TranscriptionConfig(
        base_url=settings.stt_base_url,
        api_key=settings.stt_api_key,
        model=settings.stt_model,
        provider=settings.stt_provider,
        language=settings.stt_language,
    )


def _resolve_types(requested: list[QuestionType] | None) -> list[QuestionType]:
    chosen = list(dict.fromkeys(requested or _EMBEDDABLE_TYPES))
    return [t for t in chosen if t in _EMBEDDABLE_TYPES] or list(_EMBEDDABLE_TYPES)


def _video_source(url: str) -> VideoSource:
    """Build the video source, turning a bad URL into a 400 rather than a 500.

    ``video_url`` arrives as a plain query string, so its validation lives in the
    contract. Letting that ValidationError escape would make an unusable-but-clear
    caller mistake look like a server fault.
    """
    try:
        return VideoSource(url=url)
    except ValidationError as invalid:
        raise HTTPException(status_code=400, detail=str(invalid.errors()[0]["msg"])) from invalid


def _as_response(package) -> Response:
    return Response(
        content=package.content,
        media_type="application/zip",
        headers={
            "content-disposition": f'attachment; filename="{package.filename}"',
            "x-package-warning-count": str(len(package.warnings)),
            **({"x-package-warnings": " | ".join(package.warnings)[:900]} if package.warnings else {}),
        },
    )


@router.post("/interactive-video", responses=_ERROR_RESPONSES)
async def interactive_video(
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    chaptered: Annotated[ChapteredTranscript, Body()],
    video_url: Annotated[str, _VIDEO_URL_QUERY],
    title: Annotated[str, Query(description="Shown on the video's start screen.")] = "Interactive video",
    question_types: Annotated[list[QuestionType] | None, _TYPES_QUERY] = None,
    count: Annotated[int, Query(ge=1, le=5, description="Questions per type, per chapter.")] = 1,
    language: Annotated[str, Query(description="BCP-47 tag for the package.")] = "en",
) -> Response:
    """Build an interactive video from an already-chaptered transcript."""
    spec = await build_interactive_video(
        client,
        chaptered,
        _video_source(video_url),
        _generation_config(),
        content_id=chaptered.source.filename,
        title=title,
        question_types=_resolve_types(question_types),
        count=count,
        language=language,
    )
    return _as_response(emit_interactive_video(spec))


@router.post("/interactive-video/file", responses=_ERROR_RESPONSES)
async def interactive_video_file(
    file: Annotated[UploadFile, File()],
    stt_client: Annotated[httpx.AsyncClient, Depends(get_stt_client)],
    llm_client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    video_url: Annotated[str, _VIDEO_URL_QUERY],
    title: Annotated[str, Query(description="Shown on the video's start screen.")] = "Interactive video",
    question_types: Annotated[list[QuestionType] | None, _TYPES_QUERY] = None,
    count: Annotated[int, Query(ge=1, le=5, description="Questions per type, per chapter.")] = 1,
    language: Annotated[str, Query(description="BCP-47 tag for the package.")] = "en",
) -> Response:
    """Transcribe, chapter, question and package a recording in one call."""
    transcript = await transcribe_upload(file, _transcription_config(), stt_client)
    chaptered = await generate_chapters(llm_client, transcript, _generation_config())
    spec = await build_interactive_video(
        llm_client,
        chaptered,
        _video_source(video_url),
        _generation_config(),
        content_id=file.filename or "interactive-video",
        title=title,
        question_types=_resolve_types(question_types),
        count=count,
        language=language,
    )
    return _as_response(emit_interactive_video(spec))
