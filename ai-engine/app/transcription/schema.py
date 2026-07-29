"""The Module C.1 contract: a time-aligned transcript of an audio or video file.

Like `DocumentInsights` and `NarrationScript`, `Transcript` is its own generative
model — not an extension of `ParsedDocument`. It carries its own provenance (which
model, when) and its own `warnings`, and each segment records a start and end time
in seconds, so the transcript can be rendered as WebVTT or SRT subtitles and,
later, aligned to auto-generated chapter markers and interactive-video knowledge
checks (Module C.2–C.3).

A per-segment `speaker` field is present but left unset: speaker diarisation is
deliberately deferred (ADR-0007), and carrying the field now means enabling it
later needs no contract migration — exactly as `weight` was reserved on the
short-answer key points.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class TranscriptSegment(BaseModel):
    """One timed cue: when it is spoken, and what is said."""

    index: int = Field(description="1-based position of this cue in the transcript.")
    start: float = Field(ge=0, description="Start time in seconds from the media start.")
    end: float = Field(ge=0, description="End time in seconds from the media start.")
    text: str = Field(description="The transcribed text of this cue.")
    speaker: str | None = Field(
        default=None,
        description="Speaker label. Unset until diarisation is enabled (ADR-0007).",
    )

    @model_validator(mode="after")
    def _end_after_start(self) -> TranscriptSegment:
        # A cue whose end precedes its start would produce a negative-length
        # subtitle that some players reject outright, so pin the ordering here.
        if self.end < self.start:
            raise ValueError(f"segment end ({self.end}) precedes start ({self.start})")
        return self


class TranscriptSource(BaseModel):
    """A pointer back to the media this transcript was derived from."""

    filename: str
    media_seconds: float | None = Field(
        default=None, description="Media duration in seconds, if the provider reported it."
    )


class Transcript(BaseModel):
    """Everything Module C.1 derives from one audio or video file."""

    schema_version: str = "1.0"
    source: TranscriptSource
    generator: str = Field(description='What produced the transcript, e.g. "groq".')
    model: str = Field(description='The STT model, e.g. "whisper-large-v3".')
    generated_at: datetime
    language: str | None = Field(
        default=None, description="Detected or supplied spoken language (ISO-639-1)."
    )
    segments: list[TranscriptSegment] = Field(default_factory=list)
    full_text: str = Field(default="", description="The whole transcript as plain text.")
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues, e.g. no speech detected in the media.",
    )
