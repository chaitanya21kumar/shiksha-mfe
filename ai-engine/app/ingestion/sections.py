"""Splitting a parsed document into teachable units, deterministically.

A *section* is one unit of source a later module can generate against and point
back to: one slide, or one heading's worth of a flowing page. Narration turns each
into a spoken script; the micro-lesson builder turns each into a lesson step. Both
want the same split, and the split is a property of the document rather than of
either consumer, so it lives here.

This is deliberately Python rather than a prompt. A model asked to divide a
document returns a different number of pieces on each run, which makes the output
of every downstream module non-reproducible and untestable. The same reasoning
governs chapter boundaries in ADR-0008 and question placement in ADR-0009: the
structure is computed, and the model is only ever asked for words.

Assessment keeps its own page-level fold, and that is not duplication: it needs one
section *per page* so a question can be attributed to the page its evidence came
from, whereas these two want one section per teachable idea. Different questions,
different splits.
"""

from __future__ import annotations

from dataclasses import dataclass

from .schema import Block, BlockKind, Page, ParsedDocument


@dataclass(frozen=True)
class Section:
    """One unit of source: where it came from, its title, and its text."""

    source_index: int
    title: str | None
    text: str


def block_text(block: Block) -> str | None:
    """Render one block as plain text, or None if it carries no text."""
    if block.kind in (BlockKind.heading, BlockKind.paragraph):
        return block.text
    if block.kind is BlockKind.list and block.items:
        return "\n".join(f"- {item}" for item in block.items)
    if block.kind is BlockKind.table and block.rows:
        return "\n".join(" | ".join(row) for row in block.rows)
    return None


def _page_heading_level(page: Page) -> int | None:
    """The most prominent heading level on a page, or None if it has no headings."""
    levels = [b.level for b in page.blocks if b.kind is BlockKind.heading and b.level is not None]
    return min(levels) if levels else None


def _whole_page_section(page: Page) -> Section | None:
    """Render an entire page as one section (used for slides).

    The first heading becomes the title and is left out of the body; everything
    else, plus any speaker notes, becomes the text. Returns None if the page
    carries no title and no text.
    """
    title: str | None = None
    title_taken = False
    parts: list[str] = []
    for block in page.blocks:
        if not title_taken and block.kind is BlockKind.heading:
            title = (block.text or "").strip() or None
            title_taken = True
            continue
        text = block_text(block)
        if text:
            parts.append(text)
    if page.notes and page.notes.strip():
        parts.append(page.notes.strip())
    text = "\n".join(parts).strip()
    if not text and not title:
        return None
    return Section(source_index=page.index, title=title, text=text)


def _split_by_heading(page: Page) -> list[Section]:
    """Split a flowing page into sections at its most prominent heading level."""
    split_level = _page_heading_level(page)
    sections: list[Section] = []
    title: str | None = None
    parts: list[str] = []

    def flush() -> None:
        nonlocal title, parts
        text = "\n".join(parts).strip()
        if text or title:
            sections.append(Section(source_index=page.index, title=title, text=text))
        title, parts = None, []

    for block in page.blocks:
        if (
            split_level is not None
            and block.kind is BlockKind.heading
            and block.level == split_level
        ):
            flush()
            title = (block.text or "").strip() or None
            continue
        text = block_text(block)
        if text:
            parts.append(text)
    if page.notes and page.notes.strip():
        parts.append(page.notes.strip())
    flush()
    return sections


def split_page(page: Page) -> list[Section]:
    """Split one page into sections.

    A slide is always a single section, with its first heading as the title, so a
    multi-heading slide is never split — a slide is already the author's own unit
    of one idea. A flowing page (page / document / sheet) is split at its most
    prominent heading level, falling back to a single section. Speaker notes are
    folded in either way.
    """
    if page.kind == "slide":
        section = _whole_page_section(page)
        return [section] if section else []
    return _split_by_heading(page)


def sections_from_document(doc: ParsedDocument) -> list[Section]:
    """Split a whole document into sections, in reading order."""
    sections: list[Section] = []
    for page in doc.pages:
        sections.extend(split_page(page))
    return sections


def bounded(
    sections: list[Section], max_sections: int, max_chars: int, verb: str = "used"
) -> tuple[list[Section], list[str]]:
    """Cap the sections, by count and by total characters, so a prompt stays bounded.

    `verb` only shapes the warning text, so each caller can say what it was about
    to do with them ("narrated", "used") without a second copy of the logic.
    """
    warnings: list[str] = []
    if len(sections) > max_sections:
        warnings.append(f"Document had {len(sections)} sections; {verb} the first {max_sections}.")
        sections = sections[:max_sections]

    kept: list[Section] = []
    used = 0
    truncated = False
    for section in sections:
        remaining = max_chars - used
        if remaining <= 0:
            truncated = True
            break
        text = section.text if len(section.text) <= remaining else section.text[:remaining]
        truncated = truncated or len(text) < len(section.text)
        kept.append(Section(section.source_index, section.title, text))
        used += len(text)
    if truncated:
        warnings.append(f"Source text was truncated to about {max_chars} characters.")
    return kept, warnings
