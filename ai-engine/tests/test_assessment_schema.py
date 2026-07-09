"""Direct tests for the assessment contract's validators and computed fields.

These construct the typed models directly (rather than through the pipeline) so
every validator branch is exercised, including the ones the pipeline never
produces because it assembles valid input.
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
    MatchItem,
    MatchSource,
    MatchTarget,
    MCQItem,
)


def _mcq(**overrides) -> MCQItem:
    kwargs = dict(
        id="q1",
        prompt="Q?",
        choices=[Choice(id="q1-c1", text="a", is_correct=True), Choice(id="q1-c2", text="b")],
    )
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
    with pytest.raises(ValidationError):
        _mcq(choices=[Choice(id="c", text="a", is_correct=True), Choice(id="c", text="b")])


def test_single_answer_requires_exactly_one_correct():
    with pytest.raises(ValidationError):  # two correct
        _mcq(choices=[Choice(id="c1", text="a", is_correct=True), Choice(id="c2", text="b", is_correct=True)])
    with pytest.raises(ValidationError):  # zero correct
        _mcq(choices=[Choice(id="c1", text="a"), Choice(id="c2", text="b")])


def test_multi_answer_allows_more_than_one_correct():
    q = _mcq(
        single_answer=False,
        choices=[Choice(id="c1", text="a", is_correct=True), Choice(id="c2", text="b", is_correct=True)],
    )
    assert sum(c.is_correct for c in q.choices) == 2


# --------------------------------------------------------------------------- #
# Match
# --------------------------------------------------------------------------- #
def test_match_unknown_target_id_rejected():
    with pytest.raises(ValidationError):
        MatchItem(
            id="q1",
            prompt="Match",
            sources=[MatchSource(id="s1", text="a", target_id="missing"), MatchSource(id="s2", text="b", target_id="t1")],
            targets=[MatchTarget(id="t1", text="x"), MatchTarget(id="t2", text="y")],
        )


def test_match_duplicate_target_ids_rejected():
    with pytest.raises(ValidationError):
        MatchItem(
            id="q1",
            prompt="Match",
            sources=[MatchSource(id="s1", text="a", target_id="t1"), MatchSource(id="s2", text="b", target_id="t1")],
            targets=[MatchTarget(id="t1", text="x"), MatchTarget(id="t1", text="y")],
        )


# --------------------------------------------------------------------------- #
# Fill-in-the-blank
# --------------------------------------------------------------------------- #
def test_fill_blank_marker_count_must_match_blanks():
    with pytest.raises(ValidationError):
        FillBlankItem(id="q1", text="a [[1]] and [[2]]", blanks=[Blank(id="b1", answers=["x"])])


def test_fill_blank_duplicate_markers_rejected():
    # A blank marked twice must be rejected, even though a set would dedupe to {1}.
    with pytest.raises(ValidationError):
        FillBlankItem(id="q1", text="[[1]] and [[1]] again", blanks=[Blank(id="b1", answers=["x"])])


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
    with pytest.raises(ValidationError):
        _set([_mcq(id="q1"), _mcq(id="q1")])


def test_computed_max_points_and_counts():
    s = _set([_mcq(id="q1", points=2.0), _mcq(id="q2")])
    dumped = s.model_dump()
    assert dumped["max_points"] == 3.0
    assert dumped["counts"] == {"mcq": 2}
