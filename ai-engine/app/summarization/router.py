"""HTTP surface for the summarisation layer (Module A.2).

Two ways in, same output:

- ``POST /summarize`` takes a `ParsedDocument` (the output of ``/ingest``) and
  returns its `DocumentInsights`. Composable: ingest once, enrich many times.
- ``POST /summarize/file`` takes a raw file upload (any supported document
  format) and does both steps in one call — convenient for quick checks and live
  demos.

The endpoints stay thin: the pipeline raises domain errors (empty document,
gateway unavailable, timeout) and app-level exception handlers map those to the
documented HTTP responses. The model client comes from a dependency so it can be
swapped for a fake in tests.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ..ingestion.schema import ParsedDocument
from ..ingestion.service import parse_upload
from .pipeline import generation_config, generate_insights
from .schema import DocumentInsights

router = APIRouter(tags=["summarization"])

_ERROR_RESPONSES = {
    400: {"description": "The document has no text to summarise"},
    503: {"description": "The model gateway is unreachable, rejected the key, or is rate-limited"},
    504: {"description": "Generation timed out"},
}


def get_llm_client(request: Request) -> httpx.AsyncClient:
    """The shared, long-timeout httpx client used for generation."""
    client: httpx.AsyncClient | None = request.app.state.llm_client
    if client is None:  # lifespan has not run (or already shut down)
        raise HTTPException(status_code=503, detail="Model client is not initialised.")
    return client


@router.post("/summarize", responses=_ERROR_RESPONSES)
async def summarize(
    document: ParsedDocument,
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
) -> DocumentInsights:
    """Derive insights from an already-parsed document."""
    return await generate_insights(client, document, generation_config())


@router.post("/summarize/file", responses={**_ERROR_RESPONSES, 415: {"description": "Unsupported file type"}})
async def summarize_file(
    file: Annotated[UploadFile, File()],
    client: Annotated[httpx.AsyncClient, Depends(get_llm_client)],
) -> DocumentInsights:
    """Parse an uploaded document and derive insights in one call."""
    document = await parse_upload(file)
    return await generate_insights(client, document, generation_config())
