"""Shared builders for assessment fixtures.

The emitter, schema and grader suites all need the same handful of valid
questions. Keeping one definition means a contract change breaks in one place
rather than four, and a reader comparing two suites sees only what actually
differs between them.

Each builder takes keyword overrides, so a test names only the field it cares
about and the rest stays a known-good default.
"""

from __future__ import annotations

from datetime import datetime, timezone

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


def make_set(questions, **overrides) -> AssessmentSet:
    kwargs = {
        "assessment_id": "a-1",
        "source": AssessmentSource(filename="lesson.pdf", title="Lesson", page_count=2),
        "generator": "test",
        "model": "m",
        "generated_at": datetime(2026, 7, 17, tzinfo=timezone.utc),
        "questions": questions,
    }
    kwargs.update(overrides)
    return AssessmentSet(**kwargs)


def make_mcq(**overrides) -> MCQItem:
    kwargs = {
        "id": "q1",
        "prompt": "Which one?",
        "choices": [
            Choice(id="q1-c1", text="Right", is_correct=True),
            Choice(id="q1-c2", text="Wrong"),
        ],
    }
    kwargs.update(overrides)
    return MCQItem(**kwargs)


def make_match(**overrides) -> MatchItem:
    kwargs = {
        "id": "q1",
        "prompt": "Match them.",
        "points": 2.0,
        "sources": [
            MatchSource(id="q1-s1", text="Heart", target_id="q1-t3"),
            MatchSource(id="q1-s2", text="Lungs", target_id="q1-t1"),
        ],
        "targets": [
            MatchTarget(id="q1-t1", text="Gas exchange"),
            MatchTarget(id="q1-t2", text="Filtration"),
            MatchTarget(id="q1-t3", text="Pumps blood"),
        ],
    }
    kwargs.update(overrides)
    return MatchItem(**kwargs)


def make_blank(**overrides) -> FillBlankItem:
    kwargs = {
        "id": "q1",
        "text": "The capital of India is [[1]].",
        "blanks": [Blank(id="q1-b1", answers=["New Delhi", "Delhi"])],
    }
    kwargs.update(overrides)
    return FillBlankItem(**kwargs)


def make_short(**overrides) -> ShortAnswerItem:
    kwargs = {
        "id": "q1",
        "prompt": "Explain how a sea breeze forms.",
        "points": 2.0,
        "model_answer": "The land heats faster, so wind blows from sea to land.",
        "key_points": [
            KeyPoint(id="q1-k1", text="Land warms faster", accepted=["land heats", "land warms"]),
            KeyPoint(id="q1-k2", text="Air moves inland", accepted=["sea to land"]),
        ],
    }
    kwargs.update(overrides)
    return ShortAnswerItem(**kwargs)
