"""Parse a PDF into a `ParsedDocument` (Module A.1).

PDFs carry almost no semantic structure — there are no real "heading" tags,
just text drawn at certain positions and sizes. So headings are detected
heuristically: the most common font size on a page is treated as body text,
and noticeably larger text is treated as a heading (larger = higher level).
That is enough to give downstream modules a usable outline; genuine
uncertainty is recorded in `ParsedDocument.warnings` rather than hidden.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pymupdf

from .schema import Block, BlockKind, ImageRef, Page, ParsedDocument, SourceInfo

_PARSER = "pymupdf"
_BULLET_PREFIXES = ("•", "‣", "·", "-", "–", "*")
_MAX_HEADING_WORDS = 20


def _parser_version() -> str:
    return getattr(pymupdf, "__version__", "unknown")


def _parse_pdf_date(value: str | None) -> datetime | None:
    """PDF dates look like ``D:20240115093000+05'30'``. Best-effort; None on failure."""
    if not value:
        return None
    digits = value.lstrip("D:")[:14]
    try:
        return datetime.strptime(digits, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _iter_text_spans(page_dicts: list[dict]) -> Iterator[dict]:
    """Yield every text span across all pages (text blocks have ``type`` 0)."""
    for data in page_dicts:
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                yield from line.get("spans", [])


def _document_body_size(page_dicts: list[dict]) -> float:
    """Body text size for the whole document.

    Computed once across all pages and weighted by how much text is set at each
    size, so the most-typeset size wins. Document-wide (not per-page) because
    body text is a property of the document — a single sparse page must not
    skew the estimate, which would misread its heading as body text.
    """
    weights: Counter[float] = Counter()
    for span in _iter_text_spans(page_dicts):
        text = span.get("text", "").strip()
        if text:
            weights[round(span.get("size", 0.0), 1)] += len(text)
    return weights.most_common(1)[0][0] if weights else 0.0


def _heading_level(size: float, body: float) -> int | None:
    """Heading level from how much larger than body the text is, else None."""
    if body <= 0:
        return None
    ratio = size / body
    if ratio >= 1.5:
        return 1
    if ratio >= 1.2:
        return 2
    return None


def _looks_like_list(lines: list[str]) -> bool:
    stripped = [ln.strip() for ln in lines if ln.strip()]
    if len(stripped) < 2:
        return False
    marked = sum(1 for ln in stripped if _is_list_marker(ln))
    return marked >= max(2, len(stripped) // 2)


def _is_list_marker(line: str) -> bool:
    if line[:1] in _BULLET_PREFIXES:
        return True
    first = line.split(" ", 1)[0].rstrip(".)")
    return first.isdigit()


def _strip_marker(line: str) -> str:
    s = line.strip()
    if s[:1] in _BULLET_PREFIXES:
        return s[1:].strip()
    head, _, rest = s.partition(" ")
    if head.rstrip(".)").isdigit():
        return rest.strip()
    return s


def _text_block_entry(block: dict, body: float) -> tuple[float, Block] | None:
    """Classify one text block as a heading, list, or paragraph (or skip it)."""
    lines = [
        "".join(span.get("text", "") for span in line.get("spans", []))
        for line in block.get("lines", [])
    ]
    text = "\n".join(lines).strip()
    if not text:
        return None

    y0 = block["bbox"][1]
    max_size = max(
        (span.get("size", body) for line in block.get("lines", []) for span in line.get("spans", [])),
        default=body,
    )
    level = _heading_level(max_size, body)
    if level is not None and len(text.split()) <= _MAX_HEADING_WORDS:
        return y0, Block(kind=BlockKind.heading, text=" ".join(text.split()), level=level)
    if _looks_like_list(lines):
        items = [_strip_marker(ln) for ln in lines if ln.strip()]
        return y0, Block(kind=BlockKind.list, items=[i for i in items if i])
    return y0, Block(kind=BlockKind.paragraph, text=" ".join(text.split()))


def _image_entries(page: "pymupdf.Page", page_index: int) -> list[tuple[float, Block]]:
    """Image blocks for a page, keyed by vertical position for reading order."""
    entries: list[tuple[float, Block]] = []
    for img_index, info in enumerate(page.get_image_info(xrefs=True), start=1):
        bbox = info.get("bbox") or (0, 0, 0, 0)
        entries.append((
            bbox[1],
            Block(
                kind=BlockKind.image,
                image=ImageRef(
                    id=f"p{page_index}-img{img_index}",
                    width=info.get("width"),
                    height=info.get("height"),
                ),
            ),
        ))
    return entries


def _build_page(data: dict, page: "pymupdf.Page", page_index: int, body: float) -> Page:
    """Assemble one page's blocks in reading order (top to bottom)."""
    entries: list[tuple[float, Block]] = []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        entry = _text_block_entry(block, body)
        if entry is not None:
            entries.append(entry)
    entries.extend(_image_entries(page, page_index))
    entries.sort(key=lambda item: item[0])
    return Page(index=page_index, kind="page", blocks=[block for _, block in entries])


def parse_pdf(path: str | Path) -> ParsedDocument:
    """Read a PDF and return its structured representation."""
    path = Path(path)
    doc = pymupdf.open(path)
    try:
        meta = doc.metadata or {}
        page_dicts = [page.get_text("dict") for page in doc]
        body = _document_body_size(page_dicts)
        pages = [
            _build_page(data, doc[index], index + 1, body)
            for index, data in enumerate(page_dicts)
        ]
        source = SourceInfo(
            filename=path.name,
            format="pdf",
            page_count=doc.page_count,
            title=meta.get("title") or None,
            author=meta.get("author") or None,
            created=_parse_pdf_date(meta.get("creationDate")),
            modified=_parse_pdf_date(meta.get("modDate")),
        )
        return ParsedDocument(
            source=source,
            parser=_PARSER,
            parser_version=_parser_version(),
            parsed_at=datetime.now(timezone.utc),
            pages=pages,
            warnings=[],
        )
    finally:
        doc.close()
