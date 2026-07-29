"""Tests for the Module C.1 contract."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.transcription.schema import Transcript, TranscriptSegment, TranscriptSource


def test_a_segment_end_may_not_precede_its_start():
    with pytest.raises(ValidationError):
        TranscriptSegment(index=1, start=5.0, end=2.0, text="backwards")


def test_a_zero_length_cue_is_allowed():
    # end == start is a valid instant cue; only end < start is rejected.
    seg = TranscriptSegment(index=1, start=2.0, end=2.0, text="instant")
    assert seg.end == seg.start


def test_a_negative_time_is_rejected():
    with pytest.raises(ValidationError):
        TranscriptSegment(index=1, start=-0.5, end=1.0, text="before zero")


def test_the_speaker_field_defaults_to_unset():
    seg = TranscriptSegment(index=1, start=0.0, end=1.0, text="hi")
    assert seg.speaker is None


def test_a_transcript_round_trips_through_json():
    original = Transcript(
        source=TranscriptSource(filename="a.wav", media_seconds=12.3),
        generator="groq",
        model="whisper-large-v3",
        generated_at=datetime.now(timezone.utc),
        language="english",
        segments=[TranscriptSegment(index=1, start=0.0, end=2.0, text="hello")],
        full_text="hello",
    )
    restored = Transcript.model_validate_json(original.model_dump_json())
    assert restored == original
