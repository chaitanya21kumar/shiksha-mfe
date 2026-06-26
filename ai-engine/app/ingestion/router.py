"""HTTP surface for document ingestion.

`POST /ingest` accepts an uploaded PDF or PPTX and returns the structured
`ParsedDocument`. The transport layer stays thin: all the real work — picking a
parser, streaming to disk, parsing in a worker thread — lives in
`service.parse_upload`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, UploadFile

from .schema import ParsedDocument
from .service import parse_upload

router = APIRouter(tags=["ingestion"])


@router.post(
    "/ingest",
    responses={
        400: {"description": "The file matched a supported type but could not be parsed"},
        415: {"description": "Unsupported file type"},
    },
)
async def ingest(file: Annotated[UploadFile, File()]) -> ParsedDocument:
    """Parse an uploaded PDF or PPTX into a structured document."""
    return await parse_upload(file)
