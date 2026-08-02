"""The micro-lesson builder: how a source becomes a lesson, and what it refuses to do.

The interesting tests here are about *structure*, not prose. The lesson's shape is
computed in Python, so it is exactly testable: the same source must always produce
the same number of steps, in the same order, each pointing at the unit it came
from. What the model writes inside a step is not asserted, because it should not be.

The one model call runs against a mocked gateway, so these stay offline.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from app.chaptering.schema import Chapter, ChapteredTranscript
from app.ingestion.schema import Block, BlockKind, Page, ParsedDocument, SourceInfo
from app.microlesson.pipeline import (
    lesson_from_document,
    lesson_from_text,
    lesson_from_transcript,
    sections_from_text,
    sections_from_transcript,
)
from app.microlesson.schema import LessonStep, MicroLesson
from app.summarization.pipeline import EmptyDocumentError, GenerationConfig
from app.transcription.schema import TranscriptSource

BODY_A = "Water moves between the oceans, the atmosphere and the land continuously."
BODY_B = "Energy from the sun heats the ocean surface until molecules escape as gas."
BODY_C = "Rising vapour cools and gathers around particles of dust to make clouds."


def _config() -> GenerationConfig:
    return GenerationConfig(
        base_url="https://gateway/v1",
        api_key="k",
        model="m",
        provider="test",
        temperature=0.0,
        max_source_chars=24000,
    )


def _reply(steps: list[dict], objectives: list[str] | None = None) -> httpx.Response:
    content = json.dumps({"objectives": objectives or ["Explain the cycle"], "steps": steps})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _good_steps(n: int) -> list[dict]:
    return [
        {"index": i, "title": f"Model title {i}", "bullets": [f"Point {i}"], "notes": f"Notes {i}"}
        for i in range(1, n + 1)
    ]


def _run(coro_factory, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0))

    async def go():
        try:
            return await coro_factory(client)
        finally:
            await client.aclose()

    return asyncio.run(go())


def _text_lesson(text: str, steps: list[dict] | None = None, **kw) -> MicroLesson:
    n = len(sections_from_text(text))
    handler = lambda request: _reply(steps if steps is not None else _good_steps(n))  # noqa: E731
    return _run(lambda c: lesson_from_text(c, text, _config(), **kw), handler)


def _doc(pages: list[Page], fmt: str = "pdf") -> ParsedDocument:
    return ParsedDocument(
        source=SourceInfo(filename="lesson." + fmt, format=fmt, page_count=len(pages), title="Deck"),
        parser="test",
        parser_version="1.0",
        parsed_at=datetime.now(timezone.utc),
        pages=pages,
    )


def _page(index: int, heading: str, body: str, kind: str = "page") -> Page:
    return Page(
        index=index,
        kind=kind,
        blocks=[
            Block(kind=BlockKind.heading, text=heading, level=1),
            Block(kind=BlockKind.paragraph, text=body),
        ],
    )


# --- the source selector: three inputs, one shape --------------------------------


def test_a_document_becomes_one_step_per_section():
    lesson = _run(
        lambda c: lesson_from_document(c, _doc([_page(1, "Evaporation", BODY_A)]), _config()),
        lambda request: _reply(_good_steps(1)),
    )
    assert lesson.source.kind == "document"
    assert lesson.step_count == 1
    assert lesson.steps[0].source_index == 1


def test_a_transcript_becomes_one_step_per_chapter():
    chaptered = ChapteredTranscript(
        source=TranscriptSource(filename="talk.mp4", media_seconds=200.0),
        generator="test",
        model="m",
        generated_at=datetime.now(timezone.utc),
        chapters=[
            Chapter(index=1, start=0.0, end=100.0, title="Evaporation", text=BODY_A),
            Chapter(index=2, start=100.0, end=200.0, title="Condensation", text=BODY_C),
        ],
    )
    lesson = _run(
        lambda c: lesson_from_transcript(c, chaptered, _config()),
        lambda request: _reply(_good_steps(2)),
    )
    assert lesson.source.kind == "transcript"
    assert [s.source_index for s in lesson.steps] == [1, 2]
    assert [s.title for s in lesson.steps] == ["Evaporation", "Condensation"]


def test_pasted_text_becomes_one_step_per_block():
    lesson = _text_lesson(f"{BODY_A}\n\n{BODY_B}\n\n{BODY_C}")
    assert lesson.source.kind == "text"
    assert lesson.step_count == 3


def test_a_chapter_with_no_text_contributes_no_step():
    chaptered = ChapteredTranscript(
        source=TranscriptSource(filename="talk.mp4"),
        generator="test",
        model="m",
        generated_at=datetime.now(timezone.utc),
        chapters=[
            Chapter(index=1, start=0.0, end=10.0, title="Real", text=BODY_A),
            Chapter(index=2, start=10.0, end=20.0, title="Empty", text="   "),
        ],
    )
    assert len(sections_from_transcript(chaptered)) == 1


# --- how pasted text is split ----------------------------------------------------


def test_a_heading_on_its_own_line_belongs_to_the_block_beneath_it():
    sections = sections_from_text(f"Evaporation\n{BODY_A}")
    assert len(sections) == 1
    assert sections[0].title == "Evaporation"
    assert "Evaporation" not in sections[0].text


def test_a_heading_standing_alone_belongs_to_the_next_block():
    """The commonest way people write notes, and the case that used to be lost.

    A lone heading is below the minimum length for a step, so handling only the
    inline convention dropped it entirely — the lesson silently lost the author's
    section titles and the model invented replacements.
    """
    sections = sections_from_text(f"Evaporation\n\n{BODY_A}\n\nCondensation\n\n{BODY_C}")
    assert [s.title for s in sections] == ["Evaporation", "Condensation"]
    assert [s.source_index for s in sections] == [1, 2]


def test_a_trailing_heading_becomes_no_step_of_its_own():
    """There is nothing for it to head, so it is ordinary text — and then too short
    to teach from. A lesson should not end on a step whose only content is its own
    title."""
    lesson = _text_lesson(f"{BODY_A}\n\nFurther reading")
    assert lesson.step_count == 1


def test_a_sentence_is_not_mistaken_for_a_heading():
    sections = sections_from_text(f"{BODY_A}\n\n{BODY_B}")
    assert [s.title for s in sections] == [None, None]


def test_section_numbers_are_contiguous_after_headings_are_folded_in():
    """Folding a heading into the next block must not leave a gap in the numbering,
    because `source_index` is what a reviewer traces a step back through."""
    sections = sections_from_text(f"One\n\n{BODY_A}\n\nTwo\n\n{BODY_B}\n\nThree\n\n{BODY_C}")
    assert [s.source_index for s in sections] == [1, 2, 3]


# --- what the model may and may not decide ---------------------------------------


def test_the_authors_heading_wins_over_the_models():
    """Retitling a section the author already named is a change nobody asked for."""
    lesson = _text_lesson(f"Evaporation\n\n{BODY_A}")
    assert lesson.steps[0].title == "Evaporation"


def test_the_models_heading_is_used_when_the_source_has_none():
    lesson = _text_lesson(BODY_A)
    assert lesson.steps[0].title == "Model title 1"


def test_a_step_the_model_skipped_falls_back_to_the_source_and_warns():
    """A missing step must not silently shorten the lesson."""
    text = f"{BODY_A}\n\n{BODY_B}\n\n{BODY_C}"
    lesson = _text_lesson(text, steps=[_good_steps(3)[0], _good_steps(3)[2]])
    assert lesson.step_count == 3
    assert BODY_B[:30] in lesson.steps[1].bullets[0]
    assert any("step(s) 2" in w for w in lesson.warnings)


def test_a_step_invented_for_a_section_that_does_not_exist_is_discarded():
    """A step with no source behind it is one nobody can check."""
    lesson = _text_lesson(
        BODY_A,
        steps=_good_steps(1) + [{"index": 9, "title": "Invented", "bullets": ["Nothing"]}],
    )
    assert lesson.step_count == 1
    assert any("invented" in w and "9" in w for w in lesson.warnings)


def test_the_model_cannot_change_how_many_steps_there_are():
    text = f"{BODY_A}\n\n{BODY_B}\n\n{BODY_C}"
    for returned in ([], _good_steps(1), _good_steps(3), _good_steps(8)):
        lesson = _text_lesson(text, steps=returned)
        assert lesson.step_count == 3, f"model returned {len(returned)} steps"


def test_an_unusable_reply_still_produces_a_whole_lesson():
    text = f"{BODY_A}\n\n{BODY_B}"
    lesson = _run(
        lambda c: lesson_from_text(c, text, _config()),
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
    )
    assert lesson.step_count == 2
    assert any("Could not generate" in w for w in lesson.warnings)


# --- titles, bounds and refusals -------------------------------------------------


def test_an_explicit_title_is_never_overridden():
    lesson = _text_lesson(f"Evaporation\n\n{BODY_A}", title="  My Own Lesson  ")
    assert lesson.title == "My Own Lesson"


def test_the_title_falls_back_to_the_sources_own_heading():
    lesson = _text_lesson(f"Evaporation\n\n{BODY_A}")
    assert lesson.title == "Evaporation"


def test_a_section_with_almost_nothing_in_it_is_dropped():
    """The stub ends in a full stop on purpose.

    Written as a bare "ok" this test passed without ever reaching the length
    filter: a lone short line is heading-shaped, so it was carried onto the next
    block as its title and no third section was ever built. Setting the minimum to
    zero left it green, which is the tell. Punctuating it keeps it an ordinary
    section, so the drop is what the assertion is actually measuring.
    """
    text = f"{BODY_A}\n\nok.\n\n{BODY_B}"
    assert len(sections_from_text(text)) == 3, "the stub must survive splitting to be dropped later"
    lesson = _text_lesson(text)
    assert lesson.step_count == 2


def test_a_source_with_nothing_to_teach_is_refused():
    with pytest.raises(EmptyDocumentError):
        _text_lesson("hi\n\nok")


def test_the_source_records_how_many_units_it_offered():
    lesson = _text_lesson(f"{BODY_A}\n\n{BODY_B}\n\n{BODY_C}")
    assert lesson.source.unit_count == 3


# --- the contract ----------------------------------------------------------------


def _lesson(steps: list[LessonStep]) -> MicroLesson:
    return MicroLesson(
        lesson_id="l-1",
        source={"kind": "text"},
        title="T",
        generator="test",
        model="m",
        generated_at=datetime.now(timezone.utc),
        steps=steps,
    )


def test_steps_must_be_numbered_without_gaps():
    """A packaged lesson is navigated by position, so a gap reorders the slides."""
    with pytest.raises(ValidationError):
        _lesson([LessonStep(index=1, title="A"), LessonStep(index=3, title="C")])


def test_steps_must_not_repeat_a_number():
    with pytest.raises(ValidationError):
        _lesson([LessonStep(index=1, title="A"), LessonStep(index=1, title="B")])


def test_steps_numbered_in_order_are_accepted():
    lesson = _lesson([LessonStep(index=1, title="A"), LessonStep(index=2, title="B")])
    assert lesson.step_count == 2


def test_a_step_must_have_a_title():
    with pytest.raises(ValidationError):
        LessonStep(index=1, title="")


def test_a_mistyped_field_is_refused():
    with pytest.raises(ValidationError):
        LessonStep(index=1, title="A", bullet=["one"])


def test_a_lesson_can_be_read_back_from_its_own_output():
    """The strictness on the parts and the leniency on the whole are deliberate, and
    this is the asymmetry that pays for it: `step_count` is serialised but not
    accepted, so forbidding extras on `MicroLesson` would make the engine reject a
    lesson it produced itself the moment one is POSTed to a packaging endpoint."""
    lesson = _lesson([LessonStep(index=1, title="A")])
    assert MicroLesson.model_validate(lesson.model_dump(mode="json")).step_count == 1
