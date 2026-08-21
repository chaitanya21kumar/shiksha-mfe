"""Run every module over one source and report on each of them.

The orchestration is deliberately thin. It owns no generation logic of its own: each
stage is the module's existing entry point, called with the same arguments its own
router would pass. A second implementation of "how a lesson is built" living here
would be the drift ADR-0011 warns about, one layer up.

What this file *does* own is the failure policy, and that is the whole point of it.

**A stage that fails must not take the others down.** Four generations run against a
model. Any of them can legitimately produce nothing: a scanned page supports no
grounded question, a document of headings supports no lesson. Letting the first of
those fail the request would throw away three good artefacts and tell the teacher
nothing about which one was the problem.

**But a failure must never look like an absence.** Every stage reports, including the
ones that were not asked for, and the three outcomes stay distinct. A caller reading
`assessment is None` can always find out from `stages` whether that is because they
did not ask, or because the document could not support one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from ..assessment.pipeline import generate_assessment
from ..ingestion.schema import ParsedDocument
from ..microlesson.pipeline import (
    lesson_from_document,
    lesson_from_text,
    lesson_from_transcript,
)
from ..narration.pipeline import generate_narration
from ..summarization.pipeline import generate_insights
from .schema import (
    DEFAULT_QUESTION_TYPES,
    Course,
    CourseOptions,
    CourseSource,
    Stage,
    StageOutcome,
    StageReport,
)

__all__ = ["DEFAULT_QUESTION_TYPES", "build_course"]

logger = logging.getLogger("ai_engine.course")

#: The reason a stage did not run when the caller simply did not ask for it. One
#: spelling, because a caller matching on this string should not have to guess which
#: of four near-identical sentences a given stage produces.
NOT_REQUESTED = "not requested"

class _Recorder:
    """Collects one `StageReport` per stage, in run order."""

    def __init__(self) -> None:
        self.reports: list[StageReport] = []

    def skipped(self, stage: Stage, why: str) -> None:
        self.reports.append(
            StageReport(stage=stage, outcome=StageOutcome.SKIPPED, detail=why)
        )

    def produced(self, stage: Stage, *, artefacts: list[str] | None = None) -> None:
        self.reports.append(
            StageReport(
                stage=stage, outcome=StageOutcome.PRODUCED, artefacts=artefacts or []
            )
        )

    def failed(self, stage: Stage, exc: BaseException) -> None:
        # Logged with the traceback and reported without it. A caller needs to know
        # what they can do about it; we need to know whether it was our bug. Sending
        # the traceback to the caller would serve neither, and would leak internals
        # to a tenant.
        logger.warning("Course stage %s failed: %s", stage.value, exc, exc_info=True)
        self.reports.append(
            StageReport(
                stage=stage, outcome=StageOutcome.FAILED, detail=_readable(exc)
            )
        )


def _readable(exc: BaseException) -> str:
    """The sentence a teacher should read when a stage could not be produced."""
    text = str(exc).strip() or exc.__class__.__name__
    return text[:400]


async def _stage(recorder: _Recorder, stage: Stage, wanted: bool, why_not: str, run):
    """Run one stage, or record why it did not run, and never raise.

    `Exception` rather than the engine's own error types on purpose. The narrow set
    is knowable today and will not stay knowable: every module reaches a model, a
    parser and a template, and the failure policy here has to hold for the exception
    nobody predicted as much as for the ones we named. `BaseException` is excluded so
    a cancellation or a keyboard interrupt still stops the build.
    """
    if not wanted:
        recorder.skipped(stage, why_not)
        return None
    try:
        result = await run()
    except Exception as exc:  # noqa: BLE001 — see the docstring above
        recorder.failed(stage, exc)
        return None
    recorder.produced(stage)
    return result


async def build_course(
    client: httpx.AsyncClient,
    config,
    *,
    document: ParsedDocument | None = None,
    chaptered=None,
    text: str = "",
    options: CourseOptions | None = None,
) -> Course:
    """Build one course from one source, running every stage that was asked for.

    Exactly one of `document`, `chaptered` or `text` must be given — the same three
    sources Module D accepts, because a course is a lesson plus its neighbours and
    has no business accepting a fourth.

    The stages run in the order a person would do them: understand the document,
    then say it aloud, then teach it, then check it. That order is also the useful
    one on failure, since the cheapest and most reliable stages finish first and a
    caller watching a slow build sees results accumulate rather than nothing at all.
    """
    options = options or CourseOptions()
    given = [name for name, value in
             (("document", document), ("chaptered", chaptered), ("text", text.strip()))
             if value]
    if len(given) != 1:
        raise ValueError(
            "A course is built from exactly one of a document, a transcript or text; "
            f"got {given or 'nothing'}."
        )

    recorder = _Recorder()

    # Insights and narration both read the parsed document directly, so neither is
    # available for a transcript or for pasted text. Reported as skipped with the
    # reason rather than silently absent.
    doc_only = document is not None

    insights = await _stage(
        recorder, Stage.INSIGHTS,
        options.with_insights and doc_only,
        NOT_REQUESTED if not options.with_insights else "only a parsed document can be summarised",
        lambda: generate_insights(client, document, config),
    )

    narration = await _stage(
        recorder, Stage.NARRATION,
        options.with_narration and doc_only,
        NOT_REQUESTED if not options.with_narration else "only a parsed document can be narrated",
        lambda: generate_narration(client, document, config),
    )

    async def _lesson():
        if document is not None:
            return await lesson_from_document(
                client, document, config, title=options.title, language=options.language
            )
        if chaptered is not None:
            return await lesson_from_transcript(
                client, chaptered, config, title=options.title, language=options.language
            )
        return await lesson_from_text(client, text, config, title=options.title, language=options.language)

    lesson = await _stage(
        recorder, Stage.LESSON, options.with_lesson, NOT_REQUESTED, _lesson
    )

    assessment = await _stage(
        recorder, Stage.ASSESSMENT,
        options.with_assessment and doc_only,
        NOT_REQUESTED if not options.with_assessment else "questions are grounded in a parsed document",
        lambda: generate_assessment(
            client, document, config,
            question_types=list(options.question_types),
            count=options.question_count,
            language=options.language,
            pass_percentage=options.pass_percentage,
            solution_visibility=options.solution_visibility,
            time_limit_seconds=options.time_limit_seconds,
        ),
    )

    return Course(
        course_id=_course_id(document, chaptered, text),
        title=_title(options.title, lesson, insights, document),
        language=options.language,
        source=_source(document, chaptered, text, lesson),
        generator=getattr(config, "provider", "") or "unknown",
        model=getattr(config, "model", "") or "unknown",
        generated_at=datetime.now(timezone.utc),
        insights=insights,
        narration=narration,
        lesson=lesson,
        assessment=assessment,
        stages=recorder.reports,
        warnings=_collected_warnings(insights, narration, lesson, assessment),
    )


def _course_id(document, chaptered, text: str) -> str:
    """Reuse the id the source already carries rather than minting a second one.

    A course that invented its own id would give the same upload two identities, and
    the one a caller can correlate with anything else is the one already on the
    lesson or the document.
    """
    for candidate in (document, chaptered):
        for attribute in ("document_id", "transcript_id", "id"):
            value = getattr(candidate, attribute, "")
            if value:
                return str(value)
    return f"course-{abs(hash(text)) % (10**10):010d}" if text else "course"


def _title(explicit: str | None, lesson, insights, document) -> str:
    """The author's title wins, then the lesson's, then the document's filename.

    Same precedence Module D settled on: a title the author actually wrote is never
    replaced by one a model produced.
    """
    for candidate in (
        explicit,
        getattr(lesson, "title", ""),
        getattr(insights, "title", ""),
        getattr(getattr(document, "source", None), "filename", ""),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()[:300]
    return "Course"


def _source(document, chaptered, text: str, lesson) -> CourseSource:
    if document is not None:
        return CourseSource(
            kind="document",
            filename=getattr(getattr(document, "source", None), "filename", "") or "",
            unit_count=getattr(getattr(lesson, "source", None), "unit_count", 0) or 0,
        )
    if chaptered is not None:
        return CourseSource(
            kind="transcript",
            unit_count=len(getattr(chaptered, "chapters", []) or []),
        )
    return CourseSource(kind="text")


def _collected_warnings(*artefacts) -> list[str]:
    """Every stage's warnings, prefixed with where each came from.

    Merged rather than nested because a caller wants one list to show a teacher, and
    unprefixed because "the model returned nothing for step 2" is ambiguous once four
    modules can say something like it.
    """
    out: list[str] = []
    for artefact in artefacts:
        if artefact is None:
            continue
        name = type(artefact).__name__.replace("Set", "").replace("Script", "").lower()
        for warning in getattr(artefact, "warnings", []) or []:
            out.append(f"{name}: {warning}")
    return out
