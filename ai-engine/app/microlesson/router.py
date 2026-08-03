"""HTTP surface for the micro-lesson builder (Module D, week 9).

Four endpoints, one per way of arriving at a lesson. Issue #7 asks for a lesson
"generated from a document, a transcript, or free-form input", so there is an
endpoint for each rather than one overloaded route with a mode flag — a caller
uploading a PDF and a caller pasting notes want different request shapes, and
collapsing them would mean a body where half the fields are always unused.

The routes stay thin, as the other modules' do: the pipeline raises the shared
domain errors and the app-level handlers map them to documented responses.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Body, Depends, File, Query, UploadFile

from ..chaptering.schema import ChapteredTranscript
from ..ingestion.schema import ParsedDocument
from ..ingestion.service import parse_upload
from ..summarization.pipeline import generation_config
from ..summarization.router import get_llm_client
from .pipeline import lesson_from_document, lesson_from_text, lesson_from_transcript
from .schema import MicroLesson

router = APIRouter(tags=["micro-lesson"])

_ERRORS = {
    400: {"description": "The source has nothing long enough to build a lesson from"},
    503: {"description": "The model gateway is unreachable, rejected the key, or is rate-limited"},
    504: {"description": "Generation timed out"},
}

_TITLE_QUERY = Query(
    description="The lesson's title. Defaults to the source's own title or filename; "
    "it is never generated, so a caller who names their lesson gets that name back."
)
_LANGUAGE_QUERY = Query(description="BCP-47 tag for the lesson and its future package.")


@router.post("/micro-lesson", responses=_ERRORS)
async def micro_lesson_from_document(
    document: ParsedDocument,
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> MicroLesson:
    """Build a lesson from an already-parsed document."""
    return await lesson_from_document(
        client, document, generation_config(), title=title, language=language
    )


@router.post(
    "/micro-lesson/file",
    responses={**_ERRORS, 413: {"description": "File too large"}, 415: {"description": "Unsupported file type"}},
)
async def micro_lesson_from_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> MicroLesson:
    """Parse an upload and build a lesson from it in one call."""
    document = await parse_upload(file)
    return await lesson_from_document(
        client, document, generation_config(), title=title, language=language
    )


@router.post("/micro-lesson/transcript", responses=_ERRORS)
async def micro_lesson_from_transcript(
    chaptered: Annotated[ChapteredTranscript, Body()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> MicroLesson:
    """Build a lesson from a chaptered recording.

    One step per chapter, so the lesson inherits a structure that came from where
    the speaker actually paused rather than from anyone's guess.
    """
    return await lesson_from_transcript(
        client, chaptered, generation_config(), title=title, language=language
    )


@router.post("/micro-lesson/text", responses=_ERRORS)
async def micro_lesson_from_text(
    text: Annotated[str, Body(media_type="text/plain")],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    title: Annotated[str | None, _TITLE_QUERY] = None,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> MicroLesson:
    """Build a lesson from pasted text, split at its blank lines."""
    return await lesson_from_text(
        client, text, generation_config(), title=title, language=language
    )
