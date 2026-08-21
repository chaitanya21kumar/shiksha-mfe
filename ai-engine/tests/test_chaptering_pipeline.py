"""Tests for the chaptering pipeline (Module C.2).

The model gateway is mocked with an httpx ``MockTransport``, so these run offline.
The point of most of them is that the *division* is deterministic: the boundaries,
the count and the segment membership must be exactly predictable, because only the
titles are generated.
"""

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.chaptering.pipeline import EmptyTranscriptError, generate_chapters
from app.summarization.llm_client import LLMUnavailable
from app.summarization.pipeline import GenerationConfig
from app.transcription.schema import Transcript, TranscriptSegment, TranscriptSource

_CONFIG = GenerationConfig(
    base_url="https://llm.test/v1",
    api_key="k",
    model="openai/gpt-oss-20b",
    provider="groq",
    temperature=0.2,
    max_source_chars=24000,
)


def _transcript(segments: list[TranscriptSegment], language: str | None = "en") -> Transcript:
    return Transcript(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=segments[-1].end if segments else 0),
        generator="groq",
        model="whisper-large-v3",
        generated_at=datetime.now(timezone.utc),
        language=language,
        segments=segments,
        full_text=" ".join(s.text for s in segments),
    )


def _speech(count: int, *, seconds: float = 10.0, gap: float = 0.1, start: float = 0.0):
    """`count` back-to-back segments of `seconds` each, separated by `gap`."""
    segments = []
    at = start
    for i in range(1, count + 1):
        segments.append(TranscriptSegment(index=i, start=at, end=at + seconds, text=f"sentence {i}"))
        at += seconds + gap
    return segments


def _titles_handler(titles: dict[int, str] | None = None):
    """A gateway that titles every chapter it is asked about."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        # Count how many chapters the prompt asked for, and answer all of them.
        asked = body.count("Chapter ")
        chapters = [
            {"index": i, "title": (titles or {}).get(i, f"Topic {i}")} for i in range(1, asked + 1)
        ]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": __import__("json").dumps({"chapters": chapters})}}
                ]
            },
        )

    return handler


def _run(handler, transcript):
    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0)) as client:
            return await generate_chapters(client, transcript, _CONFIG)

    return asyncio.run(go())


# --- the division is deterministic ------------------------------------------


def test_a_short_recording_is_a_single_chapter():
    # Well under the 90 s target, so there is nothing to divide.
    result = _run(_titles_handler(), _transcript(_speech(4)))
    assert len(result.chapters) == 1
    assert result.chapters[0].start == 0.0
    assert result.chapters[0].segment_indexes == [1, 2, 3, 4]


def test_a_long_recording_breaks_at_the_pause_after_the_target():
    # 10 s segments with a real pause after the 10th (i.e. at 101 s, past the
    # 90 s target) — the chapter must end exactly there, not mid-thought.
    segments = _speech(10, seconds=10.0, gap=0.1)
    tail_start = segments[-1].end + 2.0  # a 2 s pause: a natural break
    segments += _speech(6, seconds=10.0, gap=0.1, start=tail_start)
    for position, segment in enumerate(segments, start=1):
        segment.index = position

    result = _run(_titles_handler(), _transcript(segments))
    assert len(result.chapters) == 2
    assert result.chapters[0].segment_indexes == list(range(1, 11))
    assert result.chapters[1].start == tail_start


def test_continuous_speech_is_broken_by_the_overshoot_ceiling():
    # No pause ever reaches 0.6 s, so only the overshoot ceiling can end a
    # chapter — without it one unbroken stretch would swallow the recording.
    segments = _speech(60, seconds=10.0, gap=0.05)
    result = _run(_titles_handler(), _transcript(segments))
    assert len(result.chapters) > 1
    assert all(c.duration <= 90.0 * 1.6 + 10.0 for c in result.chapters)


def test_chapters_are_contiguous_and_cover_every_segment():
    segments = _speech(40, seconds=10.0, gap=0.1)
    result = _run(_titles_handler(), _transcript(segments))
    covered = [i for chapter in result.chapters for i in chapter.segment_indexes]
    assert covered == list(range(1, 41))  # in order, nothing lost, nothing repeated


def test_a_very_short_trailing_chapter_is_folded_into_the_previous_one():
    # A 5 s tail after a long pause would otherwise be a stub in the nav bar.
    segments = _speech(10, seconds=10.0, gap=0.1)
    segments.append(TranscriptSegment(index=11, start=segments[-1].end + 3.0,
                                      end=segments[-1].end + 8.0, text="one last thought"))
    result = _run(_titles_handler(), _transcript(segments))
    assert result.chapters[-1].segment_indexes[-1] == 11
    assert len(result.chapters) == 1


def test_the_chapter_count_is_capped_for_a_very_long_recording():
    # 4 hours of continuous speech: the target stretches so the cap holds.
    segments = _speech(1440, seconds=10.0, gap=0.05)
    result = _run(_titles_handler(), _transcript(segments))
    assert len(result.chapters) <= 24


def test_blank_segments_are_ignored_when_dividing():
    segments = _speech(4)
    segments.insert(2, TranscriptSegment(index=99, start=20.0, end=20.5, text="   "))
    result = _run(_titles_handler(), _transcript(segments))
    assert 99 not in result.chapters[0].segment_indexes


# --- titles, provenance and degradation --------------------------------------


def test_titles_are_applied_and_provenance_recorded():
    result = _run(_titles_handler({1: "The water cycle"}), _transcript(_speech(4)))
    assert result.chapters[0].title == "The water cycle"
    assert result.generator == "groq"
    assert result.model == "openai/gpt-oss-20b"
    assert result.language == "en"
    assert result.source.filename == "lecture.mp4"


def test_a_title_is_normalised_to_one_line_without_a_trailing_stop():
    result = _run(_titles_handler({1: "  The water\n cycle.  "}), _transcript(_speech(4)))
    assert result.chapters[0].title == "The water cycle"


def test_a_chapter_the_model_skips_gets_a_default_title_and_a_warning():
    def handler(request: httpx.Request) -> httpx.Response:
        # Answers only chapter 1, whatever was asked.
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"chapters":[{"index":1,"title":"Only one"}]}'}}]},
        )

    segments = _speech(40, seconds=10.0, gap=0.1)
    result = _run(handler, _transcript(segments))
    assert result.chapters[0].title == "Only one"
    assert result.chapters[1].title == "Chapter 2"
    assert any("chapter 2" in w.lower() for w in result.warnings)


def test_unusable_model_output_degrades_to_default_titles_with_a_warning():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    result = _run(handler, _transcript(_speech(4)))
    assert result.chapters[0].title == "Chapter 1"
    assert any("could not generate" in w.lower() for w in result.warnings)


def test_a_title_returned_as_a_list_is_joined_rather_than_lost():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"chapters":[{"index":1,"title":["The","water","cycle"]}]}'}}]},
        )

    result = _run(handler, _transcript(_speech(4)))
    assert result.chapters[0].title == "The water cycle"


def test_a_transcript_with_no_speech_is_rejected():
    empty = _transcript([TranscriptSegment(index=1, start=0, end=1, text="  ")])
    handler = _titles_handler()
    with pytest.raises(EmptyTranscriptError):
        _run(handler, empty)


def test_gateway_failures_propagate():
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    transcript = _transcript(_speech(4))
    with pytest.raises(LLMUnavailable):
        _run(unreachable, transcript)


# --- input the provider is not obliged to give us in order --------------------


def test_out_of_order_segments_are_sorted_rather_than_crashing_the_contract():
    # Nothing in the Transcript contract forbids segments that are out of order,
    # and Whisper does emit repeat/rewind cues on looping audio. A backwards
    # segment used to build a chapter with end < start, which the ChapteredTranscript
    # contract rightly refuses — reaching the caller as an unhandled 500.
    segments = [
        TranscriptSegment(index=1, start=100.0, end=145.0, text="second half"),
        TranscriptSegment(index=2, start=0.0, end=95.0, text="first half"),
    ]
    result = _run(_titles_handler(), _transcript(segments))
    assert [c.start for c in result.chapters] == sorted(c.start for c in result.chapters)
    assert all(c.end >= c.start for c in result.chapters)
    assert any("not in time order" in w for w in result.warnings)


def test_overlapping_segments_do_not_produce_overlapping_chapters():
    segments = [
        TranscriptSegment(index=1, start=0.0, end=145.0, text="a long stretch"),
        TranscriptSegment(index=2, start=50.0, end=100.0, text="a repeated cue"),
    ]
    result = _run(_titles_handler(), _transcript(segments))
    previous = None
    for chapter in result.chapters:
        assert chapter.end >= chapter.start
        if previous is not None:
            assert chapter.start >= previous
        previous = chapter.end


def test_a_single_unbroken_segment_says_so_instead_of_pretending_to_chapter():
    # Boundaries can only fall *between* segments, so a provider that returns one
    # segment for a whole recording cannot be split at all — and a lone chapter
    # covering an hour should not look like a considered decision.
    segments = [TranscriptSegment(index=1, start=0.0, end=3600.0, text="one long stretch")]
    result = _run(_titles_handler(), _transcript(segments))
    assert len(result.chapters) == 1
    assert any("single chapter" in w for w in result.warnings)


def test_a_title_failure_is_reported_on_one_line_so_it_survives_a_header():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"chapters": [{"index": "one"}]}'}}]}
        )

    result = _run(handler, _transcript(_speech(30)))
    failures = [w for w in result.warnings if w.startswith("Could not generate chapter titles")]
    assert failures and all("\n" not in w for w in failures)
