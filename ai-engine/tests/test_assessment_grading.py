"""Tests for the short-answer grader.

The grader exists in three places — here, in the SCORM player's JavaScript, and in
H5P.Essay itself — because each package has to mark an answer offline, with no
model available. If any of the three drifts, the same learner gets a different
result depending on which format their LMS imported. These tests are what hold
them together.

Two of the cases below assert *failure* modes rather than successes. That is
deliberate: a learner who writes the key phrases in a meaningless order scores
full marks, and one who is entirely correct in different words scores zero. Both
are properties of exact phrase matching, both are disclosed to the learner on the
results screen, and pinning them here means a future "improvement" that quietly
changes the marking has to fail a test first.
"""

from __future__ import annotations

import pytest

from app.assessment.grading import key_points_made, normalise, point_is_made, score_short_answer
from app.assessment.schema import KeyPoint, ShortAnswerItem


def _item(**overrides) -> ShortAnswerItem:
    kwargs = {
        "id": "q1",
        "prompt": "Explain how a sea breeze forms and what happens at night.",
        "points": 3.0,
        "model_answer": (
            "In summer the land heats faster than the water, so wind blows from sea to "
            "land. At night it reverses."
        ),
        "key_points": [
            KeyPoint(id="q1-k1", text="The land warms faster", accepted=["land heats", "land warms"]),
            KeyPoint(id="q1-k2", text="Air moves inland", accepted=["sea to land", "onshore"]),
            KeyPoint(id="q1-k3", text="It reverses at night", accepted=["reverses", "reverse"]),
        ],
    }
    kwargs.update(overrides)
    return ShortAnswerItem(**kwargs)


# --- the marking table -------------------------------------------------------
#
# Every row here was also run through H5P.Essay's own algorithm in a browser, and
# all eight agreed. See work-docs/proof/week-07-short-answer/.

@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("In summer the land heats faster, so wind blows from sea to land. In winter it reverses.", 3.0),
        ("The land heats up more than the water.", 1.0),
        ("The land heats faster.\nWind goes from sea to land.\nIt reverses.", 3.0),
        ("", 0.0),
    ],
)
def test_an_answer_scores_the_key_points_it_makes(answer, expected):
    assert score_short_answer(_item(), answer) == pytest.approx(expected)


def test_a_stuffed_answer_scores_full_marks_and_that_is_a_known_limit():
    # Exact phrase matching has no notion of syntax or coherence, so bare key
    # phrases score. min_chars raises the cost of doing this; it cannot detect it,
    # and the ADR says so rather than implying the problem is solved.
    assert score_short_answer(_item(), "land heats sea to land reverses") == pytest.approx(3.0)


def test_a_correct_answer_in_different_words_scores_nothing_and_that_is_the_other_limit():
    # The symmetric failure. Recall comes from the `accepted` variants and is
    # finite. The learner is shown which points were missed and the model answer,
    # so the mark is at least inspectable.
    paraphrase = "The ground warms quicker than the water, pulling damp air inland; winter flips it."
    assert score_short_answer(_item(), paraphrase) == pytest.approx(0.0)


# --- the two quirks that must be reproduced, not fixed -----------------------


def test_whitespace_is_halved_rather_than_collapsed():
    # H5P's normalisation is a single non-overlapping pass over pairs, so four
    # spaces become two. Writing \s+ here would be tidier and would silently
    # disagree with H5P — the same answer would score differently in the two
    # packages.
    assert normalise("a  b") == "a b"
    assert normalise("a    b") == "a  b"
    assert normalise("sea   to land") == "sea  to land"


def test_a_triple_space_defeats_a_multi_word_phrase_in_both_packages():
    assert score_short_answer(_item(), "wind blows from sea   to land") == pytest.approx(0.0)


def test_a_match_must_sit_on_word_boundaries():
    # "grassland heats up" contains "land heats" as a substring, but the preceding
    # character is 's' — not a delimiter — so H5P does not count it and neither do we.
    assert score_short_answer(_item(), "grassland heats up") == pytest.approx(0.0)
    assert score_short_answer(_item(), "the land heats up") == pytest.approx(1.0)


@pytest.mark.parametrize("boundary", [".", ",", "?", "!", ";", "'", '"', " "])
def test_every_word_delimiter_h5p_recognises_isolates_a_match(boundary):
    point = KeyPoint(id="k", text="t", accepted=["reverses"])
    assert point_is_made(point, f"it reverses{boundary} then stops")


def test_a_phrase_at_the_very_start_or_end_still_counts():
    point = KeyPoint(id="k", text="t", accepted=["reverses"])
    assert point_is_made(point, "reverses")
    assert point_is_made(point, "at night it reverses")
    assert point_is_made(point, "reverses at night")


# --- the rest ----------------------------------------------------------------


def test_marking_is_case_insensitive():
    # H5P defaults keyword matching to case-SENSITIVE; the emitter forces it off,
    # because a learner should not lose a mark for a lower-case sentence start.
    assert score_short_answer(_item(), "LAND HEATS faster") == pytest.approx(1.0)


def test_any_accepted_variant_scores_the_point_once():
    # Two forms of the same point are an OR, not two marks.
    assert score_short_answer(_item(), "the land heats and the land warms") == pytest.approx(1.0)


def test_the_ids_of_the_points_made_are_reported_in_marking_order():
    made = key_points_made(_item(), "the land heats and it reverses")
    assert made == ["q1-k1", "q1-k3"]


def test_a_weighted_point_is_worth_its_weight():
    item = _item(
        points=4.0,
        key_points=[
            KeyPoint(id="q1-k1", text="The main idea", accepted=["land heats"], weight=3),
            KeyPoint(id="q1-k2", text="A detail", accepted=["reverses"], weight=1),
        ],
    )
    assert score_short_answer(item, "the land heats") == pytest.approx(3.0)
    assert score_short_answer(item, "it reverses") == pytest.approx(1.0)


def test_a_full_answer_scores_exactly_the_questions_points():
    # The contract pins points to the summed weights, so this is also H5P's
    # getMaxScore() and our SCORM denominator — one number, three consumers.
    item = _item()
    assert score_short_answer(item, item.model_answer) == pytest.approx(item.points)
