"""The Module A.3 contract: a spoken narration script derived from a document.

Like `DocumentInsights`, `NarrationScript` is a separate, generative model — not
an extension of `ParsedDocument`. It carries its own provenance (which model,
when) and its own `warnings`, and each segment records a word count and a rough
spoken-duration estimate so a later module (interactive video, text-to-speech)
can plan timing without re-deriving it. Each segment also points back to the
`Page.index` it narrates, so narration can be aligned to the original slide.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NarrationSegment(BaseModel):
    """One speakable unit of narration, aligned to a slide or section."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(description="1-based position of this segment in the narration.")
    source_index: int | None = Field(
        default=None,
        description="The 1-based Page.index this narrates, for aligning narration with the source.",
    )
    title: str | None = Field(default=None, description="The slide or section title, if any.")
    script: str = Field(description="The spoken narration for this segment.")
    word_count: int = Field(default=0, description="Number of words in the script.")
    estimated_seconds: float = Field(
        default=0.0, description="Rough spoken duration at about 150 words per minute."
    )


class NarrationSource(BaseModel):
    """A pointer back to the document this narration was derived from."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    title: str | None = None
    page_count: int = Field(description="Pages, slides or sheets in the source.")


class NarrationScript(BaseModel):
    """Everything Module A.3 derives from one parsed document."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    source: NarrationSource
    generator: str = Field(description='What produced the narration, e.g. "groq".')
    model: str = Field(description='The model that generated it, e.g. "openai/gpt-oss-20b".')
    generated_at: datetime
    segments: list[NarrationSegment] = Field(default_factory=list)
    total_words: int = Field(default=0, description="Total words across all segments.")
    estimated_seconds: float = Field(
        default=0.0, description="Total rough spoken duration across all segments."
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues, e.g. a section the model did not narrate.",
    )
