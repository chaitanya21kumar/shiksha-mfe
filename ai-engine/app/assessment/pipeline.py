"""Turn a parsed document into a source-grounded `AssessmentSet` using the model.

The flow mirrors the other generative layers but adds a grounding gate, because
Module B's contract is "questions generated strictly from the source, no
hallucinations":

1. Split the document — deterministically, in Python — into numbered sections
   (one per page/slide, speaker notes folded in), so each question can point back
   to the `Page` it came from.
2. Run one focused generation per requested question type (multiple-choice,
   match-the-pair, fill-in-the-blank). The types are independent, so one type
   coming back unusable is a warning, not a failed request — the others succeed.
3. Verify each question is grounded: the model must quote an ``evidence`` span,
   and that span must actually appear in the source. Anything that cannot be
   grounded is dropped with a warning rather than shipped.
4. Assign every id ourselves (question, choice, match term, blank). xAPI and
   SCORM build their response patterns from these ids, so they must be unique and
   free of the reserved ``[,] [.] [:]`` delimiters — which model-invented ids are
   not guaranteed to be.

Connectivity and timeout failures propagate so the request fails fast; unusable
model output degrades to fewer (or no) questions with a warning.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal

import httpx
from pydantic import AliasChoices, BaseModel, Field, ValidationError, field_validator

from ..ingestion.schema import Block, BlockKind, Page, ParsedDocument
from ..summarization.llm_client import LLMBadResponse, chat_json
from ..summarization.pipeline import EmptyDocumentError, GenerationConfig
from . import prompts
from .schema import (
    AssessmentSet,
    AssessmentSource,
    Blank,
    Choice,
    FillBlankItem,
    MatchItem,
    MatchSource,
    MatchTarget,
    MCQItem,
    Question,
)

logger = logging.getLogger("ai_engine.assessment")

QuestionType = Literal["mcq", "match", "fill_blank"]
ALL_TYPES: tuple[QuestionType, ...] = ("mcq", "match", "fill_blank")

# Bound the work so a huge document cannot produce an unbounded prompt or an
# unbounded number of generations.
_MAX_SECTIONS = 40
_MAX_PER_TYPE = 20
# An evidence quote shorter than this (after normalising) is too weak to trust as
# grounding, so the question is dropped.
_MIN_EVIDENCE_CHARS = 8

_WHITESPACE = re.compile(r"\s+")
_LATEX_MARKERS = ("\\(", "\\[", "$$")


# --------------------------------------------------------------------------- #
# Deterministic sectioning
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Section:
    """One block of source the questions can be drawn from and grounded against."""

    source_index: int
    title: str | None
    text: str


def _block_text(block: Block) -> str | None:
    """Render one block as plain text, or None if it carries no text."""
    if block.kind in (BlockKind.heading, BlockKind.paragraph):
        return block.text
    if block.kind is BlockKind.list and block.items:
        return "\n".join(f"- {item}" for item in block.items)
    if block.kind is BlockKind.table and block.rows:
        return "\n".join(" | ".join(row) for row in block.rows)
    return None


def _page_section(page: Page) -> _Section | None:
    """Fold a whole page/slide into one section; its first heading is the title."""
    title: str | None = None
    title_taken = False
    parts: list[str] = []
    for block in page.blocks:
        if not title_taken and block.kind is BlockKind.heading:
            title = (block.text or "").strip() or None
            title_taken = True
            continue
        text = _block_text(block)
        if text:
            parts.append(text)
    if page.notes and page.notes.strip():
        parts.append(page.notes.strip())
    text = "\n".join(parts).strip()
    if not text:
        return None
    return _Section(source_index=page.index, title=title, text=text)


def _build_sections(doc: ParsedDocument) -> list[_Section]:
    """Split a document into groundable sections, in reading order."""
    sections: list[_Section] = []
    for page in doc.pages:
        section = _page_section(page)
        if section:
            sections.append(section)
    return sections


def _bounded(
    sections: list[_Section], max_sections: int, max_chars: int
) -> tuple[list[_Section], list[str]]:
    """Cap the sections (count and total characters) so the prompt stays bounded."""
    warnings: list[str] = []
    if len(sections) > max_sections:
        warnings.append(
            f"Document had {len(sections)} sections; used the first {max_sections}."
        )
        sections = sections[:max_sections]

    bounded: list[_Section] = []
    used = 0
    truncated = False
    for sec in sections:
        remaining = max_chars - used
        if remaining <= 0:
            truncated = True
            break
        text = sec.text if len(sec.text) <= remaining else sec.text[:remaining]
        truncated = truncated or len(text) < len(sec.text)
        bounded.append(_Section(sec.source_index, sec.title, text))
        used += len(text)
    if truncated:
        warnings.append(
            f"Source text was truncated to about {max_chars} characters before generating."
        )
    return bounded, warnings


# --------------------------------------------------------------------------- #
# Grounding helpers
# --------------------------------------------------------------------------- #
def _normalise(text: str) -> str:
    """Lower-case and collapse whitespace so quotes match despite formatting."""
    return _WHITESPACE.sub(" ", text).strip().lower()


def _contains(haystacks: list[str], needle: str) -> bool:
    """True if the (already normalised) needle appears in any haystack."""
    return bool(needle) and any(needle in hay for hay in haystacks)


def _is_grounded(evidence: str, haystacks: list[str]) -> bool:
    """True if the (normalised) evidence quote appears in one of the haystacks."""
    needle = _normalise(evidence)
    if len(needle) < _MIN_EVIDENCE_CHARS:
        return False
    return _contains(haystacks, needle)


def _has_latex(*texts: str | None) -> bool:
    joined = " ".join(t for t in texts if t)
    return any(marker in joined for marker in _LATEX_MARKERS)


def _attribute_section(
    evidence_norm: str,
    claimed: int | None,
    norm_sections: dict[int, str],
    source_index_of: dict[int, int],
) -> int | None:
    """Attribute a question to the source page whose text actually contains the
    evidence.

    The model's claimed ``source_section`` is preferred, but never trusted
    blindly: if the evidence is not in the claimed section, the section that does
    contain it is used instead, and ``None`` is returned when the evidence only
    matches across section boundaries (so a page is never guessed).
    """
    if not evidence_norm:
        return None
    if claimed in norm_sections and evidence_norm in norm_sections[claimed]:
        return source_index_of[claimed]
    for number, text in norm_sections.items():
        if evidence_norm in text:
            return source_index_of[number]
    return None


# --------------------------------------------------------------------------- #
# Internal response shapes (lenient; validated before we trust them)
# --------------------------------------------------------------------------- #
class _OptionOut(BaseModel):
    text: str = ""
    is_correct: bool = False
    feedback: str | None = None


class _PairOut(BaseModel):
    left: str = ""
    right: str = ""


class _BlankOut(BaseModel):
    answers: list[str] = Field(default_factory=list)
    tip: str | None = None


class _QuestionOut(BaseModel):
    """The union of fields any question type may return; unused ones stay empty."""

    source_section: int | None = None
    evidence: str = ""
    prompt: str = Field(
        default="", validation_alias=AliasChoices("prompt", "question", "stem", "instruction")
    )
    explanation: str | None = None
    # multiple-choice
    options: list[_OptionOut] = Field(
        default_factory=list, validation_alias=AliasChoices("options", "answers", "choices")
    )
    # match-the-pair
    pairs: list[_PairOut] = Field(
        default_factory=list, validation_alias=AliasChoices("pairs", "matches")
    )
    distractors: list[str] = Field(default_factory=list)
    # fill-in-the-blank
    text: str = Field(default="", validation_alias=AliasChoices("text", "sentence"))
    blanks: list[_BlankOut] = Field(
        default_factory=list, validation_alias=AliasChoices("blanks", "gaps")
    )

    @field_validator("distractors", mode="before")
    @classmethod
    def _clean_distractors(cls, value: object) -> list[str]:
        # Distractors are optional decoration; keep only the string ones so a match
        # question is not lost when the model returns pair-objects here by mistake.
        if not isinstance(value, list):
            return []
        cleaned: list[str] = []
        for item in value:
            if isinstance(item, str):
                cleaned.append(item)
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                cleaned.append(str(item))
        return cleaned


# --------------------------------------------------------------------------- #
# Assembly: one validated model item -> one typed, grounded Question
# --------------------------------------------------------------------------- #
def _clean(value: str | None) -> str:
    return (value or "").strip()


def _grounded_answers(raw_answers: list[str], answer_sources: list[str]) -> list[str]:
    """The accepted answers that trace to the source, de-duplicated, in order.

    An alternative the model invents but the source does not contain is dropped,
    and case-insensitive duplicates are collapsed.
    """
    grounded: list[str] = []
    seen: set[str] = set()
    for answer in (_clean(a) for a in raw_answers):
        norm = _normalise(answer)
        if answer and norm not in seen and _contains(answer_sources, norm):
            grounded.append(answer)
            seen.add(norm)
    return grounded


def _assemble_mcq(
    out: _QuestionOut, qid: str, source_index: int | None, haystacks: list[str], warnings: list[str]
) -> MCQItem | None:
    prompt = _clean(out.prompt)
    if not prompt or not _is_grounded(out.evidence, haystacks):
        warnings.append("Dropped a multiple-choice question that was empty or not grounded in the source.")
        return None
    options = [o for o in out.options if _clean(o.text)]
    if len(options) < 2:
        warnings.append("Dropped a multiple-choice question with fewer than two options.")
        return None
    choices = [
        Choice(id=f"{qid}-c{i}", text=_clean(o.text), is_correct=bool(o.is_correct), feedback=(o.feedback or None))
        for i, o in enumerate(options, start=1)
    ]
    try:
        return MCQItem(
            id=qid,
            source_index=source_index,
            prompt=prompt,
            choices=choices,
            single_answer=True,
            explanation=(out.explanation or None),
            has_latex=_has_latex(prompt, *(o.text for o in options)),
        )
    except ValidationError:
        warnings.append("Dropped a malformed multiple-choice question.")
        return None


def _assemble_match(
    out: _QuestionOut, qid: str, source_index: int | None, haystacks: list[str], warnings: list[str]
) -> MatchItem | None:
    prompt = _clean(out.prompt)
    if not prompt or not _is_grounded(out.evidence, haystacks):
        warnings.append("Dropped a match-the-pair question that was empty or not grounded in the source.")
        return None
    pairs = [(_clean(p.left), _clean(p.right)) for p in out.pairs]
    pairs = [(left, right) for left, right in pairs if left and right]
    if len(pairs) < 2:
        warnings.append("Dropped a match-the-pair question with fewer than two pairs.")
        return None
    sources: list[MatchSource] = []
    targets: list[MatchTarget] = []
    for i, (left, right) in enumerate(pairs, start=1):
        tid = f"{qid}-t{i}"
        targets.append(MatchTarget(id=tid, text=right))
        sources.append(MatchSource(id=f"{qid}-s{i}", text=left, target_id=tid))
    # Add distractors, skipping any that duplicate an existing target (a repeated
    # option is redundant and would just be ambiguous to the learner).
    seen_targets = {t.text.lower() for t in targets}
    for distractor in (_clean(d) for d in out.distractors):
        if distractor and distractor.lower() not in seen_targets:
            targets.append(MatchTarget(id=f"{qid}-t{len(targets) + 1}", text=distractor))
            seen_targets.add(distractor.lower())
    try:
        return MatchItem(
            id=qid,
            source_index=source_index,
            prompt=prompt,
            sources=sources,
            targets=targets,
            explanation=(out.explanation or None),
            has_latex=_has_latex(prompt, *(t.text for t in targets), *(s.text for s in sources)),
        )
    except ValidationError:
        warnings.append("Dropped a malformed match-the-pair question.")
        return None


def _assemble_fill_blank(
    out: _QuestionOut, qid: str, source_index: int | None, haystacks: list[str], warnings: list[str]
) -> FillBlankItem | None:
    text = _clean(out.text)
    if not text or not _is_grounded(out.evidence, haystacks):
        warnings.append("Dropped a fill-in-the-blank question that was empty or not grounded in the source.")
        return None
    # Every accepted answer must trace to the source. The blanked word is removed
    # from the quoted sentence, and any alternative must appear in the source too —
    # otherwise the model can slip an incorrect synonym into the answer key (e.g.
    # accepting "petals" as an alternative for "leaves").
    answer_sources = [_normalise(out.evidence), *haystacks]
    blanks: list[Blank] = []
    for i, blank in enumerate(out.blanks, start=1):
        grounded = _grounded_answers(blank.answers, answer_sources)
        if not grounded:
            warnings.append("Dropped a fill-in-the-blank whose answer was not found in the source.")
            return None
        blanks.append(Blank(id=f"{qid}-b{i}", answers=grounded, tip=(blank.tip or None)))
    if not blanks:
        warnings.append("Dropped a fill-in-the-blank question with no usable answers.")
        return None
    try:
        return FillBlankItem(
            id=qid,
            source_index=source_index,
            prompt=(out.prompt or None),
            text=text,
            blanks=blanks,
            explanation=(out.explanation or None),
            has_latex=_has_latex(text, *(a for b in blanks for a in b.answers)),
        )
    except ValidationError:
        warnings.append("Dropped a fill-in-the-blank whose [[n]] markers did not match its blanks.")
        return None


@dataclass(frozen=True)
class _TypeSpec:
    prompt: Callable[[list[tuple[int, str | None, str]], int], str]
    assemble: Callable[[_QuestionOut, str, int | None, list[str], list[str]], Question | None]


_SPECS: dict[QuestionType, _TypeSpec] = {
    "mcq": _TypeSpec(prompts.mcq_prompt, _assemble_mcq),
    "match": _TypeSpec(prompts.match_prompt, _assemble_match),
    "fill_blank": _TypeSpec(prompts.fill_blank_prompt, _assemble_fill_blank),
}


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
async def _generate_type(
    client: httpx.AsyncClient,
    config: GenerationConfig,
    qtype: QuestionType,
    numbered: list[tuple[int, str | None, str]],
    count: int,
    warnings: list[str],
) -> list[_QuestionOut]:
    """Run one generation for a question type and return its validated items.

    Each question is validated on its own, so one malformed item (an 8B model
    occasionally drifts on a single item's shape) is skipped with a warning rather
    than discarding the whole batch. Connectivity and timeout errors propagate (the
    caller fails the request); an unusable response degrades to an empty list.
    """
    try:
        raw = await chat_json(
            client,
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            system=prompts.SYSTEM,
            user=_SPECS[qtype].prompt(numbered, count),
            temperature=config.temperature,
        )
    except LLMBadResponse as exc:
        logger.warning("Could not generate %s questions: %s", qtype, exc)
        warnings.append(f"Could not generate {qtype} questions: {exc}")
        return []

    raw_items = raw.get("questions")
    if not isinstance(raw_items, list):
        raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        warnings.append(f"Could not generate {qtype} questions: the response had no question list.")
        return []

    parsed: list[_QuestionOut] = []
    for item in raw_items[:count]:  # never assemble more than were asked for
        try:
            parsed.append(_QuestionOut.model_validate(item))
        except ValidationError:
            warnings.append(f"Skipped a malformed {qtype} question returned by the model.")
    return parsed


async def generate_assessment(
    client: httpx.AsyncClient,
    doc: ParsedDocument,
    config: GenerationConfig,
    *,
    question_types: list[QuestionType],
    count: int,
    language: str,
) -> AssessmentSet:
    """Derive a source-grounded assessment from a parsed document."""
    sections = _build_sections(doc)
    if not sections:
        raise EmptyDocumentError("The document contains no text to build questions from.")

    sections, warnings = _bounded(sections, _MAX_SECTIONS, config.max_source_chars)
    if not sections:
        raise EmptyDocumentError(
            "The document has no text within the size limit to build questions from."
        )

    numbered = [(i, sec.title, sec.text) for i, sec in enumerate(sections, start=1)]
    norm_sections = {i: _normalise(sec.text) for i, sec in enumerate(sections, start=1)}
    source_index_of = {i: sec.source_index for i, sec in enumerate(sections, start=1)}
    norm_all = _normalise("\n".join(sec.text for sec in sections))

    per_type = min(max(count, 1), _MAX_PER_TYPE)
    questions: list[Question] = []
    counter = 1
    for qtype in question_types:
        items = await _generate_type(client, config, qtype, numbered, per_type, warnings)
        for out in items:
            evidence_norm = _normalise(out.evidence)
            # Grounding is checked against the whole document; attribution finds
            # the specific page the evidence came from (the model's claimed
            # section is verified, not trusted).
            source_index = _attribute_section(evidence_norm, out.source_section, norm_sections, source_index_of)
            qid = f"q{counter}"
            item = _SPECS[qtype].assemble(out, qid, source_index, [norm_all], warnings)
            if item is not None:
                questions.append(item)
                counter += 1

    return AssessmentSet(
        assessment_id=str(uuid.uuid4()),
        source=AssessmentSource(
            filename=doc.source.filename,
            title=doc.source.title,
            page_count=doc.source.page_count,
        ),
        language=language,
        generator=config.provider,
        model=config.model,
        generated_at=datetime.now(timezone.utc),
        questions=questions,
        warnings=warnings,
    )
