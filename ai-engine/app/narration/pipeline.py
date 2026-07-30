"""Turn a parsed document into a spoken `NarrationScript` using the model.

The document is split — deterministically, in Python — into narration sections:
one per slide for a slide deck, and one per top-level heading (falling back to
one per page) for flowing documents, with any speaker notes folded in. The model
is then asked, in a single call, to write a spoken script for each numbered
section. Scripts are matched back by index, and any section the model skips is
recorded as a warning rather than failing the whole request.

Doing the segmentation ourselves (rather than leaving it to the model) keeps the
number and order of segments deterministic and testable, and lets each segment
point back to the `Page` it narrates so a later module can align narration with
the original slides. Connectivity and timeout failures propagate so the request
fails fast; unusable model output degrades to an empty narration with a warning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..ingestion.schema import Block, BlockKind, Page, ParsedDocument
from ..summarization.llm_client import LLMBadResponse, chat_json_for
from ..summarization.pipeline import EmptyDocumentError, GenerationConfig
from . import prompts
from .schema import NarrationScript, NarrationSegment, NarrationSource

logger = logging.getLogger("ai_engine.narration")

# A common spoken-narration pace is ~150 words per minute.
_WORDS_PER_SECOND = 150 / 60
# Bound the single model call so a very large document cannot produce an
# unbounded prompt: cap the number of sections narrated.
_MAX_SECTIONS = 40


@dataclass(frozen=True)
class _Section:
    """One narration unit: where it came from, its title, and its text."""

    source_index: int
    title: str | None
    text: str


class _Segment(BaseModel):
    """One script the model returned, keyed by the section number it was given."""

    index: int
    script: str = ""

    @field_validator("script", mode="before")
    @classmethod
    def _coerce_script(cls, value: object) -> object:
        # Some models return the script as a list of sentences; join it rather
        # than lose the segment to a type error.
        if value is None:
            return ""
        if isinstance(value, list):
            return " ".join(str(item).strip() for item in value if str(item).strip())
        return value


class _NarrationResponse(BaseModel):
    segments: list[_Segment] = Field(default_factory=list)


def _block_text(block: Block) -> str | None:
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


def _whole_page_section(page: Page) -> _Section | None:
    """Render an entire page as one narration section (used for slides).

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
        text = _block_text(block)
        if text:
            parts.append(text)
    if page.notes and page.notes.strip():
        parts.append(page.notes.strip())
    text = "\n".join(parts).strip()
    if not text and not title:
        return None
    return _Section(source_index=page.index, title=title, text=text)


def _split_by_heading(page: Page) -> list[_Section]:
    """Split a flowing page into sections at its most prominent heading level."""
    split_level = _page_heading_level(page)
    sections: list[_Section] = []
    title: str | None = None
    parts: list[str] = []

    def flush() -> None:
        nonlocal title, parts
        text = "\n".join(parts).strip()
        if text or title:
            sections.append(_Section(source_index=page.index, title=title, text=text))
        title, parts = None, []

    for block in page.blocks:
        if split_level is not None and block.kind is BlockKind.heading and block.level == split_level:
            flush()
            title = (block.text or "").strip() or None
            continue
        text = _block_text(block)
        if text:
            parts.append(text)
    if page.notes and page.notes.strip():
        parts.append(page.notes.strip())
    flush()
    return sections


def _split_page(page: Page) -> list[_Section]:
    """Split one page into narration sections.

    A slide is always a single section — one script per slide — with its first
    heading as the title, so a multi-heading slide is never split. A flowing page
    (page / document / sheet) is split at its most prominent heading level,
    falling back to a single section. Speaker notes are folded in either way.
    """
    if page.kind == "slide":
        section = _whole_page_section(page)
        return [section] if section else []
    return _split_by_heading(page)


def _build_sections(doc: ParsedDocument) -> list[_Section]:
    """Split a whole document into narration sections, in reading order."""
    sections: list[_Section] = []
    for page in doc.pages:
        sections.extend(_split_page(page))
    return sections


def _bounded(
    sections: list[_Section], max_sections: int, max_chars: int
) -> tuple[list[_Section], list[str]]:
    """Cap the sections (count and total characters) so the prompt stays bounded."""
    warnings: list[str] = []
    if len(sections) > max_sections:
        warnings.append(f"Document had {len(sections)} sections; narrated the first {max_sections}.")
        sections = sections[:max_sections]

    bounded: list[_Section] = []
    used = 0
    truncated = False
    for sec in sections:
        remaining = max_chars - used
        if remaining <= 0:
            truncated = True
            break
        text = sec.text if len(sec.text) <= remaining else sec.text[:remaining]
        truncated = truncated or len(text) < len(sec.text)
        bounded.append(_Section(sec.source_index, sec.title, text))
        used += len(text)
    if truncated:
        warnings.append(f"Source text was truncated to about {max_chars} characters before narrating.")
    return bounded, warnings


async def _generate(
    client: httpx.AsyncClient,
    config: GenerationConfig,
    numbered: list[tuple[int, str | None, str]],
    warnings: list[str],
) -> dict[int, str]:
    """Run the single narration call and return ``{section number: script}``.

    Connectivity and timeout errors propagate (the caller fails the request);
    unusable output (bad status, non-JSON, schema mismatch) degrades to an empty
    result with a warning, so the endpoint still returns a well-formed response.
    """
    try:
        raw = await chat_json_for(
                client, config, system=prompts.SYSTEM, user=prompts.narration_prompt(numbered)
            )
        parsed = _NarrationResponse.model_validate(raw)
    except (LLMBadResponse, ValidationError) as exc:
        logger.warning("Could not generate narration: %s", exc)
        warnings.append(f"Could not generate narration: {exc}")
        return {}
    return {seg.index: seg.script for seg in parsed.segments}


def _estimated_seconds(word_count: int) -> float:
    return round(word_count / _WORDS_PER_SECOND, 1)


async def generate_narration(
    client: httpx.AsyncClient, doc: ParsedDocument, config: GenerationConfig
) -> NarrationScript:
    """Derive a spoken narration script from a parsed document."""
    sections = _build_sections(doc)
    if not sections:
        raise EmptyDocumentError("The document contains no narratable text.")

    sections, warnings = _bounded(sections, _MAX_SECTIONS, config.max_source_chars)
    if not sections:
        raise EmptyDocumentError("The document has no narratable text within the size limit.")
    numbered = [(i, sec.title, sec.text) for i, sec in enumerate(sections, start=1)]
    scripts = await _generate(client, config, numbered, warnings)

    segments: list[NarrationSegment] = []
    for i, sec in enumerate(sections, start=1):
        script = (scripts.get(i) or "").strip()
        if not script:
            label = f" ({sec.title})" if sec.title else ""
            warnings.append(f"No narration was produced for section {i}{label}.")
            continue
        words = len(script.split())
        segments.append(
            NarrationSegment(
                index=len(segments) + 1,
                source_index=sec.source_index,
                title=sec.title,
                script=script,
                word_count=words,
                estimated_seconds=_estimated_seconds(words),
            )
        )

    total_words = sum(seg.word_count for seg in segments)
    return NarrationScript(
        source=NarrationSource(
            filename=doc.source.filename,
            title=doc.source.title,
            page_count=doc.source.page_count,
        ),
        generator=config.provider,
        model=config.model,
        generated_at=datetime.now(timezone.utc),
        segments=segments,
        total_words=total_words,
        estimated_seconds=_estimated_seconds(total_words),
        warnings=warnings,
    )
