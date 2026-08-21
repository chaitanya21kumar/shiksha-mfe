"""Module E: running every module over one source, and reporting on each.

The orchestration itself is thin — each stage is another module's own entry point.
What these pin is the *failure policy*, which is the only thing this layer decides
and the thing a teacher actually depends on.

The case that matters is not "everything worked". It is the one where three stages
worked and one did not, because that is what a scanned page or a document of
headings produces, and because failing the whole request there would throw away
three good artefacts and explain nothing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.course import pipeline as P
from app.course.schema import CourseOptions, Stage, StageOutcome
from app.ingestion.schema import Block, BlockKind, Page, ParsedDocument, SourceInfo
from app.microlesson.schema import LessonStep, MicroLesson
from app.narration.schema import NarrationScript, NarrationSource
from app.summarization.schema import DocumentInsights, InsightsSource
from tests.factories import make_mcq, make_set

WHEN = datetime(2026, 8, 20, tzinfo=timezone.utc)


def make_insights(**kw) -> DocumentInsights:
    return DocumentInsights(
        source=InsightsSource(filename="lesson.pdf", page_count=1),
        generator="test", model="m", generated_at=WHEN, **kw,
    )


def make_narration(**kw) -> NarrationScript:
    return NarrationScript(
        source=NarrationSource(filename="lesson.pdf", page_count=1),
        generator="test", model="m", generated_at=WHEN, **kw,
    )


def make_assessment(**kw):
    return make_set([make_mcq()], **kw)


class Config:
    provider = "test"
    model = "m"
    temperature = 0.2


def make_document() -> ParsedDocument:
    return ParsedDocument(
        source=SourceInfo(filename="lesson.pdf", format="pdf", page_count=1, title="Lesson"),
        parser="test",
        parser_version="1.0",
        parsed_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        pages=[
            Page(index=1, kind="page", blocks=[
                Block(kind=BlockKind.heading, text="Evaporation", level=1),
                Block(kind=BlockKind.paragraph, text="The sun warms the ocean and water rises."),
            ])
        ],
    )


def make_lesson(**kw) -> MicroLesson:
    defaults = dict(
        lesson_id="l-1", source={"kind": "document", "unit_count": 3}, title="Lesson",
        generator="test", model="m", generated_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        steps=[LessonStep(index=1, title="Evaporation", bullets=["The sun warms it"], notes="")],
    )
    defaults.update(kw)
    return MicroLesson(**defaults)


def stub(monkeypatch, name, result=None, raises=None):
    """Replace one stage. Every stage here is another module's function, so the tests
    never depend on a model — only on how this layer treats what comes back."""
    async def fake(*a, **k):
        if raises is not None:
            raise raises
        return result
    monkeypatch.setattr(P, name, fake)


def all_good(monkeypatch):
    stub(monkeypatch, "generate_insights", result=make_insights())
    stub(monkeypatch, "generate_narration", result=make_narration())
    stub(monkeypatch, "generate_assessment", result=make_assessment())
    stub(monkeypatch, "lesson_from_document", result=make_lesson())


def build(**kwargs):
    """Run a build the way the rest of this suite runs async code: no plugin, just
    `asyncio.run`, so the tests stay readable and the dependency list stays short.

    Anything that is not a source is an option, so a test names only the knob it
    cares about and the defaults stay in one place.
    """
    sources = {k: kwargs.pop(k) for k in ("document", "chaptered", "text") if k in kwargs}
    options = CourseOptions(**kwargs) if kwargs else None
    return asyncio.run(P.build_course(None, Config(), options=options, **sources))


def report(course, stage: Stage):
    return next(r for r in course.stages if r.stage is stage)


# --- the failure policy, which is the whole reason this layer exists -------------


def test_one_stage_failing_does_not_lose_the_others(monkeypatch):
    """The case a classroom actually hits. A document that cannot support a question
    must still yield its lesson and its summary."""
    all_good(monkeypatch)
    stub(monkeypatch, "generate_assessment", raises=RuntimeError("nothing groundable"))

    course = build(document=make_document())

    assert course.lesson is not None
    assert course.insights is not None
    assert course.assessment is None
    assert report(course, Stage.ASSESSMENT).outcome is StageOutcome.FAILED


def test_a_failure_says_what_a_teacher_can_act_on(monkeypatch):
    """A bare "failed" is worse than nothing: it tells them to retry something that
    will fail identically."""
    all_good(monkeypatch)
    stub(monkeypatch, "generate_assessment", raises=RuntimeError("no passage supports a question"))

    course = build(document=make_document())

    assert "no passage supports a question" in report(course, Stage.ASSESSMENT).detail


def test_an_unexpected_error_is_caught_like_any_other(monkeypatch):
    """The narrow set of known errors is knowable today and will not stay knowable.
    A stage reaching a model, a parser and a template can fail in ways nobody listed,
    and the policy has to hold for those too."""
    all_good(monkeypatch)
    stub(monkeypatch, "lesson_from_document", raises=KeyError("something nobody predicted"))

    course = build(document=make_document())

    assert report(course, Stage.LESSON).outcome is StageOutcome.FAILED
    assert course.insights is not None


def test_cancellation_still_stops_the_build(monkeypatch):
    """`Exception` and not `BaseException`: a cancelled request must not be recorded
    as a stage that politely failed."""
    all_good(monkeypatch)
    stub(monkeypatch, "lesson_from_document", raises=KeyboardInterrupt())
    document = make_document()

    with pytest.raises(KeyboardInterrupt):
        build(document=document)


# --- absence must never be confused with failure ---------------------------------


def test_every_stage_reports_even_the_ones_not_asked_for(monkeypatch):
    """A report that listed only problems would make "nothing went wrong" and
    "nothing ran" look identical."""
    all_good(monkeypatch)
    course = build(document=make_document())
    assert {r.stage for r in course.stages} == {
        Stage.INSIGHTS, Stage.NARRATION, Stage.LESSON, Stage.ASSESSMENT
    }


def test_not_asked_for_is_skipped_not_failed(monkeypatch):
    all_good(monkeypatch)
    course = build(document=make_document(), with_assessment=False
    )
    assert report(course, Stage.ASSESSMENT).outcome is StageOutcome.SKIPPED
    assert report(course, Stage.ASSESSMENT).detail == "not requested"


def test_a_course_is_complete_when_nothing_asked_for_failed(monkeypatch):
    """Complete is not "everything ran". A caller who did not want an assessment has
    a complete course without one."""
    all_good(monkeypatch)
    course = build(document=make_document(), with_assessment=False
    )
    assert course.is_complete is True


def test_a_course_with_a_failed_stage_is_not_complete(monkeypatch):
    all_good(monkeypatch)
    stub(monkeypatch, "generate_assessment", raises=RuntimeError("no"))
    course = build(document=make_document())
    assert course.is_complete is False


def test_produced_lists_only_what_is_really_on_the_course(monkeypatch):
    all_good(monkeypatch)
    stub(monkeypatch, "generate_narration", raises=RuntimeError("no"))
    course = build(document=make_document())
    assert Stage.NARRATION not in course.produced
    assert Stage.LESSON in course.produced


# --- the sources, and what each can honestly support ------------------------------


def test_text_cannot_be_summarised_and_says_so_structurally(monkeypatch):
    """"Not requested" would tell a teacher they had a choice they do not have."""
    stub(monkeypatch, "lesson_from_text", result=make_lesson())
    course = build(text="Some notes.", with_insights=True, with_assessment=True)
    assert report(course, Stage.INSIGHTS).outcome is StageOutcome.SKIPPED
    assert "parsed document" in report(course, Stage.INSIGHTS).detail
    assert "parsed document" in report(course, Stage.ASSESSMENT).detail


def test_exactly_one_source_is_required(monkeypatch):
    all_good(monkeypatch)
    document = make_document()
    with pytest.raises(ValueError):
        build(document=document, text="also this")


def test_no_source_at_all_is_refused(monkeypatch):
    with pytest.raises(ValueError):
        build()


# --- what the course says about itself --------------------------------------------


def test_an_explicit_title_is_never_replaced_by_a_generated_one(monkeypatch):
    all_good(monkeypatch)
    course = build(document=make_document(), title="My Own Title"
    )
    assert course.title == "My Own Title"


def test_the_lessons_title_is_used_when_the_caller_gave_none(monkeypatch):
    all_good(monkeypatch)
    stub(monkeypatch, "lesson_from_document", result=make_lesson(title="The Water Cycle"))
    course = build(document=make_document())
    assert course.title == "The Water Cycle"


def test_warnings_say_which_stage_they_came_from(monkeypatch):
    """Four modules can each say "the model returned nothing for step 2". Unprefixed,
    a merged list is unreadable."""
    all_good(monkeypatch)
    stub(monkeypatch, "lesson_from_document",
         result=make_lesson(warnings=["the model returned nothing for step 2"]))
    course = build(document=make_document())
    assert any(w.startswith("microlesson:") for w in course.warnings)


# --- the transcript source, and the fallbacks nothing else reaches ----------------


def test_a_chaptered_recording_is_a_valid_source(monkeypatch):
    """The third of the three sources Module D accepts. A course inherits them rather
    than inventing a fourth, so this path has to work as well as the document one."""
    class Chaptered:
        transcript_id = "t-9"
        chapters = [object(), object()]

    stub(monkeypatch, "lesson_from_transcript", result=make_lesson())
    course = build(chaptered=Chaptered())

    assert course.lesson is not None
    assert course.source.kind == "transcript"
    assert course.source.unit_count == 2
    assert report(course, Stage.INSIGHTS).outcome is StageOutcome.SKIPPED


def test_the_course_reuses_the_id_the_source_already_carries(monkeypatch):
    """Minting a second id would give one upload two identities, and the one that
    correlates with anything else is the one already on the document."""
    stub(monkeypatch, "lesson_from_document", result=make_lesson())
    stub(monkeypatch, "generate_insights", result=make_insights())
    stub(monkeypatch, "generate_narration", result=make_narration())
    stub(monkeypatch, "generate_assessment", result=make_assessment())

    document = make_document()
    object.__setattr__(document, "__dict__", {**document.__dict__, "document_id": "doc-77"})
    assert build(document=document).course_id == "doc-77"


def test_a_text_course_still_gets_a_stable_id(monkeypatch):
    """Pasted notes carry no id of their own, so one is derived from the text — the
    same notes give the same id rather than a new one on every build."""
    stub(monkeypatch, "lesson_from_text", result=make_lesson())
    first = build(text="The water cycle.").course_id
    second = build(text="The water cycle.").course_id
    assert first == second
    assert first.startswith("course-")


def test_a_course_falls_back_to_a_plain_title_when_nothing_names_it(monkeypatch):
    """Every candidate can be blank: no title given, a lesson whose own title is
    whitespace, and a document with no filename. It must still be a valid course."""
    stub(monkeypatch, "lesson_from_text", result=make_lesson(title="   "))
    assert build(text="notes").title == "Course"
