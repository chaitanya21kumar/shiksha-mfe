"""Parse a Word (.docx) file into a `ParsedDocument`.

Word documents carry real structure in their styles, so headings are read from
the paragraph style ("Heading 1" → level 1, "Title" → level 1), consecutive
list-styled paragraphs are grouped into list blocks, tables become table blocks,
and inline images become image blocks. Body content is walked in document order.
A .docx is flow text, so it is one ``document`` page.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import docx
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .schema import Block, BlockKind, ImageRef, Page, ParsedDocument, SourceInfo

_PARSER = "python-docx"


def _parser_version() -> str:
    return getattr(docx, "__version__", "unknown")


def _iter_block_items(document) -> Iterator[Paragraph | Table]:
    """Yield each paragraph and table in document order."""
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _heading_level(para: Paragraph) -> int | None:
    name = para.style.name or ""
    if name.startswith("Heading "):
        try:
            return min(int(name.split()[1]), 6)
        except ValueError:
            return 1
    if name == "Title":
        return 1
    if name == "Subtitle":
        return 2
    return None


def _is_list_item(para: Paragraph) -> bool:
    if "list" in (para.style.name or "").lower():
        return True
    p_pr = para._p.find(qn("w:pPr"))
    return p_pr is not None and p_pr.find(qn("w:numPr")) is not None


def _image_count(para: Paragraph) -> int:
    return sum(1 for _ in para._p.iter(qn("w:drawing")))


def parse_docx(path: str | Path) -> ParsedDocument:
    """Read a .docx file and return its structured representation."""
    document = docx.Document(str(path))
    blocks: list[Block] = []
    list_buffer: list[str] = []
    image_index = 0

    def flush_list() -> None:
        nonlocal list_buffer
        if list_buffer:
            blocks.append(Block(kind=BlockKind.list, items=list_buffer))
            list_buffer = []

    for item in _iter_block_items(document):
        if isinstance(item, Table):
            flush_list()
            rows = [[cell.text.strip() for cell in row.cells] for row in item.rows]
            if rows:
                blocks.append(Block(kind=BlockKind.table, rows=rows))
            continue

        text = item.text.strip()
        level = _heading_level(item)
        if level is not None and text:
            flush_list()
            blocks.append(Block(kind=BlockKind.heading, text=text, level=level))
        elif _is_list_item(item) and text:
            list_buffer.append(text)
        elif text:
            flush_list()
            blocks.append(Block(kind=BlockKind.paragraph, text=text))

        for _ in range(_image_count(item)):
            flush_list()
            image_index += 1
            blocks.append(Block(kind=BlockKind.image, image=ImageRef(id=f"img{image_index}")))
    flush_list()

    props = document.core_properties
    warnings = [] if blocks else ["The document contained no readable text."]
    return ParsedDocument(
        source=SourceInfo(
            filename=Path(path).name,
            format="docx",
            page_count=1,
            title=props.title or None,
            author=props.author or None,
            created=props.created,
            modified=props.modified,
        ),
        parser=_PARSER,
        parser_version=_parser_version(),
        parsed_at=datetime.now(timezone.utc),
        pages=[Page(index=1, kind="document", blocks=blocks)],
        warnings=warnings,
    )
