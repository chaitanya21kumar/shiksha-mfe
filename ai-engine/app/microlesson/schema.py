"""The Module D contract: one micro-lesson, before it becomes a package.

A micro-lesson is a short sequence of steps a learner works through. This contract
holds it in a form that maps cleanly onto all three targets Module D has to emit —
an H5P Course Presentation, an HTML5 slide deck and a SCORM 1.2 course — without
being shaped by any one of them. The same separation `AssessmentSet` keeps from
H5P Question Set, and for the same reason: the moment a contract borrows one
target's vocabulary, the other two need translation layers.

Two rules keep it honest.

**Every step traces back to a unit of source.** `source_index` is not decoration:
it is what lets a reviewer ask "where did this come from" and get an answer. A step
with no source is a step nobody can check, so the pipeline never creates one.

**The number of steps is decided in Python, not by the model.** A lesson has as
many steps as the source has teachable units — slides, headings, chapters. The
model writes the words inside each step and is never asked how many there should
be, for the reason ADR-0008 gives about chapter boundaries: a generated structure
is different on every run, which makes everything downstream untestable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

#: Where a lesson came from. Three, because that is what issue #7 asks for: "a
#: document, a transcript, or free-form input".
SourceKind = Literal["document", "transcript", "text"]

#: A lesson longer than this is not a *micro*-lesson. The cap matches the section
#: cap the other pipelines use, so a document that produces 40 sections produces at
#: most 40 steps rather than being silently cut at a different number here.
MAX_STEPS = 40

#: Below this a step has nothing to teach, and is almost always an artefact of a
#: stray heading or a slide with only a title.
MIN_STEP_CHARS = 20


class LessonStep(BaseModel):
    """One step of the lesson: what a learner sees, and what is said over it."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1, description="1-based position of this step in the lesson.")
    title: str = Field(min_length=1, description="The step's heading, shown to the learner.")
    bullets: list[str] = Field(
        default_factory=list,
        description="The on-screen points. Kept short: a slide is not a paragraph.",
    )
    notes: str = Field(
        default="",
        description="What a teacher would say over this step. Becomes speaker notes.",
    )
    source_index: int | None = Field(
        default=None,
        description=(
            "The page, slide or chapter this step was built from. None only for a "
            "free-form source, which has no numbered units to point at."
        ),
    )


class LessonSource(BaseModel):
    """A pointer back to whatever the lesson was generated from."""

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    filename: str | None = Field(
        default=None, description="Original filename, when the source was a file."
    )
    title: str | None = None
    unit_count: int = Field(
        default=0,
        ge=0,
        description="Pages, slides or chapters the source offered before any capping.",
    )


class MicroLesson(BaseModel):
    """Everything Module D derives from one source, before packaging.

    Unlike the two contracts above, this one does **not** forbid extra fields, and
    the reason is `step_count`. A computed field is serialised into the output but
    is not accepted as an input, so `extra="forbid"` here would mean the engine
    rejecting a lesson it produced itself: generate one, POST it to a packaging
    endpoint the way `assess_h5p` already takes an `AssessmentSet`, and the request
    fails on the very field the response added. `AssessmentSet` leaves the same
    door open for the same reason. `LessonStep` and `LessonSource` keep the strict
    setting, because they have no computed fields and a mistyped key there is a
    silent content loss rather than a round-trip.
    """

    schema_version: str = "1.0"
    lesson_id: str = Field(description="Stable id; subcontent and manifest ids derive from it.")
    source: LessonSource
    title: str = Field(min_length=1, description="The lesson title, shown on the first screen.")
    language: str = Field(default="en", description="BCP-47 tag for the package manifest.")
    generator: str = Field(description='What produced the lesson, e.g. "groq".')
    model: str = Field(description="The model that wrote the prose.")
    generated_at: datetime
    objectives: list[str] = Field(
        default_factory=list,
        description="What a learner should be able to do afterwards. Shown before step one.",
    )
    steps: list[LessonStep] = Field(default_factory=list, max_length=MAX_STEPS)
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues, e.g. a step the model returned nothing for.",
    )

    @computed_field
    @property
    def step_count(self) -> int:
        return len(self.steps)

    @model_validator(mode="after")
    def _steps_are_numbered_in_order(self) -> MicroLesson:
        """Steps must be 1..n with no gaps.

        A packaged lesson is navigated by position, so a missing or repeated index
        is not a cosmetic problem: an H5P Course Presentation would render the
        slides out of order, and a SCORM course would report progress against the
        wrong one. Cheaper to refuse here than to debug in an LMS.
        """
        expected = list(range(1, len(self.steps) + 1))
        actual = [step.index for step in self.steps]
        if actual != expected:
            raise ValueError(f"steps must be numbered 1..{len(self.steps)}, got {actual}")
        return self
