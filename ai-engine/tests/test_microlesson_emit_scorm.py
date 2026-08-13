"""A `MicroLesson` as a SCORM 1.2 course.

Two things are worth testing here and one is not. The package structure and the
manifest are worth it, because an LMS rejects a malformed one outright. The
reporting *behaviour* is worth it, and it is asserted by reading the script that
does the reporting rather than by re-implementing SCORM in Python — the script was
also driven end to end against a strict fake LMS during development, which is
where the session-time format and the completion transition were actually proved.

What is not worth testing is that the deck renders: the HTML5 suite already covers
that, and this target uses the identical renderer. Asserting it twice would only
create a second place to update.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime, timezone

import pytest

from app.microlesson.emit import EmptyLessonError, emit_scorm
from app.microlesson.emit.scorm import API_NAME, REPORTER_NAME
from app.microlesson.schema import LessonStep, MicroLesson
from app.packaging.scorm import LAUNCH_NAME, MANIFEST_NAME


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
            LessonStep(index=1, title="Evaporation", bullets=["The sun heats the ocean"], notes="n", source_index=1),
            LessonStep(index=2, title="Condensation", bullets=["Vapour cools"], notes="", source_index=2),
        ],
    )
    defaults.update(kw)
    return MicroLesson(**defaults)


def archive_of(lesson: MicroLesson) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(emit_scorm(lesson).content))


def reporter_source() -> str:
    return archive_of(make_lesson()).read(REPORTER_NAME).decode("utf-8")


def reporter_code() -> str:
    """The reporter with its comments stripped.

    A first version of the no-score test asserted against the raw file and failed
    on the comment *explaining* why there is no score. Asserting on prose is worse
    than not asserting: it fails when the reasoning is documented well and passes
    when the documentation is deleted. Only the code is evidence.
    """
    source = reporter_source()
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


# --- the package an LMS will open -------------------------------------------------


def test_the_package_holds_exactly_what_a_sco_needs():
    names = sorted(archive_of(make_lesson()).namelist())
    assert names == sorted([MANIFEST_NAME, LAUNCH_NAME, API_NAME, REPORTER_NAME])


def test_there_are_no_directory_entries():
    """A bare `scorm/` entry is enough for some importers to reject the package."""
    for info in archive_of(make_lesson()).infolist():
        assert not info.filename.endswith("/")


def test_the_manifest_lists_every_file():
    manifest = archive_of(make_lesson()).read(MANIFEST_NAME).decode()
    for name in (LAUNCH_NAME, API_NAME, REPORTER_NAME):
        assert name in manifest


def test_the_manifest_declares_no_mastery_score():
    """There is no score at all in a lesson, so a threshold to compare against would
    have an LMS inventing a verdict out of nothing. ADR-0005 records the same call
    for the assessment package, where it merely caused disagreement between LMSs."""
    assert "masteryscore" not in archive_of(make_lesson()).read(MANIFEST_NAME).decode()


def test_the_launch_page_loads_the_api_before_the_reporter():
    """The reporter constructs `new Scorm()`, so the wrapper has to be parsed first."""
    html = archive_of(make_lesson()).read(LAUNCH_NAME).decode()
    assert html.index(API_NAME) < html.index(REPORTER_NAME)


def test_the_api_wrapper_is_module_bs_own_file():
    """Shared rather than copied — it implements ADL's discovery algorithm and has
    nothing assessment-specific in it, so two copies could only drift."""
    from importlib import resources

    shipped = archive_of(make_lesson()).read(API_NAME).decode()
    original = resources.files("app.packaging.scorm").joinpath("assets", "api.js").read_text(encoding="utf-8")
    assert shipped == original


# --- what the reporter promises ---------------------------------------------------


def test_no_score_is_ever_reported():
    """A lesson asks nothing. Reporting 0 out of 0 is not "no score", it is a zero,
    and more than one LMS renders that as a failed attempt."""
    assert "cmi.core.score" not in reporter_code()


def test_only_the_two_honest_statuses_are_written():
    source = reporter_code()
    assert '"incomplete"' in source
    assert '"completed"' in source
    # Nothing here is judged, so neither verdict may ever be claimed.
    assert 'set("cmi.core.lesson_status", "passed")' not in source
    assert 'set("cmi.core.lesson_status", "failed")' not in source


def test_session_time_is_built_as_a_cmi_timespan():
    """SCORM 1.2 wants HHHH:MM:SS with at least two hour digits. A malformed value is
    rejected wholesale, so the session silently reports nothing."""
    source = reporter_code()
    # Split rather than chained, so a failure names the component that is unpadded
    # instead of reporting that the conjunction as a whole was false.
    assert "pad(h)" in source
    assert "pad(m)" in source
    assert "pad(sec)" in source


def test_a_finished_lesson_is_never_reopened_as_incomplete():
    """An LMS that already has this learner down as completed must not be told
    otherwise on a revisit — that takes a finished lesson away from someone."""
    source = reporter_code()
    assert '"not attempted"' in source
    assert 'status === "completed"' in source


def test_the_resume_position_is_range_checked_before_it_is_trusted():
    """It comes back from the LMS as a string of unknown shape."""
    source = reporter_code()
    assert "parseInt" in source
    assert "isNaN" in source
    assert "< total()" in source


def test_it_listens_on_the_decks_hook_rather_than_reaching_into_it():
    """The deck exposes one seam. If the reporter grew a second way in, the plain
    HTML5 file and the SCO would stop being the same code."""
    source = reporter_code()
    assert "deck.onSlide = report" in source
    assert "LessonDeck" in source


def test_it_uses_pagehide_rather_than_unload():
    """unload does not fire reliably on mobile Safari or when a tab is discarded,
    and a missed finish leaves the attempt open in the gradebook."""
    source = reporter_code()
    assert "pagehide" in source
    assert 'addEventListener("unload"' not in source


def test_it_does_nothing_at_all_outside_an_lms():
    """The same file has to stay openable in a plain browser, which is how it gets
    checked before anyone imports it."""
    source = reporter_code()
    assert "if (!deck) return;" in source
    assert "if (scorm.initialize())" in source


# --- refusals and reproducibility -------------------------------------------------


def test_a_lesson_with_nothing_to_show_is_refused():
    lesson = make_lesson(steps=[LessonStep(index=1, title="  ", bullets=[""], notes="")])
    with pytest.raises(EmptyLessonError):
        emit_scorm(lesson)


def test_the_same_lesson_emits_byte_identical_packages():
    lesson = make_lesson()
    first = emit_scorm(lesson).content
    second = emit_scorm(lesson).content
    assert first == second


def test_the_filename_marks_it_as_the_scorm_one():
    """A teacher who downloads both formats needs to tell them apart in a folder."""
    assert emit_scorm(make_lesson()).filename == "The-Water-Cycle-scorm.zip"


def test_markup_from_the_document_cannot_execute_in_the_sco():
    lesson = make_lesson(steps=[LessonStep(index=1, title="<script>alert(1)</script>", bullets=["b"], notes="")])
    html = archive_of(lesson).read(LAUNCH_NAME).decode()
    assert "<script>alert" not in html


def test_the_reporting_script_is_declared_as_package_data():
    """Read at emit time from the installed package. Without the pyproject entry it
    is absent from a built wheel and every lesson silently loses its reporting."""
    import tomllib
    from pathlib import Path

    config = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert "assets/*" in config["tool"]["setuptools"]["package-data"]["app.microlesson.emit"]


# --- the hole the second audit pass found -----------------------------------------


def test_a_title_cannot_break_out_of_a_script_tag():
    """An earlier version injected a JSON island describing the lesson, built with
    `json.dumps` — which does not escape `<`. A lesson titled `</script><script>…`
    closed the island early and ran whatever followed, inside a tenant's LMS.

    The island was deleted rather than escaped: nothing read it. This test would
    catch it, or anything like it, coming back.
    """
    lesson = make_lesson(title="</script><script>alert(1)</script>")
    html = archive_of(lesson).read(LAUNCH_NAME).decode()
    assert "<script>alert(1)" not in html
    # Asserted by role rather than by a raw count, which a first version got wrong
    # by forgetting the deck's own inline script: one inline script (the deck), and
    # two external ones (the API wrapper and the reporter). Nothing else.
    assert html.count("<script src=") == 2
    assert html.count("<script>") == 1


def test_the_launch_page_carries_no_speculative_data():
    """Fields nobody reads are the ones nobody audits."""
    html = archive_of(make_lesson()).read(LAUNCH_NAME).decode()
    assert "lesson-meta" not in html
    assert "application/json" not in html
