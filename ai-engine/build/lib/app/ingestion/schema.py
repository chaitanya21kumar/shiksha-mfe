"""Structured representation of a parsed document — the Module A contract.

Every parser (PDF, PPT, …) produces a `ParsedDocument`, and every downstream
module (summaries, glossary, assessments, lessons) consumes one. Keeping this
shape stable and source-agnostic is what lets the rest of the engine stay
simple: nothing downstream needs to know or care whether the original file was
a PDF or a PowerPoint.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class BlockKind(str, Enum):
    """The kind of content a `Block` holds."""

    heading = "heading"
    paragraph = "paragraph"
    list = "list"
    table = "table"
    image = "image"


class ImageRef(BaseModel):
    """An image found on a page or slide.

    The image bytes are written to a file separately; here we keep a stable
    reference plus whatever metadata the source exposes.
    """

    id: str
    caption: str | None = None
    width: int | None = None
    height: int | None = None
    path: str | None = Field(
        default=None, description="Where the extracted image file was written, if extracted."
    )


class Block(BaseModel):
    """One piece of content on a page/slide, kept in reading order.

    Only the fields relevant to `kind` are populated (e.g. `items` for a list,
    `image` for an image); the rest stay `None`.
    """

    kind: BlockKind
    text: str | None = None
    level: int | None = Field(
        default=None, description="Heading level, 1 = most prominent. Heading blocks only."
    )
    items: list[str] | None = Field(default=None, description="List items. List blocks only.")
    rows: list[list[str]] | None = Field(
        default=None, description="Table cells, row by row. Table blocks only."
    )
    image: ImageRef | None = None


class Page(BaseModel):
    """One unit of a document.

    A ``page`` (PDF), a ``slide`` (PPT), a ``sheet`` (CSV / spreadsheet), or a
    ``document`` — a single logical unit for flow formats (DOCX, TXT, Markdown,
    HTML) that have no native pagination.
    """

    index: int = Field(description="1-based position in the document.")
    kind: Literal["page", "slide", "sheet", "document"]
    blocks: list[Block] = Field(default_factory=list)
    notes: str | None = Field(default=None, description="Speaker notes (PPT slides).")


class SourceInfo(BaseModel):
    """Metadata about the original file."""

    filename: str
    format: Literal["pdf", "pptx", "docx", "csv", "txt", "md", "html"]
    page_count: int = Field(
        description="Number of pages, slides or sheets; 1 for flow documents (DOCX, TXT, Markdown, HTML)."
    )
    title: str | None = None
    author: str | None = None
    created: datetime | None = None
    modified: datetime | None = None


class ParsedDocument(BaseModel):
    """The full structured output of parsing one document."""

    schema_version: str = "1.0"
    source: SourceInfo
    parser: str = Field(description='Tool that produced this, e.g. "pymupdf".')
    parser_version: str
    parsed_at: datetime
    pages: list[Page] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list, description="Non-fatal issues encountered while parsing."
    )
