"""HTTP surface for the transcription layer (Module C.1).

``POST /transcribe`` takes an audio or video upload and returns a `Transcript`.
The ``format`` query parameter picks the representation: ``json`` (default, the
structured transcript), ``vtt`` (a WebVTT subtitle file) or ``srt`` — the two
caption formats an LMS or an H5P Interactive Video imports.

The endpoint stays thin: the service raises 413/415 for the upload and the
pipeline raises the shared gateway errors, which the app-level exception handlers
map to 503/504. The STT client comes from a dependency so it can be swapped for a
fake in tests.
"""

from __future__ import annotations

from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse

from ..config import settings
from .emit import to_srt, to_webvtt
from .pipeline import TranscriptionConfig
from .schema import Transcript
from .service import transcribe_upload

router = APIRouter(tags=["transcription"])

_ERROR_RESPONSES = {
    413: {"description": "The media file is larger than the configured ceiling"},
    415: {"description": "Unsupported media type"},
    503: {"description": "The STT gateway is unreachable, rejected the key, or is rate-limited"},
    504: {"description": "Transcription timed out"},
}


def get_stt_client(request: Request) -> httpx.AsyncClient:
    """The shared, long-timeout httpx client used for transcription."""
    client: httpx.AsyncClient | None = request.app.state.stt_client
    if client is None:  # lifespan has not run (or already shut down)
        raise HTTPException(status_code=503, detail="STT client is not initialised.")
    return client


def _transcription_config() -> TranscriptionConfig:
    return TranscriptionConfig(
        base_url=settings.stt_base_url,
        api_key=settings.stt_api_key,
        model=settings.stt_model,
        provider=settings.stt_provider,
        language=settings.stt_language,
    )


@router.post("/transcribe", responses=_ERROR_RESPONSES)
async def transcribe_endpoint(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_stt_client)],
    # Aliased so the query key stays ?format= while the parameter avoids shadowing
    # the built-in ``format``.
    output_format: Annotated[Literal["json", "vtt", "srt"], Query(alias="format")] = "json",
):
    """Transcribe an uploaded media file into a transcript or a subtitle file."""
    transcript: Transcript = await transcribe_upload(file, _transcription_config(), client)
    if output_format == "vtt":
        return PlainTextResponse(to_webvtt(transcript), media_type="text/vtt")
    if output_format == "srt":
        return PlainTextResponse(to_srt(transcript), media_type="application/x-subrip")
    return transcript
