"""A `MicroLesson` as an H5P Course Presentation.

These open the emitted package and look inside, because that is the only honest
test for a format whose validator drops unknown keys without raising. A test that
asserts "we wrote something" passes just as happily on a field the player will
never read.

The field names asserted here are real ones from `H5P.CoursePresentation-1.26`'s
own `semantics.json`, and the behaviours asserted (a comment button appearing, a
coordinate being a percentage) were read out of its shipped runtime.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

import pytest

from app.microlesson.emit import EmptyLessonError, emit_h5p
from app.microlesson.schema import LessonStep, MicroLesson
from app.packaging.h5p import (
    ADVANCED_TEXT,
    ALLOWED_SLIDE_ELEMENT_LIBRARIES,
    COURSE_PRESENTATION,
    COURSE_PRESENTATION_CLOSURE,
    library_string,
)


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
            LessonStep(
                index=1,
                title="Evaporation",
                bullets=["The sun heats the ocean", "Molecules escape as vapour"],
                notes="The sun's energy drives the whole cycle.",
                source_index=1,
            ),
            LessonStep(
                index=2,
                title="Condensation",
                bullets=["Vapour cools as it rises"],
                notes="",
                source_index=2,
            ),
        ],
    )
    defaults.update(kw)
    return MicroLesson(**defaults)


def content_of(lesson: MicroLesson) -> dict:
    """Unzip the package and read the content the player will actually run."""
    package = emit_h5p(lesson)
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    return json.loads(archive.read("content/content.json"))


def manifest_of(lesson: MicroLesson) -> dict:
    package = emit_h5p(lesson)
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    return json.loads(archive.read("h5p.json"))


def slides_of(lesson: MicroLesson) -> list[dict]:
    return content_of(lesson)["presentation"]["slides"]


# --- the shape the lesson dictates ------------------------------------------------


def test_every_step_becomes_one_slide():
    """The lesson decided how many steps there are. The emitter does not get a vote."""
    lesson = make_lesson()
    slides = slides_of(lesson)
    # Two steps plus no objectives on this fixture.
    assert len(slides) == lesson.step_count


def test_objectives_lead_with_their_own_slide():
    lesson = make_lesson(objectives=["Explain the cycle", "Name the stages"])
    slides = slides_of(lesson)
    assert len(slides) == lesson.step_count + 1
    first = json.dumps(slides[0])
    assert "What you will be able to do" in first
    assert "Explain the cycle" in first


def test_a_lesson_with_no_objectives_gets_no_empty_opening_slide():
    """An objectives slide with nothing under it reads as a mistake, not a design."""
    assert len(slides_of(make_lesson(objectives=[]))) == make_lesson().step_count


def test_the_slide_order_follows_the_step_order():
    slides = slides_of(make_lesson())
    titles = [json.dumps(s) for s in slides]
    assert "Evaporation" in titles[0]
    assert "Condensation" in titles[1]


# --- the fields H5P actually declares ---------------------------------------------


def test_each_element_names_a_library_the_format_allows():
    """`action.library` is matched literally against the list in the semantics."""
    for slide in slides_of(make_lesson()):
        for element in slide["elements"]:
            assert element["action"]["library"] in ALLOWED_SLIDE_ELEMENT_LIBRARIES


def test_text_is_carried_by_the_advanced_text_library():
    element = slides_of(make_lesson())[0]["elements"][0]
    assert element["action"]["library"] == library_string(ADVANCED_TEXT)
    assert "text" in element["action"]["params"]


def test_geometry_is_expressed_in_percent():
    """The runtime does `left: e.x + "%"`, so anything over 100 leaves the slide."""
    for slide in slides_of(make_lesson()):
        for element in slide["elements"]:
            for field in ("x", "y", "width", "height"):
                assert 0 <= element[field] <= 100, f"{field} is not a usable percentage"
            assert element["x"] + element["width"] <= 100
            assert element["y"] + element["height"] <= 100


def test_the_manifest_names_the_course_presentation_and_ships_its_whole_closure():
    manifest = manifest_of(make_lesson())
    assert manifest["mainLibrary"] == COURSE_PRESENTATION[0]
    declared = {(d["machineName"], d["majorVersion"], d["minorVersion"]) for d in manifest["preloadedDependencies"]}
    assert declared == set(COURSE_PRESENTATION_CLOSURE)


def test_the_closure_includes_the_library_that_arrives_indirectly():
    """H5P.Transition is not a declared dependency of Course Presentation.

    It arrives underneath H5P.JoubelUI, so a closure built from the top-level list
    would omit it and the package would install cleanly and then misbehave. This
    asserts the transitive walk, not the shortcut.
    """
    manifest = manifest_of(make_lesson())
    names = {d["machineName"] for d in manifest["preloadedDependencies"]}
    assert "H5P.Transition" in names
    assert "H5P.JoubelUI" in names


# --- the notes decision -----------------------------------------------------------


def test_the_spoken_notes_become_the_elements_comment():
    """Course Presentation has no notes field. `solution` is the real one, and the
    runtime turns it into a button for any element that carries it."""
    body = slides_of(make_lesson())[0]["elements"][1]
    # The apostrophe stays as written: `escape_text` uses quote=False because these
    # are text nodes, not attribute values, and escaping quotes only hurts readability.
    assert body["solution"] == "The sun's energy drives the whole cycle."


def test_a_step_without_notes_carries_no_comment_at_all():
    """The runtime guards on the field being truthy, so an empty string would still
    be falsy — but omitting it keeps the emitted content honest about what is there."""
    body = slides_of(make_lesson())[1]["elements"][1]
    assert "solution" not in body


def test_a_step_with_notes_actually_builds_the_comment_button():
    """The test that a first version got exactly backwards.

    `alwaysDisplayComments` does not mean "show the comment text". It is the only
    thing that builds the button at all::

        void 0 !== e.alwaysDisplayComments && e.alwaysDisplayComments
            && t.showCPComments()

    The first version asserted this was False, which passed, shipped notes into the
    package, and made them unreachable in the player — the other two callers are
    both on the show-solutions path, which a lesson never offers. Caught by opening
    the package in a real H5P player and finding zero buttons on the slide.
    """
    body = slides_of(make_lesson())[0]["elements"][1]
    assert body["solution"]
    assert body["alwaysDisplayComments"] is True


def test_a_step_without_notes_sets_no_comment_flag_either():
    """No comment, no flag. Carrying the flag alone would ask the player to build a
    button for text that is not there."""
    body = slides_of(make_lesson())[1]["elements"][1]
    assert "solution" not in body
    assert "alwaysDisplayComments" not in body


# --- what must never reach a tenant's LMS -----------------------------------------


def test_markup_in_a_bullet_is_escaped():
    """AdvancedText injects `text` as HTML, and the text came from an upload."""
    lesson = make_lesson(
        steps=[LessonStep(index=1, title="Safe", bullets=["<script>alert(1)</script>"], notes="")]
    )
    payload = json.dumps(content_of(lesson))
    assert "<script>" not in payload
    assert "&lt;script&gt;" in payload


def test_no_model_text_reaches_the_package_unescaped():
    """Covers the on-screen heading *and* the subcontent label, which is the one a
    first pass got wrong: the label went in raw because it is a different field with
    a different rule, so a test that only checked the visible text passed."""
    lesson = make_lesson(steps=[LessonStep(index=1, title="<img src=x onerror=1>", bullets=["a"], notes="")])
    payload = json.dumps(content_of(lesson))
    assert "<img" not in payload
    assert "&lt;img" in payload


def test_markup_in_the_notes_is_escaped():
    lesson = make_lesson(
        steps=[LessonStep(index=1, title="T", bullets=["a"], notes="<script>x</script>")]
    )
    payload = json.dumps(content_of(lesson))
    assert "<script>" not in payload


# --- refusals and edges -----------------------------------------------------------


def test_a_lesson_whose_every_step_is_blank_is_refused():
    """Rather than emitting a presentation of empty slides that imports fine."""
    lesson = make_lesson(steps=[LessonStep(index=1, title="   ", bullets=["  ", ""], notes="")])
    with pytest.raises(EmptyLessonError):
        emit_h5p(lesson)


def test_a_step_with_a_title_and_no_points_still_gets_its_slide():
    lesson = make_lesson(steps=[LessonStep(index=1, title="Summary", bullets=[], notes="")])
    slides = slides_of(lesson)
    assert len(slides) == 1
    assert len(slides[0]["elements"]) == 1


def test_blank_bullets_do_not_become_empty_list_items():
    lesson = make_lesson(steps=[LessonStep(index=1, title="T", bullets=["real", "  ", ""], notes="")])
    text = slides_of(lesson)[0]["elements"][1]["action"]["params"]["text"]
    assert text.count("<li>") == 1


def test_the_summary_slide_is_switched_off():
    """A lesson has no questions, so a slide scoring nothing out of nothing is noise."""
    assert content_of(make_lesson())["override"]["hideSummarySlide"] is True


def test_the_keyword_sidebar_is_switched_off():
    """We have no per-slide keywords, and an empty sidebar eats a third of the width."""
    assert content_of(make_lesson())["presentation"]["keywordListEnabled"] is False


# --- provenance and reproducibility -----------------------------------------------


def test_the_same_lesson_emits_byte_identical_packages():
    """Deterministic subcontent ids and fixed timestamps make the artefact testable."""
    lesson = make_lesson()
    # Two independent emissions, named rather than compared inline, so the assertion
    # reads as "these two runs agreed" and a failure prints which bytes differ.
    first = emit_h5p(lesson).content
    second = emit_h5p(lesson).content
    assert first == second


def test_two_elements_on_one_slide_have_different_subcontent_ids():
    """They are derived from the element's role, so a collision would mean two
    children of the same slide claiming one identity."""
    elements = slides_of(make_lesson())[0]["elements"]
    ids = {e["action"]["subContentId"] for e in elements}
    assert len(ids) == len(elements)


def test_the_filename_comes_from_the_lesson_title():
    assert emit_h5p(make_lesson()).filename == "The-Water-Cycle.h5p"


def test_warnings_from_the_lesson_travel_with_the_package():
    lesson = make_lesson(warnings=["The model returned nothing for step 2"])
    assert emit_h5p(lesson).warnings == ["The model returned nothing for step 2"]


def test_a_step_with_points_and_no_heading_still_gets_its_slide():
    """The contract requires a non-empty title, but a title of only whitespace
    satisfies that and arrives here as an empty heading. The slide is still worth
    emitting — the points are the content."""
    lesson = make_lesson(steps=[LessonStep(index=1, title="   ", bullets=["A real point"], notes="")])
    slides = slides_of(lesson)
    assert len(slides) == 1
    assert len(slides[0]["elements"]) == 1
    assert "A real point" in slides[0]["elements"][0]["action"]["params"]["text"]


def test_devanagari_survives_into_the_package():
    """The tenants this is built for teach in Hindi and Marathi. The package writes
    JSON with ensure_ascii=False for exactly this reason: escaping to \\uXXXX would
    bloat the file and destroy the readability that makes a generated artefact
    reviewable by the teacher whose document it came from."""
    lesson = make_lesson(
        title="जल चक्र",
        steps=[LessonStep(index=1, title="वाष्पीकरण", bullets=["सूर्य समुद्र को गर्म करता है"], notes="")],
    )
    payload = json.dumps(content_of(lesson), ensure_ascii=False)
    assert "वाष्पीकरण" in payload
    assert "\\u0935" not in payload


def test_notes_that_are_only_whitespace_build_no_button():
    """A comment of spaces would render an empty popup behind a button that looks
    like it offers something. The runtime's own guard checks the trimmed text, so
    this matches what it does rather than second-guessing it."""
    lesson = make_lesson(steps=[LessonStep(index=1, title="A", bullets=["b"], notes="   ")])
    body = slides_of(lesson)[0]["elements"][1]
    assert "solution" not in body
    assert "alwaysDisplayComments" not in body


# --- the title band, which used to cut headings in half ---------------------------
#
# An element's box is absolutely positioned with a fixed height and the runtime
# clips whatever overflows it — it does not scroll and it does not shrink the type.
# A single fixed title height therefore sliced any heading that wrapped, straight
# through the glyphs, and a heading is the one thing on a slide nobody can miss.
#
# These assert the properties that make that impossible rather than restating the
# constants back to themselves: a band that grows with the heading, a body that
# never starts underneath it, and a bottom edge that does not move.


def one_step(title: str, bullets: list[str] | None = None) -> MicroLesson:
    return make_lesson(steps=[LessonStep(index=1, title=title, bullets=bullets or ["A point"], notes="")])


def boxes(lesson: MicroLesson) -> tuple[dict, dict]:
    """The title box and the body box of the first slide."""
    elements = slides_of(lesson)[0]["elements"]
    assert len(elements) == 2, "this helper wants a slide with both a heading and a body"
    return elements[0], elements[1]


SHORT_TITLE = "Evaporation"
#: The heading from the report that started this: it wrapped to two lines and the
#: second was cut through the middle.
WRAPPED_TITLE = "The Water Cycle: How Water Moves Through Earth's Systems"
#: 113 characters. Three lines by the emitter's wrap width, and three lines in the
#: player too — this exact string was pasted into a running `H5P.AdvancedText` and
#: measured, rather than being assumed from the character count. Word wrapping
#: breaks earlier than a plain division does, which is the reason to check.
THREE_LINE_TITLE = (
    "The Water Cycle and Why It Matters: How Water Moves Between the Ocean, "
    "the Atmosphere and the Land, Over and Over"
)
RUNAWAY_TITLE = "A heading so long that no reasonable slide could ever hold it, " + ("and on it goes " * 12)


def test_a_heading_that_wraps_gets_a_taller_band_than_one_that_does_not():
    short, _ = boxes(one_step(SHORT_TITLE))
    wrapped, _ = boxes(one_step(WRAPPED_TITLE))
    assert wrapped["height"] > short["height"]


#: What the shipped `H5P.CoursePresentation-1.26` player does with an `h2`, read out
#: of a running instance with the developer tools: one line box is this much of the
#: slide's height, and the heading carries this much margin beneath it. Both are
#: ratios of the slide, and the player scales type with the slide, so they do not
#: move with the viewport.
#:
#: These live here rather than being imported from the emitter on purpose. A test
#: that checks the band against the emitter's own idea of a line height agrees with
#: it however wrong it is — the first version did exactly that, and passed against a
#: line height of 8.0 that clipped a three-line heading in the real player.
MEASURED_LINE_BOX_PCT = 9.26
MEASURED_HEADING_MARGIN_PCT = 3.09


def test_the_band_is_tall_enough_for_every_line_the_heading_wraps_to():
    """The bug itself, checked against the player rather than against ourselves."""
    # The runaway heading is deliberately absent: past the cap it *is* clipped, on
    # purpose, and that trade is asserted by its own test below.
    for title, lines in ((SHORT_TITLE, 1), (WRAPPED_TITLE, 2), (THREE_LINE_TITLE, 3)):
        band, _ = boxes(one_step(title))
        needed = lines * MEASURED_LINE_BOX_PCT + MEASURED_HEADING_MARGIN_PCT
        assert band["height"] >= needed, (
            f"{title[:40]!r}… wraps to {lines} lines needing {needed:.2f}% "
            f"and is given {band['height']}% — the player will clip it"
        )


def test_the_body_never_starts_underneath_the_heading():
    for title in (SHORT_TITLE, WRAPPED_TITLE, RUNAWAY_TITLE):
        band, body = boxes(one_step(title))
        assert body["y"] > band["y"] + band["height"], f"{title!r} has its points printed over its heading"


def test_the_body_ends_in_the_same_place_whatever_the_heading():
    """A taller heading takes room from the points, rather than pushing them off the
    bottom edge of the slide where the runtime would clip them instead."""
    bottoms = {
        round(body["y"] + body["height"], 6)
        for body in (boxes(one_step(t))[1] for t in (SHORT_TITLE, WRAPPED_TITLE, RUNAWAY_TITLE))
    }
    assert len(bottoms) == 1, f"the body's bottom edge moves: {sorted(bottoms)}"


def test_a_runaway_heading_cannot_eat_the_slide():
    """Past a few lines a heading stops being a heading. Clipping a word of it is
    the better failure: the points below it stay readable."""
    band, body = boxes(one_step(RUNAWAY_TITLE))
    assert band["height"] <= 40
    assert body["height"] >= 45


def test_escaping_does_not_inflate_the_band():
    """`Q&A` reaches the package as `Q&amp;A` — four characters longer, and still
    three on screen. Sizing the band off the escaped markup would leave a fat empty
    band above every heading carrying an ampersand or a quote.

    Both fixtures are chosen to straddle the wrap width: 34 raw characters is one
    line, and the 50 it escapes to is two. A first version of this test used a
    heading that stayed on one line either way, so it passed whichever length the
    band was measured from — it was found by the mutation surviving it.
    """
    plain, _ = boxes(one_step("Questions and answers about it"))
    escaped, _ = boxes(one_step("Q&A: acids & bases & salts & water"))
    assert plain["height"] == escaped["height"]


def test_a_slide_with_no_heading_gives_the_whole_area_to_its_points():
    """A title of only whitespace passes the contract and arrives here as an empty
    heading. Reserving a band for it would leave a strip of blank slide on top."""
    (body,) = slides_of(one_step("   "))[0]["elements"]
    _, with_heading = boxes(one_step(SHORT_TITLE))
    assert body["y"] < with_heading["y"]
    assert body["height"] > with_heading["height"]
    assert round(body["y"] + body["height"], 6) == round(with_heading["y"] + with_heading["height"], 6)


def test_the_objectives_slide_is_laid_out_by_the_same_rules():
    """It builds its own heading rather than taking one from the lesson, and an
    earlier version of this fix left it on the old fixed geometry."""
    band, body = boxes(make_lesson(objectives=["Explain the cycle"]))
    _, step_body = boxes(one_step(SHORT_TITLE))
    assert body["y"] > band["y"] + band["height"]
    assert round(body["y"] + body["height"], 6) == round(step_body["y"] + step_body["height"], 6)
