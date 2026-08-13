"""A `MicroLesson` as a standalone HTML5 deck.

The claim this format makes is "open it anywhere, with nothing else". So the tests
that matter are the ones that would catch that claim quietly becoming false: a
stylesheet that slipped in, a font from a CDN, a script tag pointing outward. Each
of those still *looks* fine on the machine that built it and breaks on a school
laptop behind a filter.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from app.microlesson.emit import EmptyLessonError, emit_html5
from app.microlesson.schema import LessonStep, MicroLesson


def make_lesson(steps: list[LessonStep] | None = None, **kw) -> MicroLesson:
    defaults = dict(
        lesson_id="lesson-1",
        source={"kind": "text"},
        title="The Water Cycle",
        generator="test",
        model="m",
        generated_at=datetime.now(timezone.utc),
        steps=steps
        if steps is not None
        else [
            LessonStep(index=1, title="Evaporation", bullets=["The sun heats the ocean"],
                       notes="The sun drives the cycle.", source_index=1),
            LessonStep(index=2, title="Condensation", bullets=["Vapour cools"], notes="", source_index=2),
        ],
    )
    defaults.update(kw)
    return MicroLesson(**defaults)


def html_of(lesson: MicroLesson) -> str:
    return emit_html5(lesson).content.decode("utf-8")


# --- the promise: nothing is fetched ----------------------------------------------


def test_nothing_is_loaded_from_anywhere():
    """The whole reason this format exists next to the other two."""
    html = html_of(make_lesson())
    refs = re.findall(r'(?:src|href)="(?!#)([^"]+)"', html)
    assert refs == [], f"the deck reaches out to {refs}"


def test_there_is_no_url_anywhere_in_the_document():
    """Catches a background-image or @import that the attribute scan would miss."""
    html = html_of(make_lesson())
    assert "http://" not in html
    assert "https://" not in html
    assert "url(" not in html


def test_the_deck_is_one_file():
    package = emit_html5(make_lesson())
    assert package.filename.endswith(".html")
    assert package.content  # and nothing beside it


# --- what a reader sees -----------------------------------------------------------


def test_every_step_becomes_a_slide():
    html = html_of(make_lesson())
    assert html.count('class="slide"') == make_lesson().step_count


def test_objectives_add_an_opening_slide():
    lesson = make_lesson(objectives=["Explain the cycle"])
    assert html_of(lesson).count('class="slide"') == lesson.step_count + 1


def test_the_notes_are_present_but_folded_away():
    """A details element, so they open with no script and print open."""
    html = html_of(make_lesson())
    assert "<summary>Teacher notes</summary>" in html
    assert "The sun drives the cycle." in html


def test_a_step_without_notes_gets_no_empty_disclosure():
    assert html_of(make_lesson()).count("<summary>Teacher notes</summary>") == 1


def test_it_still_reads_with_scripting_switched_off():
    """The `no-js` class keeps every slide visible until the script removes it, so a
    browser with JavaScript disabled shows the lesson rather than a blank page."""
    html = html_of(make_lesson())
    assert 'class="no-js"' in html
    assert ".no-js .slide{display:block}" in html


def test_it_announces_slide_changes_to_a_screen_reader():
    html = html_of(make_lesson())
    assert 'aria-live="polite"' in html


def test_it_prints_as_a_handout():
    """Every slide on the page and the navigation gone, so "save as PDF" is useful."""
    html = html_of(make_lesson())
    assert "@media print" in html
    assert "page-break-after" in html


# --- what must never reach a browser ----------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "<script>alert(1)</script>"),
        ("bullets", "<img src=x onerror=alert(1)>"),
        ("notes", "</p><script>alert(1)</script>"),
    ],
)
def test_markup_from_the_document_cannot_execute(field, value):
    kw = {"index": 1, "title": "T", "bullets": ["b"], "notes": "n"}
    kw[field] = [value] if field == "bullets" else value
    html = html_of(make_lesson(steps=[LessonStep(**kw)]))
    assert "<script>alert" not in html
    assert "<img src=x" not in html
    assert "&lt;" in html


def test_the_lesson_title_is_escaped_in_the_page_title():
    lesson = make_lesson(title="Cats <script>x</script>")
    html = html_of(lesson)
    assert "<title>Cats &lt;script&gt;" in html


# --- refusals ---------------------------------------------------------------------


def test_a_lesson_with_nothing_to_show_is_refused():
    lesson = make_lesson(steps=[LessonStep(index=1, title="  ", bullets=[""], notes="")])
    with pytest.raises(EmptyLessonError):
        emit_html5(lesson)


def test_the_same_lesson_renders_identically_twice():
    lesson = make_lesson()
    first = emit_html5(lesson).content
    second = emit_html5(lesson).content
    assert first == second


def test_the_filename_comes_from_the_title():
    assert emit_html5(make_lesson()).filename == "The-Water-Cycle.html"


# --- the two holes the second audit pass found ------------------------------------


def test_the_language_cannot_break_out_of_its_attribute():
    """`escape_text` leaves quotes alone — right for a text node, wrong for an
    attribute, and the deck has exactly one attribute carrying caller input.

    A first version used it here anyway, and a language of `" onload="alert(1)`
    produced a working handler on the html element, reachable from a query
    parameter. All the other tests passed. The fix enforces the shape a language
    tag has rather than escaping, so a quote cannot be expressed at all.
    """
    html = html_of(make_lesson(language='" onload="alert(1)'))
    assert "onload=" not in html
    assert '<html lang="und"' in html


@pytest.mark.parametrize("tag,expected", [("en", "en"), ("hi", "hi"), ("es-419", "es"), ("", "en")])
def test_a_real_language_tag_still_survives(tag, expected):
    """The guard must not be so blunt that it throws away legitimate tags.

    `es-419` keeps its primary subtag rather than becoming `und`, and an empty
    value falls back to `en` before the guard ever sees it — `und` is reserved for
    input that is present and unusable, which is the case worth distinguishing.
    """
    assert f'<html lang="{expected}"' in html_of(make_lesson(language=tag))


def test_a_step_with_a_heading_and_no_points_still_gets_a_slide():
    """One of two branches the coverage report showed unexercised. A summary step
    with only a heading is a real thing a model produces."""
    lesson = make_lesson(steps=[LessonStep(index=1, title="In summary", bullets=[], notes="")])
    html = html_of(lesson)
    assert html.count('class="slide"') == 1
    assert "In summary" in html
    assert "<ul>" not in html


def test_a_step_with_points_and_no_heading_still_gets_a_slide():
    """The other branch. The contract requires a non-empty title, but a title of
    only whitespace passes it and reaches here as an empty heading."""
    lesson = make_lesson(steps=[LessonStep(index=1, title="  ", bullets=["A point"], notes="")])
    html = html_of(lesson)
    assert html.count('class="slide"') == 1
    assert "A point" in html
    assert "<h2>" not in html


def test_devanagari_survives_and_the_language_is_declared():
    """Written as UTF-8 with the charset declared, so a browser opening the file
    from a pen drive renders it rather than showing mojibake."""
    lesson = make_lesson(
        title="जल चक्र", language="hi",
        steps=[LessonStep(index=1, title="वाष्पीकरण", bullets=["सूर्य समुद्र को गर्म करता है"], notes="")],
    )
    html = html_of(lesson)
    assert "वाष्पीकरण" in html
    assert '<html lang="hi"' in html
    assert '<meta charset="utf-8">' in html


def test_the_biggest_lesson_the_contract_allows_still_renders():
    """40 steps is the cap. Worth one test, because a deck that is fine at three
    slides and unusable at forty is a thing that ships."""
    from app.microlesson.schema import MAX_STEPS

    lesson = make_lesson(steps=[
        LessonStep(index=i, title=f"Step {i}", bullets=[f"Point {i}"], notes=f"Note {i}")
        for i in range(1, MAX_STEPS + 1)
    ])
    html = html_of(lesson)
    assert html.count('class="slide"') == MAX_STEPS
