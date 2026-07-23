"""Tests for the subtitle emitters (Module C.1).

WebVTT and SRT differ in two small ways that break silently if wrong — the
millisecond separator (dot vs comma) and the header/numbering — so both are
pinned here, along with the timestamp arithmetic.
"""

from datetime import datetime, timezone

from app.transcription.emit import to_srt, to_webvtt
from app.transcription.schema import Transcript, TranscriptSegment, TranscriptSource


def _transcript(segments: list[TranscriptSegment]) -> Transcript:
    return Transcript(
        source=TranscriptSource(filename="lecture.mp3"),
        generator="groq",
        model="whisper-large-v3",
        generated_at=datetime.now(timezone.utc),
        segments=segments,
    )


def _seg(index, start, end, text, speaker=None):
    return TranscriptSegment(index=index, start=start, end=end, text=text, speaker=speaker)


def test_webvtt_starts_with_the_header_and_uses_a_dot_separator():
    vtt = to_webvtt(_transcript([_seg(1, 0.0, 2.5, "Hello world.")]))
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:02.500" in vtt
    assert "Hello world." in vtt


def test_srt_numbers_every_cue_and_uses_a_comma_separator():
    srt = to_srt(_transcript([_seg(1, 0.0, 2.5, "First."), _seg(2, 2.5, 4.0, "Second.")]))
    assert srt.startswith("1\n")
    assert "2\n00:00:02,500 --> 00:00:04,000" in srt
    # SRT has no WEBVTT header, and never the dot form of the separator.
    assert "WEBVTT" not in srt
    assert "00:00:02.500" not in srt


def test_hours_are_rendered_and_milliseconds_are_rounded():
    # 3661.4996 s → 01:01:01.500 once rounded to the millisecond.
    vtt = to_webvtt(_transcript([_seg(1, 3661.4996, 3661.9, "Late cue.")]))
    assert "01:01:01.500 --> 01:01:01.900" in vtt


def test_a_speaker_label_becomes_a_cue_prefix_when_set():
    seg = _seg(1, 0.0, 1.0, "Over here.", speaker="Speaker 1")
    assert "Speaker 1: Over here." in to_webvtt(_transcript([seg]))
    assert "Speaker 1: Over here." in to_srt(_transcript([seg]))


def test_no_speaker_prefix_when_unset():
    body = to_webvtt(_transcript([_seg(1, 0.0, 1.0, "Plain line.")]))
    # The cue text sits on its own line with no "Name: " prefix prepended.
    assert "\nPlain line." in body
    assert "Plain line." in body.splitlines()


def test_an_empty_transcript_is_just_the_vtt_header():
    assert to_webvtt(_transcript([])).strip() == "WEBVTT"
    assert to_srt(_transcript([])).strip() == ""


def test_an_embedded_newline_cannot_split_a_cue():
    # A blank line ends a cue in both formats, so internal newlines are collapsed.
    seg = _seg(1, 0.0, 2.0, "first part\n\nsecond part")
    vtt, srt = to_webvtt(_transcript([seg])), to_srt(_transcript([seg]))
    assert "first part second part" in vtt
    assert "first part second part" in srt
    # Exactly one cue: SRT has a single number line, VTT a single timing line.
    assert srt.count("-->") == 1
    assert vtt.count("-->") == 1


def test_webvtt_escapes_markup_and_neutralises_the_arrow_sentinel():
    seg = _seg(1, 0.0, 2.0, "a < b & c --> d")
    vtt = to_webvtt(_transcript([seg]))
    assert "&lt;" in vtt and "&amp;" in vtt
    # The escaped text must not introduce a second "-->" that reads as a cue timing.
    assert vtt.count(" --> ") == 1
    assert "&gt;" in vtt  # the sentinel's '>' is escaped


def test_srt_keeps_text_literal_but_still_single_line():
    seg = _seg(1, 0.0, 2.0, "a < b & c")
    srt = to_srt(_transcript([seg]))
    # SRT is not HTML, so it is not escaped — but it stays on one line.
    assert "a < b & c" in srt
