"""Direct tests for the assessment contract's validators and computed fields.

These construct the typed models directly (rather than through the pipeline) so
every validator branch is exercised, including the ones the pipeline never
produces because it assembles valid input. Each ``pytest.raises`` block wraps a
single constructor call, with its inputs built beforehand.
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.assessment.schema import (
    AssessmentSet,
    AssessmentSource,
    Blank,
    Choice,
    FillBlankItem,
    KeyPoint,
    MatchItem,
    MatchSource,
    MatchTarget,
    MCQItem,
    ShortAnswerItem,
)


def _mcq(**overrides) -> MCQItem:
    kwargs = {
        "id": "q1",
        "prompt": "Q?",
        "choices": [Choice(id="q1-c1", text="a", is_correct=True), Choice(id="q1-c2", text="b")],
    }
    kwargs.update(overrides)
    return MCQItem(**kwargs)


def _set(questions) -> AssessmentSet:
    return AssessmentSet(
        assessment_id="a-1",
        source=AssessmentSource(filename="x.pdf", page_count=1),
        generator="test",
        model="m",
        generated_at=datetime.now(timezone.utc),
        questions=questions,
    )


# --------------------------------------------------------------------------- #
# MCQ
# --------------------------------------------------------------------------- #
def test_mcq_duplicate_choice_ids_rejected():
    choices = [Choice(id="c", text="a", is_correct=True), Choice(id="c", text="b")]
    with pytest.raises(ValidationError):
        _mcq(choices=choices)


def test_single_answer_with_two_correct_rejected():
    choices = [Choice(id="c1", text="a", is_correct=True), Choice(id="c2", text="b", is_correct=True)]
    with pytest.raises(ValidationError):
        _mcq(choices=choices)


def test_single_answer_with_zero_correct_rejected():
    choices = [Choice(id="c1", text="a"), Choice(id="c2", text="b")]
    with pytest.raises(ValidationError):
        _mcq(choices=choices)


def test_multi_answer_allows_more_than_one_correct():
    q = _mcq(
        single_answer=False,
        choices=[Choice(id="c1", text="a", is_correct=True), Choice(id="c2", text="b", is_correct=True)],
    )
    assert sum(c.is_correct for c in q.choices) == 2


def test_mcq_duplicate_choice_texts_rejected():
    choices = [Choice(id="c1", text="Paris", is_correct=True), Choice(id="c2", text="paris")]
    with pytest.raises(ValidationError):  # case-insensitive
        _mcq(choices=choices)


# --------------------------------------------------------------------------- #
# Match
# --------------------------------------------------------------------------- #
def test_match_unknown_target_id_rejected():
    sources = [MatchSource(id="s1", text="a", target_id="missing"), MatchSource(id="s2", text="b", target_id="t1")]
    targets = [MatchTarget(id="t1", text="x"), MatchTarget(id="t2", text="y")]
    with pytest.raises(ValidationError):
        MatchItem(id="q1", prompt="Match", sources=sources, targets=targets)


def test_match_duplicate_target_ids_rejected():
    sources = [MatchSource(id="s1", text="a", target_id="t1"), MatchSource(id="s2", text="b", target_id="t1")]
    targets = [MatchTarget(id="t1", text="x"), MatchTarget(id="t1", text="y")]
    with pytest.raises(ValidationError):
        MatchItem(id="q1", prompt="Match", sources=sources, targets=targets)


def test_match_duplicate_target_texts_rejected():
    sources = [MatchSource(id="s1", text="a", target_id="t1"), MatchSource(id="s2", text="b", target_id="t2")]
    targets = [MatchTarget(id="t1", text="Same"), MatchTarget(id="t2", text="same")]
    with pytest.raises(ValidationError):  # case-insensitive
        MatchItem(id="q1", prompt="Match", sources=sources, targets=targets)


def test_match_duplicate_source_texts_rejected():
    sources = [MatchSource(id="s1", text="Dup", target_id="t1"), MatchSource(id="s2", text="dup", target_id="t2")]
    targets = [MatchTarget(id="t1", text="x"), MatchTarget(id="t2", text="y")]
    with pytest.raises(ValidationError):  # case-insensitive
        MatchItem(id="q1", prompt="Match", sources=sources, targets=targets)


# --------------------------------------------------------------------------- #
# Fill-in-the-blank
# --------------------------------------------------------------------------- #
def test_fill_blank_marker_count_must_match_blanks():
    blanks = [Blank(id="b1", answers=["x"])]
    with pytest.raises(ValidationError):
        FillBlankItem(id="q1", text="a [[1]] and [[2]]", blanks=blanks)


def test_fill_blank_duplicate_markers_rejected():
    # A blank marked twice must be rejected, even though a set would dedupe to {1}.
    blanks = [Blank(id="b1", answers=["x"])]
    with pytest.raises(ValidationError):
        FillBlankItem(id="q1", text="[[1]] and [[1]] again", blanks=blanks)


def test_fill_blank_two_blanks_in_order_ok():
    q = FillBlankItem(
        id="q1",
        text="[[1]] absorbs light in the [[2]].",
        blanks=[Blank(id="b1", answers=["Chlorophyll"]), Blank(id="b2", answers=["leaf"])],
    )
    assert [b.id for b in q.blanks] == ["b1", "b2"]


# --------------------------------------------------------------------------- #
# AssessmentSet envelope
# --------------------------------------------------------------------------- #
def test_duplicate_question_ids_rejected():
    questions = [_mcq(id="q1"), _mcq(id="q1")]
    with pytest.raises(ValidationError):
        _set(questions)


def test_computed_max_points_and_counts():
    s = _set([_mcq(id="q1", points=2.0), _mcq(id="q2")])
    dumped = s.model_dump()
    assert dumped["max_points"] == pytest.approx(3.0)
    assert dumped["counts"] == {"mcq": 2}


# --- short answer ------------------------------------------------------------


def _key_points(n: int = 2) -> list[KeyPoint]:
    return [
        KeyPoint(id=f"q1-k{i}", text=f"Idea {i}", accepted=[f"phrase {i}"])
        for i in range(1, n + 1)
    ]


def _short(**overrides) -> ShortAnswerItem:
    kwargs = {
        "id": "q1",
        "prompt": "Explain it.",
        "key_points": _key_points(2),
        "model_answer": "phrase 1 and phrase 2.",
        "points": 2.0,
    }
    kwargs.update(overrides)
    return ShortAnswerItem(**kwargs)


def test_short_answer_points_must_equal_the_summed_key_point_weights():
    # H5P.Essay scores out of the summed keyword points and our SCORM grader awards
    # the same weights, so a disagreement here would make one answer score
    # differently in the two packages.
    with pytest.raises(ValidationError, match="must equal the sum"):
        _short(points=5.0)


def test_a_short_answer_needs_at_least_two_key_points():
    # One key point is a fill-in-the-blank wearing a textarea.
    with pytest.raises(ValidationError):
        _short(key_points=_key_points(1), points=1.0)


def test_a_short_answer_caps_at_four_key_points():
    # Agreement between markers decays as marks per item grow, so the ceiling is a
    # contract rule rather than a prompt request.
    with pytest.raises(ValidationError):
        _short(key_points=_key_points(5), points=5.0)


def test_duplicate_key_point_ids_are_rejected():
    same_id = [
        KeyPoint(id="q1-k1", text="One", accepted=["phrase one"]),
        KeyPoint(id="q1-k1", text="Two", accepted=["phrase two"]),
    ]
    with pytest.raises(ValidationError, match="ids must be unique"):
        _short(key_points=same_id)


def test_duplicate_key_point_texts_are_rejected():
    same_text = [
        KeyPoint(id="q1-k1", text="Same", accepted=["phrase one"]),
        KeyPoint(id="q1-k2", text="same", accepted=["phrase two"]),
    ]
    with pytest.raises(ValidationError, match="texts must be unique"):
        _short(key_points=same_text)


@pytest.mark.parametrize(
    ("form", "why"),
    [
        ("", "blank"),
        ("   ", "blank"),
        ("heats * faster", "contain"),
        ("/reverses/", "regex"),
        ("x" * 61, "exceed"),
    ],
)
def test_an_accepted_form_h5p_would_reinterpret_is_rejected(form, why):
    # * is a wildcard to H5P.Essay and /…/ is compiled as a regex, so either would
    # match something other than itself. The 60-char cap also keeps every SCORM
    # correct_responses pattern inside CMIString255.
    with pytest.raises(ValidationError, match=why):
        KeyPoint(id="k", text="t", accepted=[form])


def test_accepted_forms_are_unique_case_insensitively():
    with pytest.raises(ValidationError, match="unique"):
        KeyPoint(id="k", text="t", accepted=["Land Heats", "land heats"])


def test_min_chars_cannot_exceed_max_chars():
    with pytest.raises(ValidationError, match="cannot exceed"):
        _short(min_chars=500, max_chars=100)


def test_adding_a_short_answer_leaves_the_computed_fields_correct():
    # Additivity: max_points and counts walk the list, so the fourth union member
    # is picked up with no change to either.
    assessment = _set([_mcq(), _short(id="q2", key_points=[
        KeyPoint(id="q2-k1", text="A", accepted=["a"]),
        KeyPoint(id="q2-k2", text="B", accepted=["b"]),
        KeyPoint(id="q2-k3", text="C", accepted=["c"]),
    ], points=3.0)])
    assert assessment.max_points == pytest.approx(4.0)
    assert assessment.counts == {"mcq": 1, "short_answer": 1}
