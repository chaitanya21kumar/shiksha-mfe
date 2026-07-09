"""HTTP surface for the assessment layer (Module B).

- ``POST /assess`` takes a `ParsedDocument` (the output of ``/ingest``) and
  returns an `AssessmentSet`.
- ``POST /assess/file`` takes a raw upload and does parse + generate in one call.

Both accept ``question_types`` (any of mcq, match, fill_blank; defaults to all
three), ``count`` (questions per type), and ``language`` (BCP-47 tag). Like the
other generative routers the endpoints stay thin: the pipeline raises the shared
domain errors (empty document, gateway unavailable, timeout) and the app-level
exception handlers map those to the documented HTTP responses. The model client
and generation config are shared with the summarisation layer.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, Query, UploadFile

from ..config import settings
from ..ingestion.schema import ParsedDocument
from ..ingestion.service import parse_upload
from ..summarization.pipeline import GenerationConfig
from ..summarization.router import get_llm_client
from .pipeline import ALL_TYPES, QuestionType, generate_assessment
from .schema import AssessmentSet

router = APIRouter(tags=["assessment"])

_ERROR_RESPONSES = {
    400: {"description": "The document has no text to build questions from"},
    503: {"description": "The model gateway is unreachable, rejected the key, or is rate-limited"},
    504: {"description": "Generation timed out"},
}

_TYPES_QUERY = Query(
    description="Question types to generate (any of mcq, match, fill_blank). Defaults to all three."
)
_COUNT_QUERY = Query(ge=1, le=20, description="Questions to generate per requested type.")
_LANGUAGE_QUERY = Query(description="BCP-47 language tag of the source (e.g. en, hi).")


def _generation_config() -> GenerationConfig:
    return GenerationConfig(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        provider=settings.llm_provider,
        temperature=settings.llm_temperature,
        max_source_chars=settings.max_source_chars,
    )


def _resolve_types(question_types: list[QuestionType] | None) -> list[QuestionType]:
    """Default to every type, and drop duplicates while keeping request order."""
    return list(dict.fromkeys(question_types or ALL_TYPES))


@router.post("/assess", responses=_ERROR_RESPONSES)
async def assess(
    document: ParsedDocument,
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    question_types: Annotated[list[QuestionType] | None, _TYPES_QUERY] = None,
    count: Annotated[int, _COUNT_QUERY] = 5,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> AssessmentSet:
    """Generate a source-grounded assessment from an already-parsed document."""
    return await generate_assessment(
        client,
        document,
        _generation_config(),
        question_types=_resolve_types(question_types),
        count=count,
        language=language,
    )


@router.post(
    "/assess/file",
    responses={**_ERROR_RESPONSES, 413: {"description": "File too large"}, 415: {"description": "Unsupported file type"}},
)
async def assess_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
    question_types: Annotated[list[QuestionType] | None, _TYPES_QUERY] = None,
    count: Annotated[int, _COUNT_QUERY] = 5,
    language: Annotated[str, _LANGUAGE_QUERY] = "en",
) -> AssessmentSet:
    """Parse an uploaded document and generate an assessment in one call."""
    document = await parse_upload(file)
    return await generate_assessment(
        client,
        document,
        _generation_config(),
        question_types=_resolve_types(question_types),
        count=count,
        language=language,
    )
