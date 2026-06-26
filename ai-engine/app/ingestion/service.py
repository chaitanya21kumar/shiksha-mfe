"""Shared ingestion logic: turn an uploaded file into a `ParsedDocument`.

Both the ingestion endpoint (`POST /ingest`) and the summarisation file
endpoint need the same thing — pick a parser by extension, stream the upload to
disk, parse it in a worker thread, and surface clean HTTP errors for unsupported
or unparseable files. That shared behaviour lives here so the two routers stay
thin and can never drift apart.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable

from fastapi import HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from .pdf_parser import parse_pdf
from .pptx_parser import parse_pptx
from .schema import ParsedDocument

Parser = Callable[[str], ParsedDocument]

PARSERS: dict[str, Parser] = {
    ".pdf": parse_pdf,
    ".pptx": parse_pptx,
}


def _parse_to_tempfile(parser: Parser, upload: UploadFile, suffix: str) -> ParsedDocument:
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


async def parse_upload(file: UploadFile) -> ParsedDocument:
    """Parse an uploaded PDF or PPTX, or raise the right HTTP error.

    415 if the extension is unsupported; 400 if it is a known type whose bytes
    could not be parsed. The transport layer stays thin: callers just await this.
    """
    suffix = Path(file.filename or "").suffix.lower()
    parser = PARSERS.get(suffix)
    if parser is None:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix or 'unknown'}'. Supported: {', '.join(PARSERS)}.",
        )

    try:
        document = await run_in_threadpool(_parse_to_tempfile, parser, file, suffix)
    except Exception as exc:  # known type, but the bytes could not be parsed
        raise HTTPException(
            status_code=400, detail=f"Could not parse the uploaded file: {exc}"
        ) from exc

    # Report the real uploaded filename, not the temporary one.
    document.source.filename = file.filename or document.source.filename
    return document
