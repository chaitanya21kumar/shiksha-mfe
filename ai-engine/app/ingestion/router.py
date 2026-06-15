"""HTTP surface for document ingestion.

`POST /ingest` accepts an uploaded PDF or PPTX and returns the structured
`ParsedDocument`. The transport layer is kept thin: it only picks the right
parser by file extension and hands off — all real work lives in the parsers.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from .pdf_parser import parse_pdf
from .pptx_parser import parse_pptx
from .schema import ParsedDocument

router = APIRouter(tags=["ingestion"])

_PARSERS = {
    ".pdf": parse_pdf,
    ".pptx": parse_pptx,
}


@router.post("/ingest", response_model=ParsedDocument)
async def ingest(file: UploadFile = File(...)) -> ParsedDocument:
    """Parse an uploaded PDF or PPTX into a structured document."""
    suffix = Path(file.filename or "").suffix.lower()
    parser = _PARSERS.get(suffix)
    if parser is None:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix or 'unknown'}'. Supported: {', '.join(_PARSERS)}.",
        )

    data = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(data)
        tmp.flush()
        document = parser(tmp.name)

    # Report the real uploaded filename, not the temporary one.
    document.source.filename = file.filename or document.source.filename
    return document
