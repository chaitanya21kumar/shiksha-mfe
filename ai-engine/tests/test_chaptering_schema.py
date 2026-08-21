"""Tests for the Module C.2 contract.

The ordering rules matter more than they look: chapters become bookmarks in a
player's navigation bar, so an overlapping or out-of-order list seeks the learner
to the wrong place while the JSON still looks perfectly well-formed.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.chaptering.schema import MAX_CHAPTERS, MAX_TITLE_CHARS, Chapter, ChapteredTranscript
from app.transcription.schema import TranscriptSource


def _chaptered(chapters: list[Chapter]) -> ChapteredTranscript:
    return ChapteredTranscript(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=300.0),
        generator="groq",
        model="openai/gpt-oss-20b",
        generated_at=datetime.now(timezone.utc),
        chapters=chapters,
    )


def _chapter(index, start, end, title="Something"):
    return Chapter(index=index, start=start, end=end, title=title)


def test_a_chapter_end_may_not_precede_its_start():
    with pytest.raises(ValidationError):
        Chapter(index=1, start=90.0, end=30.0, title="Backwards")


def test_chapters_must_be_numbered_one_to_n_in_order():
    misnumbered = [_chapter(1, 0, 60), _chapter(3, 60, 120)]
    with pytest.raises(ValidationError, match="1..n"):
        _chaptered(misnumbered)


def test_chapters_may_not_overlap():
    # The second chapter starts before the first has finished.
    overlapping = [_chapter(1, 0, 90), _chapter(2, 60, 150)]
    with pytest.raises(ValidationError, match="before the previous"):
        _chaptered(overlapping)


def test_touching_chapters_are_allowed():
    # end == next start is the normal case: contiguous coverage, no overlap.
    result = _chaptered([_chapter(1, 0, 90), _chapter(2, 90, 180)])
    assert [c.index for c in result.chapters] == [1, 2]


def test_a_gap_between_chapters_is_allowed():
    # Silence between chapters is legitimate; only going backwards is not.
    result = _chaptered([_chapter(1, 0, 60), _chapter(2, 75, 150)])
    assert result.chapters[1].start == 75


def test_duration_is_derived_not_stored():
    assert _chapter(1, 30.0, 105.5).duration == 75.5


def test_a_chaptered_transcript_round_trips_through_json():
    original = _chaptered(
        [
            Chapter(
                index=1,
                start=0.0,
                end=90.0,
                title="The water cycle",
                segment_indexes=[1, 2, 3],
                text="Evaporation lifts water into the air.",
            )
        ]
    )
    assert ChapteredTranscript.model_validate_json(original.model_dump_json()) == original


# POST /interactive-video takes a hand-built ChapteredTranscript, and a JSON body
# has no equivalent of the upload ceiling. The generator's own invariants therefore
# live on the contract, where the emitter can rely on them.


def test_a_title_longer_than_the_contract_allows_is_rejected():
    overlong = "x" * (MAX_TITLE_CHARS + 1)
    with pytest.raises(ValidationError):
        Chapter(index=1, start=0.0, end=1.0, title=overlong)


def test_more_chapters_than_the_contract_allows_are_rejected():
    too_many = [_chapter(i, float(i), float(i) + 1, f"C{i}") for i in range(1, MAX_CHAPTERS + 2)]
    with pytest.raises(ValidationError):
        _chaptered(too_many)
