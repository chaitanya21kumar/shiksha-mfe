"""Turn a `Transcript` into a `ChapteredTranscript`.

The division is done in Python and the model only writes the titles. That split
is deliberate and is the same one narration uses: the number of chapters, where
they begin and end, and which segments they contain are all deterministic, so
they can be tested exactly, while the part that genuinely needs language — a
readable heading — is the only thing left to the model.

Boundaries are chosen by looking for a real pause. Speech has natural gaps
between segments, and a chapter that starts just after one lines up with how a
person would actually divide the recording. Once a chapter has reached the target
length the next sufficiently long pause ends it; if no pause arrives, an overshoot
ceiling ends it anyway so one long unbroken stretch cannot swallow the recording.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..summarization.llm_client import LLMBadResponse, chat_json
from ..summarization.pipeline import GenerationConfig
from ..transcription.schema import Transcript, TranscriptSegment
from . import prompts
from .schema import Chapter, ChapteredTranscript

logger = logging.getLogger("ai_engine.chaptering")

#: Aim for chapters of roughly this length. Short enough to be a useful jump
#: target in a player's navigation bar, long enough to be a real topic.
_TARGET_SECONDS = 90.0
#: A gap between segments at least this long counts as a natural pause and is
#: allowed to end a chapter once the target has been reached.
_PAUSE_SECONDS = 0.6
#: If no pause arrives, end the chapter anyway at this multiple of the target, so
#: continuous speech cannot produce one enormous chapter.
_OVERSHOOT = 1.6
#: A trailing chapter shorter than this is folded into the one before it rather
#: than left as a stub in the navigation bar.
_MIN_TAIL_SECONDS = 25.0
#: Upper bound on chapters, which also bounds the single titling call.
_MAX_CHAPTERS = 24


class EmptyTranscriptError(Exception):
    """The transcript has no timed speech to divide into chapters."""


@dataclass(frozen=True)
class _Span:
    """A run of consecutive transcript segments that will become one chapter."""

    segments: list[TranscriptSegment]

    @property
    def start(self) -> float:
        return self.segments[0].start

    @property
    def end(self) -> float:
        return self.segments[-1].end

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def text(self) -> str:
        return " ".join(segment.text.strip() for segment in self.segments if segment.text.strip())


class _TitledChapter(BaseModel):
    """One title the model returned, keyed by the chapter number it was given."""

    index: int
    title: str = ""

    @field_validator("title", mode="before")
    @classmethod
    def _coerce_title(cls, value: object) -> object:
        # Some models return a title as a list of words, or as null; take what is
        # usable rather than losing the chapter to a type error.
        if value is None:
            return ""
        if isinstance(value, list):
            return " ".join(str(part).strip() for part in value if str(part).strip())
        return value


class _TitleResponse(BaseModel):
    chapters: list[_TitledChapter] = Field(default_factory=list)


def _target_seconds(total: float) -> float:
    """The chapter length to aim for, raised if needed to stay under the cap.

    Deriving the target from the total length means a very long recording gets
    proportionally longer chapters instead of hundreds of them — the cap is
    honoured by construction rather than by trimming afterwards.
    """
    if total <= 0:
        return _TARGET_SECONDS
    return max(_TARGET_SECONDS, total / _MAX_CHAPTERS)


def _split_into_spans(segments: list[TranscriptSegment], target: float) -> list[_Span]:
    """Group consecutive segments into spans, breaking at pauses past the target."""
    spans: list[_Span] = []
    current: list[TranscriptSegment] = []

    for position, segment in enumerate(segments):
        current.append(segment)
        duration = current[-1].end - current[0].start
        if duration < target:
            continue

        following = segments[position + 1] if position + 1 < len(segments) else None
        gap = (following.start - segment.end) if following is not None else float("inf")
        if gap >= _PAUSE_SECONDS or duration >= target * _OVERSHOOT:
            spans.append(_Span(current))
            current = []

    if current:
        spans.append(_Span(current))
    return _merge_short_tail(spans)


def _merge_short_tail(spans: list[_Span]) -> list[_Span]:
    """Fold a too-short final span into the one before it.

    The last span is whatever is left over, so it is the only one that can come
    out very short. A five-second chapter at the end of a lecture is a stub in the
    navigation bar, not a chapter.
    """
    if len(spans) > 1 and spans[-1].duration < _MIN_TAIL_SECONDS:
        merged = _Span(spans[-2].segments + spans[-1].segments)
        return spans[:-2] + [merged]
    return spans


async def _titles(
    client: httpx.AsyncClient,
    config: GenerationConfig,
    numbered: list[tuple[int, str]],
    warnings: list[str],
) -> dict[int, str]:
    """Run the single titling call and return ``{chapter number: title}``.

    Connectivity and timeout errors propagate so the request fails fast; unusable
    output degrades to an empty result, which leaves every chapter with its
    fallback title and a warning rather than failing the whole request.
    """
    try:
        raw = await chat_json(
            client,
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            system=prompts.SYSTEM,
            user=prompts.title_prompt(numbered),
            temperature=config.temperature,
        )
        parsed = _TitleResponse.model_validate(raw)
    except (LLMBadResponse, ValidationError) as exc:
        logger.warning("Could not generate chapter titles: %s", exc)
        warnings.append(f"Could not generate chapter titles: {exc}")
        return {}
    return {item.index: item.title.strip() for item in parsed.chapters}


def _clean_title(title: str) -> str:
    """Normalise a returned title: one line, no trailing stop, bounded length."""
    cleaned = " ".join(title.split()).rstrip(" .;:—-")
    return cleaned[:120]


async def generate_chapters(
    client: httpx.AsyncClient, transcript: Transcript, config: GenerationConfig
) -> ChapteredTranscript:
    """Divide a transcript into titled chapters."""
    segments = [segment for segment in transcript.segments if segment.text.strip()]
    if not segments:
        raise EmptyTranscriptError("The transcript has no timed speech to divide into chapters.")

    warnings: list[str] = []
    total = segments[-1].end - segments[0].start
    spans = _split_into_spans(segments, _target_seconds(total))
    numbered = [(number, span.text) for number, span in enumerate(spans, start=1)]
    titles = await _titles(client, config, numbered, warnings)

    chapters: list[Chapter] = []
    for number, span in enumerate(spans, start=1):
        title = _clean_title(titles.get(number, ""))
        if not title:
            title = f"Chapter {number}"
            warnings.append(f"No title was produced for chapter {number}; used a default.")
        chapters.append(
            Chapter(
                index=number,
                start=span.start,
                end=span.end,
                title=title,
                segment_indexes=[segment.index for segment in span.segments],
                text=span.text,
            )
        )

    return ChapteredTranscript(
        source=transcript.source,
        generator=config.provider,
        model=config.model,
        generated_at=datetime.now(timezone.utc),
        language=transcript.language,
        chapters=chapters,
        warnings=warnings,
    )
