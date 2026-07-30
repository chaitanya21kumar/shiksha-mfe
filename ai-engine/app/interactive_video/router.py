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
packaging endpoints, because the body is a file — through the same shared helper,
so the JSON-escaping that keeps a non-English warning from becoming a 500 cannot
be forgotten in one router and remembered in another.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError

from ..assessment.pipeline import ALL_TYPES, QuestionType
from ..chaptering.pipeline import generate_chapters
from ..chaptering.schema import ChapteredTranscript
from ..packaging.response import ZIP_MEDIA_TYPE, package_response
from ..summarization.pipeline import generation_config
from ..summarization.router import get_llm_client
from ..transcription.pipeline import transcription_config
from ..transcription.router import get_stt_client
from ..transcription.service import transcribe_upload
from .emit import emit_interactive_video
from .pipeline import VideoBuildOptions, build_interactive_video
from .schema import VideoSource

router = APIRouter(tags=["interactive video"])

#: Interactive Video's own whitelist has no room for H5P.Essay, so short answers
#: are not offered here — asking for them would only produce warnings.
_EMBEDDABLE_TYPES: tuple[QuestionType, ...] = tuple(t for t in ALL_TYPES if t != "short_answer")

_ERROR_RESPONSES = {
    400: {"description": "No speech to build a video from, or an unusable parameter"},
    413: {"description": "The media file is larger than the configured ceiling"},
    415: {"description": "Unsupported media type"},
    503: {"description": "A model gateway is unreachable, rejected the key, or is rate-limited"},
    504: {"description": "Generation timed out"},
}
_ZIP_RESPONSE = {
    200: {
        "content": {ZIP_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        "description": "The interactive video package",
    },
    **_ERROR_RESPONSES,
}

_VIDEO_URL_QUERY = Query(
    description="Public http(s) URL the LMS will stream the media from. The package "
    "references the media rather than embedding it."
)
_TYPES_QUERY = Query(
    description="Question types for the knowledge checks (any of mcq, match, fill_blank). "
    "short_answer is not accepted: Interactive Video does not allow H5P.Essay."
)
_TITLE_QUERY = Query(description="Shown on the video's start screen.")
_COUNT_QUERY = Query(ge=1, le=5, description="Questions per type, per chapter.")
_LANGUAGE_QUERY = Query(description="BCP-47 tag for the package.")


def _resolve_types(requested: list[QuestionType] | None) -> list[QuestionType]:
    """Pick the types to generate, refusing a request this format cannot honour.

    A caller who asks only for ``short_answer`` used to be handed the three other
    types instead. Silently generating something nobody asked for is worse than
    saying no, so an unhonourable request is a 400 that names the reason.
    """
    if requested is None:
        return list(_EMBEDDABLE_TYPES)
    chosen = [t for t in dict.fromkeys(requested) if t in _EMBEDDABLE_TYPES]
    if not chosen:
        raise HTTPException(
            status_code=400,
            detail="short_answer cannot be embedded in an interactive video: H5P.Essay is "
            "not on H5P.InteractiveVideo's interaction whitelist and would be stripped "
            "at import. Ask for any of mcq, match, fill_blank.",
        )
    return chosen


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


def _options(
    content_id: str,
    title: str,
    question_types: list[QuestionType] | None,
    count: int,
    language: str,
) -> VideoBuildOptions:
    return VideoBuildOptions(
        content_id=content_id,
        title=title,
        question_types=_resolve_types(question_types),
        count=count,
        language=language,
    )


@router.post("/interactive-video", response_class=Response, responses=_ZIP_RESPONSE)
async def interactive_video(
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    chaptered: Annotated[ChapteredTranscript, Body()],
    video_url: Annotated[str, _VIDEO_URL_QUERY],
    title: Annotated[str, _TITLE_QUERY] = "Interactive video",
    question_types: Annotated[list[QuestionType] | None, _TYPES_QUERY] = None,
    count: Annotated[int, _COUNT_QUERY] = 1,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> Response:
    """Build an interactive video from an already-chaptered transcript."""
    spec = await build_interactive_video(
        client,
        chaptered,
        _video_source(video_url),
        generation_config(),
        _options(chaptered.source.filename, title, question_types, count, language),
    )
    return package_response(emit_interactive_video(spec))


@router.post("/interactive-video/file", response_class=Response, responses=_ZIP_RESPONSE)
async def interactive_video_file(
    file: Annotated[UploadFile, File()],
    stt_client: Annotated[httpx.AsyncClient, Depends(get_stt_client)],
    llm_client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    video_url: Annotated[str, _VIDEO_URL_QUERY],
    title: Annotated[str, _TITLE_QUERY] = "Interactive video",
    question_types: Annotated[list[QuestionType] | None, _TYPES_QUERY] = None,
    count: Annotated[int, _COUNT_QUERY] = 1,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> Response:
    """Transcribe, chapter, question and package a recording in one call."""
    options = _options(file.filename or "interactive-video", title, question_types, count, language)
    source = _video_source(video_url)
    transcript = await transcribe_upload(file, transcription_config(), stt_client)
    chaptered = await generate_chapters(llm_client, transcript, generation_config())
    spec = await build_interactive_video(
        llm_client, chaptered, source, generation_config(), options
    )
    return package_response(emit_interactive_video(spec))
