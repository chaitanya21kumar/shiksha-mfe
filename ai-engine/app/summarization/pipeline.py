"""Turn a parsed document into `DocumentInsights` using the configured model.

The flow is deliberately simple and composable: flatten the structured document
back into readable source text, then run three focused generations (summary +
takeaways, glossary, outline). The generations are independent, so a single one
that comes back unusable is recorded as a warning and the rest still succeed.
Connectivity and timeout failures, by contrast, fail the whole request fast —
there is no point asking three times when the gateway is unreachable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..ingestion.schema import Block, BlockKind, ParsedDocument
from . import prompts
from .llm_client import LLMBadResponse, chat_json
from .schema import DocumentInsights, GlossaryTerm, InsightsSource, OutlineSection

logger = logging.getLogger("ai_engine.summarization")


class EmptyDocumentError(Exception):
    """The document has no extractable text, so there is nothing to summarise."""


@dataclass(frozen=True)
class GenerationConfig:
    """Everything the pipeline needs to talk to the model gateway."""

    base_url: str
    api_key: str
    model: str
    provider: str
    temperature: float
    max_source_chars: int


# Internal response shapes. Each generation is validated against one of these
# before we trust it; the prompt states the same shape to the model.
class _SummaryResponse(BaseModel):
    summary: str = ""
    key_takeaways: list[str] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _coerce_summary(cls, value: object) -> object:
        # Some models return the summary as a list of sentences rather than one
        # string; join it instead of losing the whole section to a type error.
        if value is None:
            return ""
        if isinstance(value, list):
            # Coercing means the model ignored the requested shape; log it so
            # repeated drift is visible rather than silently absorbed.
            logger.warning(
                "Model returned 'summary' as a list of %d items; joining into one string.",
                len(value),
            )
            return " ".join(str(item).strip() for item in value if str(item).strip())
        return value

    @field_validator("key_takeaways", mode="before")
    @classmethod
    def _coerce_takeaways(cls, value: object) -> object:
        # Tolerate a single string where a list of points was asked for.
        if isinstance(value, str):
            logger.warning("Model returned 'key_takeaways' as a string; wrapping it in a list.")
            return [value] if value.strip() else []
        return value


class _GlossaryResponse(BaseModel):
    glossary: list[GlossaryTerm] = Field(default_factory=list)


class _OutlineResponse(BaseModel):
    outline: list[OutlineSection] = Field(default_factory=list)


def _block_to_text(block: Block) -> str | None:
    """Render one block as plain text, or None if it carries no text."""
    if block.kind in (BlockKind.heading, BlockKind.paragraph):
        return block.text
    if block.kind == BlockKind.list and block.items:
        return "\n".join(f"- {item}" for item in block.items)
    if block.kind == BlockKind.table and block.rows:
        return "\n".join(" | ".join(row) for row in block.rows)
    return None


def flatten_document(doc: ParsedDocument) -> str:
    """Flatten a parsed document into readable source text for the model."""
    parts: list[str] = []
    for page in doc.pages:
        for block in page.blocks:
            text = _block_to_text(block)
            if text:
                parts.append(text)
        if page.notes:
            parts.append(page.notes)
    return "\n".join(parts).strip()


T = TypeVar("T", bound=BaseModel)


async def _generate_section(
    client: httpx.AsyncClient,
    config: GenerationConfig,
    *,
    user: str,
    response_model: type[T],
    label: str,
    warnings: list[str],
) -> T | None:
    """Run one generation and validate it, or record a warning and return None.

    Only *unusable output* (bad status, non-JSON, schema mismatch) is downgraded
    to a warning here; connectivity and timeout errors propagate so the caller
    can fail the whole request.
    """
    try:
        raw = await chat_json(
            client,
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            system=prompts.SYSTEM,
            user=user,
            temperature=config.temperature,
        )
        return response_model.model_validate(raw)
    except (LLMBadResponse, ValidationError) as exc:
        # Degrade this section to a warning, but log it too: a section silently
        # missing from every response is a signal worth seeing in the logs.
        logger.warning("Could not generate %s: %s", label, exc)
        warnings.append(f"Could not generate {label}: {exc}")
        return None


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


async def generate_insights(
    client: httpx.AsyncClient, doc: ParsedDocument, config: GenerationConfig
) -> DocumentInsights:
    """Derive a summary, glossary, and outline from a parsed document."""
    source_text = flatten_document(doc)
    if not source_text:
        raise EmptyDocumentError("The document contains no extractable text to summarise.")

    warnings: list[str] = []
    source_text, truncated = _truncate(source_text, config.max_source_chars)
    if truncated:
        warnings.append(
            f"Source text was truncated to {config.max_source_chars} characters before summarising."
        )

    summary = await _generate_section(
        client, config,
        user=prompts.summary_prompt(source_text),
        response_model=_SummaryResponse, label="summary", warnings=warnings,
    )
    glossary = await _generate_section(
        client, config,
        user=prompts.glossary_prompt(source_text),
        response_model=_GlossaryResponse, label="glossary", warnings=warnings,
    )
    outline = await _generate_section(
        client, config,
        user=prompts.outline_prompt(source_text),
        response_model=_OutlineResponse, label="outline", warnings=warnings,
    )

    return DocumentInsights(
        source=InsightsSource(
            filename=doc.source.filename,
            title=doc.source.title,
            page_count=doc.source.page_count,
        ),
        generator=config.provider,
        model=config.model,
        generated_at=datetime.now(timezone.utc),
        summary=summary.summary if summary else "",
        key_takeaways=summary.key_takeaways if summary else [],
        glossary=glossary.glossary if glossary else [],
        outline=outline.outline if outline else [],
        warnings=warnings,
    )


def generation_config() -> GenerationConfig:
    """The generation settings every router hands to the pipelines.

    One definition rather than one per router: five identical copies is five places
    a new setting has to be remembered, and four of them will be missed.
    """
    from ..config import settings

    return GenerationConfig(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        provider=settings.llm_provider,
        temperature=settings.llm_temperature,
        max_source_chars=settings.max_source_chars,
    )
