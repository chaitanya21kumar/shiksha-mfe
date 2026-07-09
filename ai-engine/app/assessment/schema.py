"""The Module B contract: a source-grounded assessment derived from a document.

Like `DocumentInsights` and `NarrationScript`, `AssessmentSet` is a separate,
generative model — not an extension of `ParsedDocument`. It is a *neutral*
pedagogical contract: it holds questions in a form that maps losslessly, in
later modules, into an H5P Question Set, a SCORM 1.2 package, and xAPI 1.0
statements, without being coupled to any one of them.

Two rules keep that mapping lossless, and were chosen after checking each target
(H5P.MultiChoice / H5P.Blanks / H5P.DragText, SCORM 1.2, xAPI cmi.interactions):

- **Every answerable element carries a stable id** (choices, match terms,
  blanks). xAPI ``correctResponsesPattern`` and SCORM ``cmi.interactions`` are
  built from these ids, not display text, and text would collide with their
  reserved ``[,] [.] [:]`` delimiters. Ids are assigned by the pipeline, never by
  the model, so they are unique and delimiter-safe.
- **Answers are stored structured, never as marked-up strings.** The H5P
  ``*answer/alt:tip*`` markup and the xAPI response pattern are both generated at
  emit time from the same structured data; the contract stores neither.

Placement inside an interactive video (timestamp, coordinates) is deliberately
kept *out* of a `Question`: a question is placement-agnostic, and Module C adds a
thin wrapper when it attaches one to a video.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, computed_field, model_validator

# Fill-in-the-blank sentences mark each blank positionally as [[1]], [[2]], … .
# This token cannot collide with LaTeX (``\( \)``, ``$$``) or with the H5P blank
# markup characters (``* / :``), so a blanked sentence round-trips cleanly.
_BLANK_MARKER = re.compile(r"\[\[(\d+)\]\]")


class Choice(BaseModel):
    """One option of a multiple-choice question."""

    id: str = Field(description="Stable id, assigned by the pipeline (e.g. 'q1-c1').")
    text: str
    is_correct: bool = False
    feedback: str | None = Field(
        default=None, description="Shown when this choice is selected (H5P chosenFeedback)."
    )


class MatchTarget(BaseModel):
    """A draggable/right-hand item in a match-the-pair question."""

    id: str = Field(description="Stable id, assigned by the pipeline (e.g. 'q1-t1').")
    text: str


class MatchSource(BaseModel):
    """A left-hand prompt in a match-the-pair question, plus its correct target."""

    id: str = Field(description="Stable id, assigned by the pipeline (e.g. 'q1-s1').")
    text: str
    target_id: str = Field(description="Id of the `MatchTarget` this source correctly matches.")


class Blank(BaseModel):
    """One blank in a fill-in-the-blank question."""

    id: str = Field(description="Stable id, assigned by the pipeline (e.g. 'q1-b1').")
    answers: list[str] = Field(
        min_length=1,
        description="Accepted answers; the first is canonical, the rest are alternatives.",
    )
    tip: str | None = None


class _QuestionBase(BaseModel):
    """Fields shared by every question type."""

    id: str = Field(description="Stable, unique id within the set (e.g. 'q1').")
    source_index: int | None = Field(
        default=None,
        description="1-based Page.index the question was drawn from, or None if not attributable.",
    )
    explanation: str | None = Field(
        default=None, description="Grounded rationale for the correct answer."
    )
    points: float = Field(default=1.0, ge=0, description="Maximum score for this question.")
    has_latex: bool = Field(
        default=False,
        description=r"Stem or answers contain LaTeX, rendered by MathJax (delimiters \( \), \[ \], $$).",
    )


class MCQItem(_QuestionBase):
    """A multiple-choice question."""

    type: Literal["mcq"] = "mcq"
    prompt: str = Field(description="The question stem.")
    choices: list[Choice] = Field(min_length=2)
    single_answer: bool = Field(
        default=True,
        description="Exactly one correct choice; set False to allow multi-select.",
    )

    @model_validator(mode="after")
    def _check_choices(self) -> MCQItem:
        ids = [c.id for c in self.choices]
        if len(set(ids)) != len(ids):
            raise ValueError("choice ids must be unique")
        correct = sum(1 for c in self.choices if c.is_correct)
        if self.single_answer and correct != 1:
            raise ValueError("a single-answer question must have exactly one correct choice")
        if not self.single_answer and correct < 1:
            raise ValueError("a multi-answer question must have at least one correct choice")
        return self


class MatchItem(_QuestionBase):
    """A match-the-pair question: each source maps to exactly one target."""

    type: Literal["match"] = "match"
    prompt: str = Field(description="The matching instruction/stem.")
    sources: list[MatchSource] = Field(min_length=2)
    targets: list[MatchTarget] = Field(
        min_length=2,
        description="Candidate targets; any target no source maps to is a distractor.",
    )

    @model_validator(mode="after")
    def _check_pairs(self) -> MatchItem:
        target_ids = [t.id for t in self.targets]
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("target ids must be unique")
        source_ids = [s.id for s in self.sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source ids must be unique")
        known = set(target_ids)
        for s in self.sources:
            if s.target_id not in known:
                raise ValueError(f"source {s.id!r} points at unknown target {s.target_id!r}")
        return self


class FillBlankItem(_QuestionBase):
    """A fill-in-the-blank question.

    ``text`` is the sentence with each blank marked positionally as ``[[1]]``,
    ``[[2]]``, … in reading order; ``blanks[i]`` fills marker ``[[i + 1]]``.
    """

    type: Literal["fill_blank"] = "fill_blank"
    prompt: str | None = Field(default=None, description="Optional task instruction.")
    text: str = Field(description="Sentence with each blank marked as [[1]], [[2]], … in order.")
    blanks: list[Blank] = Field(min_length=1)
    case_sensitive: bool = Field(
        default=False,
        description="Whether answers are case-sensitive (stated explicitly: H5P and xAPI defaults differ).",
    )
    order_matters: bool = Field(
        default=True,
        description="Whether multiple blanks must be answered in order (xAPI fill-in default).",
    )

    @model_validator(mode="after")
    def _check_blanks(self) -> FillBlankItem:
        ids = [b.id for b in self.blanks]
        if len(set(ids)) != len(ids):
            raise ValueError("blank ids must be unique")
        markers = sorted(int(n) for n in _BLANK_MARKER.findall(self.text))
        if markers != list(range(1, len(self.blanks) + 1)):
            raise ValueError(
                "text must mark each blank exactly once as [[1]]..[[N]] matching the blanks"
            )
        return self


Question = Annotated[Union[MCQItem, MatchItem, FillBlankItem], Field(discriminator="type")]


class AssessmentSource(BaseModel):
    """A pointer back to the document this assessment was derived from."""

    filename: str
    title: str | None = None
    page_count: int = Field(description="Pages, slides or sheets in the source.")


class AssessmentSet(BaseModel):
    """Everything Module B derives from one parsed document."""

    schema_version: str = "1.0"
    assessment_id: str = Field(
        description="Stable unique id for this set (SCORM manifest identifier, xAPI activity IRI base)."
    )
    source: AssessmentSource
    language: str = Field(
        default="en",
        description="BCP-47 language tag of the questions (H5P manifest, xAPI language maps).",
    )
    generator: str = Field(description='What produced the assessment, e.g. "groq".')
    model: str = Field(description='The model that generated it, e.g. "llama-3.1-8b-instant".')
    generated_at: datetime
    pass_percentage: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Mastery threshold (SCORM masteryscore, H5P passPercentage, xAPI result.success).",
    )
    questions: list[Question] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues, e.g. a question dropped for not being grounded in the source.",
    )

    @model_validator(mode="after")
    def _check_ids(self) -> AssessmentSet:
        ids = [q.id for q in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("question ids must be unique within the set")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_points(self) -> float:
        """Sum of every question's points — the maximum achievable score."""
        return round(sum(q.points for q in self.questions), 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def counts(self) -> dict[str, int]:
        """How many questions of each type, derived so it can never drift."""
        out: dict[str, int] = {}
        for q in self.questions:
            out[q.type] = out.get(q.type, 0) + 1
        return out
