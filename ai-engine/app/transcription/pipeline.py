"""Turn an audio or video file into a `Transcript` via the STT gateway.

The gateway does the recognition; this module owns the contract around it. It
sends the file, maps the provider's ``verbose_json`` reply onto our own segment
shape (start, end, text), stamps provenance, and records non-fatal issues — no
speech detected, or a reply with text but no timing — as warnings rather than
failing the request. Connectivity and timeout failures propagate so the endpoint
fails fast with the documented 503/504.

Keeping the mapping here (rather than trusting the provider's shape downstream)
means a second STT provider with slightly different field names is absorbed in one
place, and the rest of Module C only ever sees a `Transcript`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .languages import to_iso639_1
from .schema import Transcript, TranscriptSegment, TranscriptSource
from .stt_client import transcribe_audio


@dataclass(frozen=True)
class TranscriptionConfig:
    """Everything the STT call needs, read from settings by the router."""

    base_url: str
    api_key: str
    model: str
    provider: str
    language: str | None = None


def _coerce_time(value: Any) -> float | None:
    """Read a start/end time as a finite, non-negative float, or None if unusable.

    ``Infinity`` and ``NaN`` are rejected explicitly: Python's ``json`` parses
    those non-standard literals, an infinite timestamp passes ``ge=0`` validation,
    and it then raises ``OverflowError`` when the emitter rounds it to a
    millisecond — so a malformed reply must drop the segment, not crash rendering.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a timestamp
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value) if value >= 0 else 0.0
    return None


def _segments_from(raw: dict[str, Any], warnings: list[str]) -> list[TranscriptSegment]:
    """Map the provider's ``segments`` array onto our timed-cue shape.

    A segment missing usable timing, or empty after trimming, is skipped with a
    warning rather than emitted as a zero-length or blank cue. An end time that
    precedes its start is clamped up to the start, which is what a player would
    do anyway and keeps the cue valid.
    """
    raw_segments = raw.get("segments")
    if not isinstance(raw_segments, list):
        return []

    segments: list[TranscriptSegment] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        # A present-but-null text field must be treated as empty, not stringified
        # into the literal "None" — dict.get's default only fires on a missing key.
        raw_text = item.get("text")
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        start = _coerce_time(item.get("start"))
        end = _coerce_time(item.get("end"))
        if not text or start is None or end is None:
            continue
        segments.append(
            TranscriptSegment(
                index=len(segments) + 1,
                start=start,
                end=max(end, start),
                text=text,
            )
        )
    if raw_segments and not segments:
        warnings.append("The transcript had no usable timed segments.")
    return segments


def _full_text(raw: dict[str, Any], segments: list[TranscriptSegment]) -> str:
    """The whole transcript as plain text: the provider's own text, or the cues joined."""
    text = raw.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return " ".join(segment.text for segment in segments).strip()


async def transcribe(
    client: httpx.AsyncClient,
    audio_path: str,
    filename: str,
    config: TranscriptionConfig,
) -> Transcript:
    """Transcribe one media file into a `Transcript`.

    STT connectivity and timeout errors propagate to the caller (fast 503/504);
    a reply that simply found no speech returns a well-formed, empty transcript
    with a warning.
    """
    raw = await transcribe_audio(
        client,
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        audio_path=audio_path,
        language=config.language,
    )

    warnings: list[str] = []
    segments = _segments_from(raw, warnings)
    full_text = _full_text(raw, segments)
    if not segments and not full_text:
        warnings.append("No speech was detected in the media.")

    # Whisper reports the detected language as a full name ("english"); normalise
    # it to the ISO-639-1 code the contract promises. A supplied hint is already a
    # code, so both paths end up in the same representation.
    detected = raw.get("language")
    language = to_iso639_1(detected) if isinstance(detected, str) and detected else config.language
    duration = _coerce_time(raw.get("duration"))

    return Transcript(
        source=TranscriptSource(filename=filename, media_seconds=duration),
        generator=config.provider,
        model=config.model,
        generated_at=datetime.now(timezone.utc),
        language=language,
        segments=segments,
        full_text=full_text,
        warnings=warnings,
    )
