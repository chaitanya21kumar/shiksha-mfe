"""The Module C.2 contract: a transcript divided into titled, timed chapters.

Like every other generative artifact in the engine, `ChapteredTranscript` is its
own model rather than an extension of `Transcript` — it carries its own
provenance and `warnings`, and the transcript it came from stays untouched.

Each chapter records the exact span it covers, the transcript segments inside it,
and the text of those segments. Carrying the text costs a little size and buys
two things: a chapter is self-contained for the next stage (knowledge-check
generation reads it without re-joining segments), and a consumer never has to
re-derive what a chapter actually said.

The times are what an H5P Interactive Video turns into bookmarks — the chapter
markers in the player's navigation bar — so they must be real positions in the
media, not indices.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from ..transcription.schema import TranscriptSource

#: The chaptering pipeline never produces more than this, and the emitter renders
#: one navigation-bar mark per chapter. Bounding it in the contract as well is what
#: keeps a hand-built request body from packaging a player that has to draw
#: thousands of marks — a JSON body has no equivalent of the upload ceiling.
MAX_CHAPTERS = 24
#: Long enough for a real heading, short enough for the bookmark menu.
MAX_TITLE_CHARS = 120


class Chapter(BaseModel):
    """One titled span of the recording."""

    index: int = Field(ge=1, description="1-based position of this chapter.")
    start: float = Field(ge=0, description="Start time in seconds from the media start.")
    end: float = Field(ge=0, description="End time in seconds from the media start.")
    title: str = Field(
        max_length=MAX_TITLE_CHARS, description="A short heading for this chapter."
    )
    segment_indexes: list[int] = Field(
        default_factory=list,
        description="The 1-based TranscriptSegment indexes this chapter covers.",
    )
    text: str = Field(
        default="", description="The transcript text of this chapter, joined in reading order."
    )

    @model_validator(mode="after")
    def _end_after_start(self) -> Chapter:
        # A chapter whose end precedes its start would produce a bookmark the
        # player cannot seek to, and a negative duration downstream.
        if self.end < self.start:
            raise ValueError(f"chapter end ({self.end}) precedes start ({self.start})")
        return self

    @property
    def duration(self) -> float:
        """How long this chapter runs, in seconds."""
        return self.end - self.start


class ChapteredTranscript(BaseModel):
    """Everything Module C.2 derives from one transcript."""

    schema_version: str = "1.0"
    source: TranscriptSource
    generator: str = Field(description='What produced the titles, e.g. "groq".')
    model: str = Field(description="The model that generated the titles.")
    generated_at: datetime
    language: str | None = Field(default=None, description="Spoken language (ISO-639-1).")
    chapters: list[Chapter] = Field(default_factory=list, max_length=MAX_CHAPTERS)
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues, e.g. a chapter the model did not title.",
    )

    @model_validator(mode="after")
    def _chapters_are_ordered_and_contiguous(self) -> ChapteredTranscript:
        """Chapters must run forward in time and never overlap.

        Overlapping bookmarks make a player seek to the wrong place, and an
        out-of-order list silently reorders the navigation bar. Both are the kind
        of fault that looks fine in JSON and is wrong in front of a learner, so
        the contract refuses them outright.
        """
        previous_end: float | None = None
        for position, chapter in enumerate(self.chapters, start=1):
            if chapter.index != position:
                raise ValueError(
                    f"chapter indexes must run 1..n in order; got {chapter.index} at position {position}"
                )
            if previous_end is not None and chapter.start < previous_end:
                raise ValueError(
                    f"chapter {chapter.index} starts at {chapter.start}, before the previous "
                    f"chapter ended at {previous_end}"
                )
            previous_end = chapter.end
        return self
