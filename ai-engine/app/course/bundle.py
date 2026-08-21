"""Everything a course produced, as one file a teacher can be handed.

Four modules emit five packages between them, in three formats, and until now a
caller collected them one HTTP request at a time. This is the publishing half of
Week 11: one archive, with a manifest that says what is inside and what is not.

**The manifest is the point, not the zip.** A folder of files tells a teacher what
succeeded; it cannot tell them what was attempted and did not. The manifest carries
every stage report, so the bundle is self-describing after it leaves us — which is
the only state it is ever in when a question about it gets asked.

**Packaging is reported per artefact.** A lesson can package as H5P and fail as
SCORM; that is one bad emitter, not a bad course. Each emission is attempted on its
own and the bundle carries whatever succeeded, so one broken format never costs a
teacher the other four.

Byte-for-byte reproducible, by the same fixed timestamp the H5P and SCORM writers
use. Two builds of the same course produce identical bytes, which is what makes a
bundle something a test can assert on rather than merely inspect.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from typing import NamedTuple

from ..assessment.emit import emit_h5p as emit_quiz_h5p
from ..assessment.emit import emit_scorm as emit_quiz_scorm
from ..microlesson.emit import emit_h5p as emit_lesson_h5p
from ..microlesson.emit import emit_html5 as emit_lesson_html5
from ..microlesson.emit import emit_scorm as emit_lesson_scorm
from ..packaging.naming import sanitise_filename
from .schema import Course, Stage, StageOutcome, StageReport

logger = logging.getLogger("ai_engine.course")

#: The earliest timestamp a ZIP can express — the same constant the H5P and SCORM
#: writers use, so every archive this engine emits is reproducible the same way.
_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

MANIFEST_NAME = "manifest.json"
README_NAME = "README.txt"

#: Every path this module writes, named once. These are our own filenames, not a
#: transcription of anyone else's, so one definition is the right number: the README
#: describes them by the same names it writes, and the two cannot drift.
INSIGHTS_JSON = "insights.json"
NARRATION_JSON = "narration.json"
LESSON_JSON = "lesson.json"
ASSESSMENT_JSON = "assessment.json"
LESSON_H5P = "lesson/lesson.h5p"
LESSON_HTML = "lesson/lesson.html"
LESSON_SCORM = "lesson/lesson-scorm.zip"
QUIZ_H5P = "quiz/quiz.h5p"
QUIZ_SCORM = "quiz/quiz-scorm.zip"


class CourseBundle(NamedTuple):
    """The archive, what to call it, what packaging managed, and what to flag.

    `warnings` is what `package_response` puts in the response headers, so it has to
    carry everything a caller would otherwise only learn by unzipping: the build's own
    warnings, plus any format that could not be emitted. A bundle that quietly lacked
    one of its five packages, with a 200 and no header, is the exact silent-success
    failure this module is built to avoid.
    """

    content: bytes
    filename: str
    report: StageReport
    warnings: list[str]


def _entry(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _json_bytes(payload) -> bytes:
    """Pretty, UTF-8, keys in the order the model declares them.

    Indented and not ASCII-escaped on purpose: a teacher who opens `lesson.json`
    should be able to read their own Hindi back, and a reviewer should be able to
    diff two bundles line by line.
    """
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def _try(files: dict[str, bytes], name: str, emit) -> str | None:
    """Emit one artefact into the bundle, or leave it out and say so.

    Returns the path written, or None. The caller collects the names; a failure is
    logged with its traceback and named in the manifest, never raised, because one
    format failing is not the course failing.
    """
    try:
        files[name] = emit()
    except Exception as exc:  # noqa: BLE001 — one bad format must not cost the rest
        logger.warning("Could not package %s: %s", name, exc, exc_info=True)
        return None
    return name


def build_bundle(course: Course) -> CourseBundle:
    """Package everything on a course into one reproducible archive."""
    files: dict[str, bytes] = {}
    written: list[str] = []
    failed: list[str] = []

    def record(name: str, emit) -> None:
        (written if _try(files, name, emit) else failed).append(name)

    # --- the readable artefacts, always first in the archive ---------------------
    for name, artefact in (
        (INSIGHTS_JSON, course.insights),
        (NARRATION_JSON, course.narration),
        (LESSON_JSON, course.lesson),
        (ASSESSMENT_JSON, course.assessment),
    ):
        if artefact is not None:
            files[name] = _json_bytes(artefact)
            written.append(name)

    # --- the packages an LMS opens ------------------------------------------------
    if course.lesson is not None:
        record(LESSON_H5P, lambda: emit_lesson_h5p(course.lesson).content)
        record(LESSON_HTML, lambda: emit_lesson_html5(course.lesson).content)
        record(LESSON_SCORM, lambda: emit_lesson_scorm(course.lesson).content)
    if course.assessment is not None:
        record(QUIZ_H5P, lambda: emit_quiz_h5p(course.assessment).content)
        record(QUIZ_SCORM, lambda: emit_quiz_scorm(course.assessment).content)

    report = _packaging_report(written, failed)

    files[MANIFEST_NAME] = _json_bytes(_manifest(course, report))
    files[README_NAME] = _readme(course, report).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # Sorted so the archive's byte order does not depend on dict insertion.
        for name in sorted(files):
            archive.writestr(_entry(name), files[name])

    stem = sanitise_filename(course.title, fallback="course")
    warnings = list(course.warnings)
    if failed:
        warnings.append(
            "packaging: could not produce " + ", ".join(sorted(failed))
        )
    for r in course.stages:
        if r.outcome is StageOutcome.FAILED:
            warnings.append(f"{r.stage.value}: {r.detail}")
    return CourseBundle(buffer.getvalue(), f"{stem}-course.zip", report, warnings)


def _packaging_report(written: list[str], failed: list[str]) -> StageReport:
    """How packaging went, in the three outcomes the rest of the contract uses.

    Producing *something* while failing something else stays `produced`, with the
    failures named: a caller who got four of five packages did not have packaging
    fail, and calling it failed would make the course look unusable when it is not.

    There is deliberately no `failed` outcome here, and that is a property of the
    order things are written rather than an oversight. Packages are only attempted
    when a lesson or an assessment exists, and either of those has already written
    its `.json` by then — so `failed` can never be non-empty while `written` is
    empty. A branch for that state would be unreachable, and unreachable code that
    looks defensive is worse than none: it can never be tested, so it can never be
    known to be right.
    """
    if failed:
        outcome, detail = StageOutcome.PRODUCED, "Could not package: " + ", ".join(sorted(failed))
    elif written:
        outcome, detail = StageOutcome.PRODUCED, ""
    else:
        outcome, detail = StageOutcome.SKIPPED, "nothing was generated to package"
    return StageReport(
        stage=Stage.PACKAGING, outcome=outcome, detail=detail, artefacts=sorted(written)
    )


def _manifest(course: Course, packaging: StageReport) -> dict[str, object]:
    """What is in this archive, what is not, and why — for whoever opens it later."""
    return {
        "schema_version": "1.0",
        "course_id": course.course_id,
        "title": course.title,
        "language": course.language,
        "source": course.source.model_dump(mode="json"),
        "generator": course.generator,
        "model": course.model,
        "generated_at": course.generated_at.isoformat(),
        "stages": [r.model_dump(mode="json") for r in [*course.stages, packaging]],
        "warnings": course.warnings,
    }


def _readme(course: Course, packaging: StageReport) -> str:
    """The same manifest, for a person rather than a program.

    Plain text because it is opened by double-clicking, on a machine we know nothing
    about. Anyone who unzips this should be able to answer "what is this and what can
    I do with it" without reading JSON.
    """
    lines = [
        course.title,
        "=" * len(course.title),
        "",
        f"Generated {course.generated_at:%d %B %Y} from {course.source.filename or course.source.kind}.",
        "",
        "WHAT IS IN HERE",
        "",
    ]
    what = {
        LESSON_H5P: "The lesson as an H5P Course Presentation. Upload it to Moodle or Sunbird.",
        LESSON_HTML: "The same lesson as one self-contained web page. Needs no LMS and no internet.",
        LESSON_SCORM: "The same lesson as a SCORM 1.2 course. Reports completion to a gradebook.",
        QUIZ_H5P: "The questions as an H5P Question Set. Marks itself in the browser.",
        QUIZ_SCORM: "The questions as a SCORM 1.2 course. Reports the score to a gradebook.",
        LESSON_JSON: "The lesson in plain data, if you want to edit it before packaging again.",
        ASSESSMENT_JSON: "The questions in plain data, each traceable to the source it came from.",
        INSIGHTS_JSON: "Summary, glossary and outline of the source document.",
        NARRATION_JSON: "A narration script for the document. Text only, not audio.",
    }
    for name in packaging.artefacts:
        lines.append(f"  {name}")
        lines.append(f"      {what.get(name, '')}".rstrip())
    missing = [r for r in course.stages if r.outcome is not StageOutcome.PRODUCED]
    if missing or packaging.detail:
        lines += ["", "WHAT IS NOT IN HERE, AND WHY", ""]
        for r in missing:
            lines.append(f"  {r.stage.value}: {r.detail or r.outcome.value}")
        if packaging.detail:
            lines.append(f"  packaging: {packaging.detail}")
    if course.warnings:
        lines += ["", "NOTES FROM THE BUILD", ""]
        lines += [f"  {w}" for w in course.warnings]
    lines += ["", f"Built by the AI Engine using {course.model}.", ""]
    return "\n".join(lines)
