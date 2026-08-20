"""The publishing half: everything a course produced, as one archive.

A folder of files can tell a teacher what succeeded. It cannot tell them what was
attempted and did not, and that is the question actually asked later — usually by
someone who was not there when it was built. So these pin the manifest and the
README as hard as the packages, because a bundle is only ever read after it has
left us.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from app.course.bundle import build_bundle
from app.course.schema import Course, CourseSource, Stage, StageOutcome, StageReport
from app.microlesson.schema import LessonStep, MicroLesson
from tests.factories import make_mcq, make_set

WHEN = datetime(2026, 8, 20, tzinfo=timezone.utc)


def make_lesson(**kw) -> MicroLesson:
    defaults = dict(
        lesson_id="l-1", source={"kind": "document", "unit_count": 2}, title="The Water Cycle",
        generator="test", model="m", generated_at=WHEN,
        steps=[LessonStep(index=1, title="Evaporation", bullets=["The sun warms it"], notes="Say this")],
    )
    defaults.update(kw)
    return MicroLesson(**defaults)


def make_course(**kw) -> Course:
    defaults = dict(
        course_id="c-1", title="The Water Cycle", language="en",
        source=CourseSource(kind="document", filename="lesson.pdf", unit_count=2),
        generator="test", model="openai/gpt-oss-20b", generated_at=WHEN,
        lesson=make_lesson(), assessment=make_set([make_mcq()]),
        stages=[
            StageReport(stage=Stage.INSIGHTS, outcome=StageOutcome.SKIPPED, detail="not requested"),
            StageReport(stage=Stage.LESSON, outcome=StageOutcome.PRODUCED),
            StageReport(stage=Stage.ASSESSMENT, outcome=StageOutcome.PRODUCED),
        ],
    )
    defaults.update(kw)
    return Course(**defaults)


def names(bundle) -> list[str]:
    return sorted(zipfile.ZipFile(io.BytesIO(bundle.content)).namelist())


def read(bundle, name: str) -> bytes:
    return zipfile.ZipFile(io.BytesIO(bundle.content)).read(name)


# --- what a teacher gets ----------------------------------------------------------


def test_every_package_the_course_supports_is_in_the_archive():
    got = names(build_bundle(make_course()))
    for expected in (
        "lesson/lesson.h5p", "lesson/lesson.html", "lesson/lesson-scorm.zip",
        "quiz/quiz.h5p", "quiz/quiz-scorm.zip",
    ):
        assert expected in got


def test_the_editable_data_travels_with_the_packages():
    """A teacher who wants to fix a heading and package again needs the lesson as
    data, not only as five compiled formats."""
    got = names(build_bundle(make_course()))
    assert "lesson.json" in got
    assert "assessment.json" in got


def test_a_course_with_no_assessment_ships_no_quiz():
    got = names(build_bundle(make_course(assessment=None)))
    assert not [n for n in got if n.startswith("quiz/")]
    assert "lesson/lesson.h5p" in got


# --- the manifest, which is the reason this is a bundle and not a folder ----------


def test_the_manifest_carries_every_stage_including_packaging():
    manifest = json.loads(read(build_bundle(make_course()), "manifest.json"))
    stages = {s["stage"] for s in manifest["stages"]}
    assert stages == {"insights", "lesson", "assessment", "packaging"}


def test_the_manifest_keeps_the_reason_a_stage_produced_nothing():
    """The whole point. Six weeks later, "where is the summary" has an answer inside
    the file rather than in a log nobody kept."""
    manifest = json.loads(read(build_bundle(make_course()), "manifest.json"))
    insights = next(s for s in manifest["stages"] if s["stage"] == "insights")
    assert insights["outcome"] == "skipped"
    assert insights["detail"] == "not requested"


def test_the_manifest_names_the_model_that_built_it():
    manifest = json.loads(read(build_bundle(make_course()), "manifest.json"))
    assert manifest["model"] == "openai/gpt-oss-20b"


# --- the README, for the person who double-clicks --------------------------------


def test_the_readme_explains_each_file_in_plain_words():
    readme = read(build_bundle(make_course()), "README.txt").decode()
    assert "lesson/lesson.h5p" in readme
    assert "Moodle" in readme


def test_the_readme_says_what_is_missing_and_why():
    readme = read(build_bundle(make_course()), "README.txt").decode()
    assert "WHAT IS NOT IN HERE" in readme
    assert "insights: not requested" in readme


# --- one bad format must not cost the others -------------------------------------


def test_a_format_that_cannot_be_emitted_is_named_rather_than_fatal(monkeypatch):
    import app.course.bundle as B

    def explode(_lesson):
        raise RuntimeError("this emitter is broken")

    monkeypatch.setattr(B, "emit_lesson_html5", explode)
    bundle = build_bundle(make_course())

    assert "lesson/lesson.html" not in names(bundle)
    assert "lesson/lesson.h5p" in names(bundle)          # the rest survived
    assert any("lesson.html" in w for w in bundle.warnings)


def test_a_failed_format_is_recorded_in_the_manifest(monkeypatch):
    import app.course.bundle as B
    monkeypatch.setattr(B, "emit_quiz_scorm", lambda _a: (_ for _ in ()).throw(RuntimeError("no")))
    manifest = json.loads(read(build_bundle(make_course()), "manifest.json"))
    packaging = next(s for s in manifest["stages"] if s["stage"] == "packaging")
    assert "quiz/quiz-scorm.zip" in packaging["detail"]


def test_a_failed_stage_reaches_the_response_headers():
    """`package_response` puts `warnings` in the headers. A bundle quietly missing a
    package, answered with a 200 and no header, is exactly the silent success this
    module exists to prevent."""
    course = make_course(stages=[
        StageReport(stage=Stage.ASSESSMENT, outcome=StageOutcome.FAILED,
                    detail="no passage supports a question"),
    ])
    bundle = build_bundle(course)
    assert any("no passage supports a question" in w for w in bundle.warnings)


# --- the archive itself ------------------------------------------------------------


def test_the_same_course_bundles_to_the_same_bytes():
    """The weakest of the three, kept because it is the property callers care about.

    On its own it proves almost nothing: two builds in the same process, in the same
    second, agree even if the archive order came from dict insertion and the
    timestamps came from the clock. Mutation testing showed exactly that — both
    breakages survived this assertion. The two below pin the mechanisms instead.
    """
    course = make_course()
    # Named rather than compared inline: two independent builds, so a failure prints
    # which bytes differ instead of reading as an expression compared with itself.
    first = build_bundle(course).content
    second = build_bundle(course).content
    assert first == second


def test_the_archive_is_ordered_by_name_not_by_insertion():
    """Reproducibility has to survive the dict order changing, which it will the
    moment a stage is added or reordered.

    Read raw rather than through `names()`, which sorts — asserting a sorted list is
    sorted is exactly the vacuous test this suite has been caught by before, and
    mutation testing caught it again here.
    """
    raw = zipfile.ZipFile(io.BytesIO(build_bundle(make_course()).content)).namelist()
    assert raw == sorted(raw)


def test_every_entry_carries_the_fixed_timestamp():
    """The same constant the H5P and SCORM writers use. A clock here would make two
    builds of one course differ by the second, and nothing downstream could be
    asserted byte for byte."""
    archive = zipfile.ZipFile(io.BytesIO(build_bundle(make_course()).content))
    stamps = {info.date_time for info in archive.infolist()}
    assert stamps == {(1980, 1, 1, 0, 0, 0)}


def test_the_filename_comes_from_the_course_title():
    assert build_bundle(make_course()).filename == "The-Water-Cycle-course.zip"


def test_a_course_that_produced_nothing_still_bundles_its_explanation():
    """An empty archive would be a worse answer than a bad one: a teacher would not
    know whether anything ran."""
    course = make_course(lesson=None, assessment=None, stages=[
        StageReport(stage=Stage.LESSON, outcome=StageOutcome.FAILED, detail="nothing teachable"),
    ])
    bundle = build_bundle(course)
    got = names(bundle)
    assert got == ["README.txt", "manifest.json"]
    assert "nothing teachable" in read(bundle, "README.txt").decode()


def test_devanagari_survives_into_the_bundle():
    """These tenants teach in Hindi and Marathi, and a teacher opening lesson.json
    should read their own words back rather than escape sequences."""
    course = make_course(title="जल चक्र", lesson=make_lesson(title="जल चक्र"))
    bundle = build_bundle(course)
    assert "जल चक्र" in read(bundle, "lesson.json").decode("utf-8")
    assert "जल चक्र" in read(bundle, "manifest.json").decode("utf-8")


# --- the artefacts only a full build produces --------------------------------------


def test_insights_and_narration_travel_as_data_when_they_were_produced():
    """Neither has a package format of its own, so the archive carries them as JSON —
    and a teacher who asked for a narration script has to actually find one."""
    from app.narration.schema import NarrationScript, NarrationSource
    from app.summarization.schema import DocumentInsights, InsightsSource

    course = make_course(
        insights=DocumentInsights(
            source=InsightsSource(filename="lesson.pdf", page_count=1),
            generator="test", model="m", generated_at=WHEN,
        ),
        narration=NarrationScript(
            source=NarrationSource(filename="lesson.pdf", page_count=1),
            generator="test", model="m", generated_at=WHEN,
        ),
    )
    got = names(build_bundle(course))
    assert "insights.json" in got
    assert "narration.json" in got


def test_the_readme_carries_the_notes_from_the_build():
    """Warnings are where the engine says what a format could not express. Leaving
    them out of the one file a teacher opens would lose exactly that."""
    course = make_course(warnings=["microlesson: the model returned nothing for step 2"])
    readme = read(build_bundle(course), "README.txt").decode()
    assert "NOTES FROM THE BUILD" in readme
    assert "nothing for step 2" in readme
