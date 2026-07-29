"""HTTP surface for the chaptering layer (Module C.2).

- ``POST /chapter`` takes a `Transcript` (the output of ``/transcribe``) and
  returns it divided into titled chapters.
- ``POST /chapter/file`` takes a media upload and does transcribe + chapter in one
  call, which is the convenient path for a demo.

The endpoints stay thin: the pipelines raise the shared domain errors and the
app-level exception handlers map them to the documented responses. Both model
clients come from dependencies so they can be swapped for fakes in tests — the
file endpoint needs the speech-to-text client as well as the generation one,
because it runs both stages.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, UploadFile

from ..config import settings
from ..summarization.pipeline import GenerationConfig
from ..summarization.router import get_llm_client
from ..transcription.pipeline import TranscriptionConfig
from ..transcription.router import get_stt_client
from ..transcription.schema import Transcript
from ..transcription.service import transcribe_upload
from .pipeline import generate_chapters
from .schema import ChapteredTranscript

router = APIRouter(tags=["chaptering"])

_ERROR_RESPONSES = {
    400: {"description": "The transcript has no timed speech to divide into chapters"},
    503: {"description": "A model gateway is unreachable, rejected the key, or is rate-limited"},
    504: {"description": "Generation timed out"},
}


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


@router.post("/chapter", responses=_ERROR_RESPONSES)
async def chapter(
    transcript: Transcript,
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
) -> ChapteredTranscript:
    """Divide an already-transcribed recording into titled chapters."""
    return await generate_chapters(client, transcript, _generation_config())


@router.post(
    "/chapter/file",
    responses={
        **_ERROR_RESPONSES,
        413: {"description": "The media file is larger than the configured ceiling"},
        415: {"description": "Unsupported media type"},
    },
)
async def chapter_file(
    file: Annotated[UploadFile, File()],
    stt_client: Annotated[httpx.AsyncClient, Depends(get_stt_client)],
    llm_client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
) -> ChapteredTranscript:
    """Transcribe an uploaded recording and divide it into chapters in one call."""
    transcript = await transcribe_upload(file, _transcription_config(), stt_client)
    return await generate_chapters(llm_client, transcript, _generation_config())
