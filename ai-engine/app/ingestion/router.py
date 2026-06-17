"""HTTP surface for document ingestion.

`POST /ingest` accepts an uploaded PDF or PPTX and returns the structured
`ParsedDocument`. The transport layer stays thin: it picks the right parser by
file extension and hands off — all real work lives in the parsers. The upload is
streamed to a temp file and parsed in a worker thread, so a large or slow file
never blocks the event loop.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Callable

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from .pdf_parser import parse_pdf
from .pptx_parser import parse_pptx
from .schema import ParsedDocument

router = APIRouter(tags=["ingestion"])

_PARSERS: dict[str, Callable[[str], ParsedDocument]] = {
    ".pdf": parse_pdf,
    ".pptx": parse_pptx,
}


def _parse_upload(
    parser: Callable[[str], ParsedDocument], upload: UploadFile, suffix: str
) -> ParsedDocument:
    """Stream the upload to a temp file and parse it. Runs in a worker thread.

    Streaming (rather than reading the whole file into memory) keeps memory
    bounded; ``delete=False`` plus an explicit ``unlink`` avoids the Windows
    file-lock issue of re-opening a still-open ``NamedTemporaryFile`` by name.
    """
    upload.file.seek(0)
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        shutil.copyfileobj(upload.file, tmp)
        tmp.close()
        return parser(tmp.name)
    finally:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)


@router.post(
    "/ingest",
    responses={
        400: {"description": "The file matched a supported type but could not be parsed"},
        415: {"description": "Unsupported file type"},
    },
)
async def ingest(file: Annotated[UploadFile, File()]) -> ParsedDocument:
    """Parse an uploaded PDF or PPTX into a structured document."""
    suffix = Path(file.filename or "").suffix.lower()
    parser = _PARSERS.get(suffix)
    if parser is None:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix or 'unknown'}'. Supported: {', '.join(_PARSERS)}.",
        )

    try:
        document = await run_in_threadpool(_parse_upload, parser, file, suffix)
    except Exception as exc:  # known type, but the bytes could not be parsed
        raise HTTPException(
            status_code=400, detail=f"Could not parse the uploaded file: {exc}"
        ) from exc

    # Report the real uploaded filename, not the temporary one.
    document.source.filename = file.filename or document.source.filename
    return document
