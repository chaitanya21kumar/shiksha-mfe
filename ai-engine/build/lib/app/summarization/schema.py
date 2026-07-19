"""The Module A.2 contract: learner-facing insights derived from a document.

`DocumentInsights` is deliberately a *separate* model from `ParsedDocument`, not
an extension of it. A parsed document is what the source literally contains —
deterministic and parser-produced. Insights are *derived* and *generative*:
produced by a language model, non-deterministic, and carrying their own
provenance (which model, when) and their own `warnings`. Keeping the two
contracts apart lets `ParsedDocument` stay frozen while generative capabilities
grow on top of it, and lets a single failed section degrade gracefully instead
of failing the whole request.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GlossaryTerm(BaseModel):
    """A term drawn from the source, with a plain-language definition."""

    term: str
    definition: str = Field(description="A short, learner-friendly definition.")


class OutlineSection(BaseModel):
    """One section of a suggested course outline."""

    title: str
    points: list[str] = Field(
        default_factory=list,
        description="The key points a learner should cover in this section.",
    )


class InsightsSource(BaseModel):
    """A pointer back to the document these insights were derived from."""

    filename: str
    title: str | None = None
    page_count: int = Field(description="Pages (PDF) or slides (PPT) in the source.")


class DocumentInsights(BaseModel):
    """Everything Module A.2 derives from one parsed document."""

    schema_version: str = "1.0"
    source: InsightsSource
    generator: str = Field(description='What produced these insights, e.g. "ollama".')
    model: str = Field(description="The model that generated them, e.g. \"llama3.2:3b\".")
    generated_at: datetime
    summary: str = Field(default="", description="A short abstract of the document.")
    key_takeaways: list[str] = Field(
        default_factory=list, description="The most important points, as concise bullets."
    )
    glossary: list[GlossaryTerm] = Field(default_factory=list)
    outline: list[OutlineSection] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues, e.g. a section the model could not produce.",
    )
