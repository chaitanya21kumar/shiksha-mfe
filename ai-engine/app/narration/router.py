"""HTTP surface for the narration layer (Module A.3).

- ``POST /narrate`` takes a `ParsedDocument` (the output of ``/ingest``) and
  returns a `NarrationScript`.
- ``POST /narrate/file`` takes a raw upload and does parse + narrate in one call.

Like the summarisation router, the endpoints stay thin: the pipeline raises the
shared domain errors (empty document, gateway unavailable, timeout) and the
app-level exception handlers map those to the documented HTTP responses. The
model client and the generation config are shared with the summarisation layer,
so both generative modules talk to the gateway the same way.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, UploadFile

from ..config import settings
from ..ingestion.schema import ParsedDocument
from ..ingestion.service import parse_upload
from ..summarization.pipeline import GenerationConfig
from ..summarization.router import get_llm_client
from .pipeline import generate_narration
from .schema import NarrationScript

router = APIRouter(tags=["narration"])

_ERROR_RESPONSES = {
    400: {"description": "The document has no text to narrate"},
    503: {"description": "The model gateway is unreachable, rejected the key, or is rate-limited"},
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


@router.post("/narrate", responses=_ERROR_RESPONSES)
async def narrate(
    document: ParsedDocument,
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
) -> NarrationScript:
    """Derive a spoken narration script from an already-parsed document."""
    return await generate_narration(client, document, _generation_config())


@router.post(
    "/narrate/file",
    responses={**_ERROR_RESPONSES, 415: {"description": "Unsupported file type"}},
)
async def narrate_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
) -> NarrationScript:
    """Parse an uploaded document and derive a narration script in one call."""
    document = await parse_upload(file)
    return await generate_narration(client, document, _generation_config())
