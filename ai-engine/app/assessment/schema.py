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
        texts = [c.text.strip().lower() for c in self.choices]
        if len(set(texts)) != len(texts):
            raise ValueError("choice texts must be unique")
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
        target_texts = [t.text.strip().lower() for t in self.targets]
        if len(set(target_texts)) != len(target_texts):
            raise ValueError("target texts must be unique")
        source_texts = [s.text.strip().lower() for s in self.sources]
        if len(set(source_texts)) != len(source_texts):
            raise ValueError("source texts must be unique")
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


#: Characters H5P.Essay's matcher reinterprets rather than matches: ``*`` is a
#: wildcard, and a form wrapped in ``/`` is compiled as a regular expression. A
#: form containing either would match something other than itself — silently, and
#: only for some learners. Wildcards are additionally unusable for us: H5P's
#: wildcard character class covers Latin, Greek, Cyrillic, kana, CJK and Thai but
#: **not** Devanagari or the other Indic scripts, so one would work in English
#: content and quietly fail in Hindi.
_UNMATCHABLE_FORM = "*"

#: A key point is a phrase, not a sentence. The cap also keeps every SCORM
#: ``correct_responses`` pattern well inside CMIString255.
_MAX_ACCEPTED_CHARS = 60


class KeyPoint(BaseModel):
    """One criterion of a short answer's mark scheme: an idea worth ``weight`` marks.

    ``accepted`` holds the surface forms that count as having made the point. Every
    one of them must occur in the source document — the pipeline drops any that do
    not — so a mark is never awarded for a phrase the tenant's material does not
    contain. That is the same rule the fill-in-the-blank answers already follow,
    and for the same reason: a mark scheme *is* an answer key.
    """

    id: str = Field(description="Stable id, assigned by the pipeline (e.g. 'q1-k1').")
    text: str = Field(description="The idea the learner must express; shown as the mark scheme.")
    accepted: list[str] = Field(
        min_length=1,
        max_length=6,
        description="Surface forms that score this point; each must appear in the source.",
    )
    weight: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Marks for making this point. An integer, so H5P's max score stays integral.",
    )
    feedback_hit: str | None = Field(default=None, description="Shown when the point was made.")
    feedback_miss: str | None = Field(default=None, description="Shown when it was not.")

    @model_validator(mode="after")
    def _check_accepted(self) -> KeyPoint:
        seen: set[str] = set()
        for form in self.accepted:
            cleaned = form.strip()
            if not cleaned:
                raise ValueError("an accepted form cannot be blank")
            if _UNMATCHABLE_FORM in cleaned:
                raise ValueError("an accepted form cannot contain '*'")
            if cleaned.startswith("/") and cleaned.endswith("/"):
                raise ValueError("an accepted form cannot be a /regex/")
            if len(cleaned) > _MAX_ACCEPTED_CHARS:
                raise ValueError(
                    f"an accepted form cannot exceed {_MAX_ACCEPTED_CHARS} characters"
                )
            lowered = cleaned.lower()
            if lowered in seen:
                raise ValueError("accepted forms must be unique")
            seen.add(lowered)
        return self


class ShortAnswerItem(_QuestionBase):
    """A short constructed-response question, marked on key-point coverage.

    The learner writes two or three sentences in their own words, and the package
    marks it by checking whether each key point's accepted forms appear in that
    text. This is a **points-based mark scheme** — the instrument exam boards use
    when a salient point corresponds to a mark — automated, not an essay grader.

    The distinction matters because there is no model at grading time: a packaged
    quiz runs inside the LMS, offline. So nothing here judges reasoning, ordering
    or coherence, and an answer that is correct in entirely different words will
    score zero. What that does and does not claim is set out in
    ``docs/adr/0006-short-answer-questions.md``; the results screen shows the
    learner which points were found and the full model answer, so the marking is
    never a black box.
    """

    type: Literal["short_answer"] = "short_answer"
    prompt: str = Field(description="The question stem.")
    key_points: list[KeyPoint] = Field(
        min_length=2,
        max_length=4,
        description=(
            "The mark scheme. Two is the floor — one key point is a fill-in-the-blank "
            "in disguise. Four is the ceiling because agreement between markers decays "
            "as the number of marks per item grows."
        ),
    )
    model_answer: str = Field(
        description="A complete answer, assembled from the source; shown after submission."
    )
    min_chars: int = Field(
        default=0,
        ge=0,
        description="Minimum length before the answer may be submitted (H5P behaviour.minimumLength).",
    )
    max_chars: int = Field(
        default=1000,
        ge=1,
        description="Maximum length of the answer (H5P behaviour.maximumLength).",
    )

    @model_validator(mode="after")
    def _check_key_points(self) -> ShortAnswerItem:
        ids = [k.id for k in self.key_points]
        if len(set(ids)) != len(ids):
            raise ValueError("key point ids must be unique")
        texts = [k.text.strip().lower() for k in self.key_points]
        if len(set(texts)) != len(texts):
            raise ValueError("key point texts must be unique")
        # Unlike the other three types, `points` is not independent here. H5P.Essay
        # scores out of the sum of its keyword points and our SCORM grader awards
        # the same weights, so letting the two disagree would make one answer score
        # differently in the two packages.
        total = float(sum(k.weight for k in self.key_points))
        if abs(self.points - total) > 1e-9:
            raise ValueError(
                f"points ({self.points}) must equal the sum of key point weights ({total})"
            )
        if self.min_chars > self.max_chars:
            raise ValueError("min_chars cannot exceed max_chars")
        return self


Question = Annotated[
    Union[MCQItem, MatchItem, FillBlankItem, ShortAnswerItem], Field(discriminator="type")
]


class AssessmentSource(BaseModel):
    """A pointer back to the document this assessment was derived from."""

    filename: str
    title: str | None = None
    page_count: int = Field(description="Pages, slides or sheets in the source.")


class ScoreBand(BaseModel):
    """One rubric band: a score range, as a percentage, and what to tell the learner.

    ``from``/``to`` are Python keywords, hence the ``_percent`` suffixes; the H5P
    emitter renames them when it writes ``endGame.overallFeedback``.
    """

    from_percent: int = Field(ge=0, le=100, description="Lower bound, inclusive.")
    to_percent: int = Field(ge=0, le=100, description="Upper bound, inclusive.")
    feedback: str

    @model_validator(mode="after")
    def _check_range(self) -> ScoreBand:
        if self.from_percent > self.to_percent:
            raise ValueError("a band cannot end before it starts")
        return self


def default_score_bands(pass_percentage: int) -> list[ScoreBand]:
    """The rubric we use when a caller supplies none: pass/fail around the threshold.

    Deterministic in Python rather than generated: band text is not drawn from the
    source document, so it is not something the grounding gate could ever verify.
    """
    if pass_percentage == 0:
        # Everything passes, so a "keep practising" band would span [0, -1].
        return [ScoreBand(from_percent=0, to_percent=100, feedback="Assessment complete.")]
    return [
        ScoreBand(
            from_percent=0,
            to_percent=pass_percentage - 1,
            feedback="Keep practising — review the material and try again.",
        ),
        ScoreBand(from_percent=pass_percentage, to_percent=100, feedback="Passed — well done."),
    ]


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
    score_bands: list[ScoreBand] = Field(
        default_factory=list,
        description=(
            "The rubric: score bands over the achieved percentage. Must tile 0-100 with no "
            "gaps or overlaps. Empty means the emitters derive a default from pass_percentage."
        ),
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

    @model_validator(mode="after")
    def _check_score_bands(self) -> AssessmentSet:
        """Bands must tile 0-100 exactly.

        A gap is not a harmless omission: H5P picks the band containing the score and
        silently shows nothing when none matches, so a hole here is invisible until a
        learner lands in it. Reject it at the contract instead.
        """
        if not self.score_bands:
            return self
        ordered = sorted(self.score_bands, key=lambda b: b.from_percent)
        if ordered[0].from_percent != 0 or ordered[-1].to_percent != 100:
            raise ValueError("score bands must cover 0 to 100")
        for previous, current in zip(ordered, ordered[1:]):
            if current.from_percent != previous.to_percent + 1:
                raise ValueError("score bands must be contiguous and must not overlap")
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
