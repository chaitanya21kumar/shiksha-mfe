"""The Week 11 contract: everything one source produces, and what it did not.

Modules A to D each answer one question well. A teacher does not have four
questions; they have a file and a lesson to run on Thursday. This contract is the
shape of that single answer — insights, a lesson, an assessment and the packages
an LMS can open, from one upload.

**Partial success is the whole design.** Four generations run against a model, and
in a classroom the interesting case is the one where three of them work. A document
of photographs yields no groundable question; a page of headings yields no lesson.
If one missing stage failed the request, a teacher would lose the three that were
fine and be told nothing about why. So every stage reports for itself, the course
carries whatever was produced, and `stages` says plainly what is absent and on
whose account.

That means a caller must never infer success from a 200. The answer to "did I get
an assessment" is `course.assessment is not None`, and the answer to "why not" is
the matching `StageReport`. Both are always present.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ..assessment.schema import AssessmentSet
from ..microlesson.schema import MicroLesson
from ..narration.schema import NarrationScript
from ..summarization.schema import DocumentInsights

#: What a course can be built from. The same three Module D accepts, because a
#: course is a superset of a lesson and inherits its sources rather than inventing
#: a fourth.
CourseSourceKind = Literal["document", "transcript", "text"]

#: The default mix when a caller does not name question types. Three objective types
#: rather than all four: short answer is the slowest to generate and the one most
#: likely to find nothing groundable, and a default should be the fast, reliable
#: path. A caller who wants it asks for it.
DEFAULT_QUESTION_TYPES = ("mcq", "fill_blank", "match")


class Stage(str, Enum):
    """The stages a build runs, in the order they run.

    Named rather than positional so a report stays readable when a stage is added,
    and so a caller can look one up without counting.
    """

    INSIGHTS = "insights"
    NARRATION = "narration"
    LESSON = "lesson"
    ASSESSMENT = "assessment"
    PACKAGING = "packaging"


class StageOutcome(str, Enum):
    """Why a stage's output is or is not in the course.

    Three states, not two, because "you did not ask for it" and "you asked and it
    could not be done" are different facts and a teacher acts differently on each.
    """

    #: Ran, and its output is on the course.
    PRODUCED = "produced"
    #: Not requested. Nothing was attempted and nothing is missing.
    SKIPPED = "skipped"
    #: Requested, attempted, and could not be produced. `detail` says why.
    FAILED = "failed"


class StageReport(BaseModel):
    """One line of the build log, in the response rather than in a server log.

    `detail` is written for the person who uploaded the file, not for us. "The
    document has no passage that can support a question" is actionable; a traceback
    is not, and a bare "failed" is worse than either.
    """

    model_config = ConfigDict(extra="forbid")

    stage: Stage
    outcome: StageOutcome
    detail: str = Field(default="", max_length=400)
    #: Files this stage contributed to the bundle, by their path inside it. Empty
    #: for a stage that produced only JSON, so a caller can tell at a glance which
    #: stages put something on disk.
    artefacts: list[str] = Field(default_factory=list)


class CourseSource(BaseModel):
    """Where the course came from, carried through so a result is self-describing."""

    model_config = ConfigDict(extra="forbid")

    kind: CourseSourceKind
    filename: str = ""
    #: How many teachable units the source offered — slides, headings, chapters.
    #: The lesson has at most this many steps, and that count came from the source
    #: rather than from the model.
    unit_count: int = 0


class Course(BaseModel):
    """One source, everything it produced, and an account of everything it did not.

    `extra="forbid"` is deliberately *not* set here, for the reason ADR-0011 records
    about `MicroLesson`: this object carries computed fields, and a caller posting it
    back to a packaging route must not be rejected for echoing what we sent them. The
    nested contracts stay strict, because they have no computed fields and a mistyped
    key there is silent content loss.
    """

    schema_version: Literal["1.0"] = "1.0"
    course_id: str
    title: str = Field(min_length=1, max_length=300)
    language: str = "en"
    source: CourseSource
    generator: str
    model: str
    generated_at: datetime

    insights: DocumentInsights | None = None
    narration: NarrationScript | None = None
    lesson: MicroLesson | None = None
    assessment: AssessmentSet | None = None

    #: One entry per stage, always, in run order — including the stages that were
    #: not requested. A report that only listed problems would make "nothing went
    #: wrong" and "nothing ran" look identical.
    stages: list[StageReport] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def produced(self) -> list[Stage]:
        """The stages that actually put something on the course.

        Convenience for a caller that wants the summary rather than the log, and the
        one thing a UI needs to render a checklist.
        """
        return [r.stage for r in self.stages if r.outcome is StageOutcome.PRODUCED]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_complete(self) -> bool:
        """True when nothing that was asked for failed.

        Deliberately not "everything ran": a course a caller never asked to have an
        assessment in is complete without one.
        """
        return not any(r.outcome is StageOutcome.FAILED for r in self.stages)


class CourseOptions(BaseModel):
    """Everything a caller can choose about a build, in one object.

    Gathered rather than passed as sixteen keyword arguments. A signature that long
    is one a caller gets subtly wrong — two adjacent booleans are impossible to read
    at a call site — and every new knob would widen it further. As one object the
    defaults live in a single place, the routes and the pipeline cannot drift about
    what a default is, and the whole thing is printable when a build misbehaves.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    language: str = "en"

    #: Which stages to attempt. A stage turned off here reports `skipped`, never
    #: `failed` — the distinction the whole contract rests on.
    with_insights: bool = True
    with_narration: bool = False
    with_lesson: bool = True
    with_assessment: bool = True

    question_types: list[str] = Field(
        default_factory=lambda: list(DEFAULT_QUESTION_TYPES)
    )
    question_count: int = Field(default=5, ge=1, le=20)
    pass_percentage: int = Field(default=60, ge=0, le=100)
    solution_visibility: str = "always"
    time_limit_seconds: int | None = Field(default=None, ge=1)
