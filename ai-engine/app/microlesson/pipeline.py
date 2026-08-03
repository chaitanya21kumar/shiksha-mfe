"""Turn a document, a transcript, or plain text into a `MicroLesson`.

Week 9 of the milestone schedule asks for three things — a source selector, content
extraction, and script generation — and they are the three stages below.

**Selecting the source** is a matter of which extractor runs. All three produce the
same `Section` list, so everything after that point is identical no matter what the
caller uploaded. That is the whole point of the shared section type: the generator
does not know or care whether it is looking at a slide deck or a lecture.

**Extraction is deterministic.** A document splits at its headings, a transcript
splits at its chapters, free-form text splits at its blank lines. None of that
involves the model, so the same input always yields the same number of steps in the
same order — which is what makes the output testable and what stops a lesson
quietly gaining or losing a step between runs.

**Only the words are generated**, in one call over all the sections at once, keyed
by section number. A section the model returns nothing for falls back to its own
source text rather than disappearing, and says so in the warnings. A step the model
invents for a section that does not exist is discarded: the lesson has exactly as
many steps as the source had units, always.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..chaptering.schema import ChapteredTranscript
from ..ingestion.schema import ParsedDocument
from ..ingestion.sections import Section, bounded, sections_from_document
from ..summarization.llm_client import LLMBadResponse, chat_json_for
from ..summarization.pipeline import EmptyDocumentError, GenerationConfig
from . import prompts
from .schema import MAX_STEPS, MIN_STEP_CHARS, LessonSource, LessonStep, MicroLesson

logger = logging.getLogger("ai_engine.microlesson")

#: Free-form text is split on blank lines, which is how people already separate
#: ideas when they type. A single wall of text stays one step rather than being
#: guessed at.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n+")
#: A heading-ish first line of a pasted block: short, and not ending in a full stop.
_MAX_INFERRED_TITLE_WORDS = 10

#: Enough of the section to stand in for a step the model skipped, without pasting
#: a whole page onto a slide.
_FALLBACK_CHARS = 240


# --------------------------------------------------------------------------- #
# What the model is allowed to return
# --------------------------------------------------------------------------- #
class _Step(BaseModel):
    index: int
    title: str = ""
    bullets: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("bullets", mode="before")
    @classmethod
    def _coerce_bullets(cls, value: object) -> object:
        # A model that returns one string where a list was asked for has still
        # given us something usable; splitting it is better than losing the step.
        if value is None:
            return []
        if isinstance(value, str):
            return [line.strip(" -•\t") for line in value.splitlines() if line.strip()]
        return value

    @field_validator("notes", mode="before")
    @classmethod
    def _coerce_notes(cls, value: object) -> object:
        if value is None:
            return ""
        if isinstance(value, list):
            return " ".join(str(item).strip() for item in value if str(item).strip())
        return value


class _LessonResponse(BaseModel):
    objectives: list[str] = Field(default_factory=list)
    steps: list[_Step] = Field(default_factory=list)

    @field_validator("objectives", mode="before")
    @classmethod
    def _coerce_objectives(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return value


# --------------------------------------------------------------------------- #
# The source selector: three extractors, one output shape
# --------------------------------------------------------------------------- #
def sections_from_transcript(chaptered: ChapteredTranscript) -> list[Section]:
    """One section per chapter, in order.

    The chapters were already computed from the transcript's own timings in
    Module C, so the lesson inherits a structure that came from where the speaker
    actually paused rather than from anyone's guess.
    """
    return [
        Section(source_index=chapter.index, title=chapter.title, text=chapter.text)
        for chapter in chaptered.chapters
        if chapter.text.strip()
    ]


def _looks_like_heading(line: str) -> bool:
    """Short, and not punctuated like a sentence."""
    stripped = line.strip()
    return (
        bool(stripped)
        and len(stripped.split()) <= _MAX_INFERRED_TITLE_WORDS
        and not stripped.endswith((".", "!", "?", ",", ";", ":"))
    )


def sections_from_text(text: str) -> list[Section]:
    """Split pasted text on blank lines, one section per paragraph block.

    Two heading conventions are honoured, because people use both and picking one
    loses the other:

    - a heading on the first line of a block, with the body beneath it
    - a heading standing alone as its own block, with a blank line before the body

    A heading in the last block is not treated as a heading, because there is
    nothing for it to head. It stays ordinary text and is then dropped for being
    too short to teach from, which is the right outcome: a lesson should not end on
    a step whose only content is its own title.

    The second convention matters more than it looks. Handling only the first drops every
    standalone heading, because a lone word is below the minimum length for a step
    — so the lesson silently loses the author's own section titles and the model
    invents replacements. Found by running the generator on ordinary notes and
    noticing the titles had changed.
    """
    blocks = [b.strip() for b in _PARAGRAPH_BREAK.split(text.strip()) if b.strip()]
    sections: list[Section] = []
    carried: str | None = None  # a heading block waiting for its body

    for position, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        # A whole block that is just a heading belongs to whatever comes next.
        if len(lines) == 1 and _looks_like_heading(block) and position < len(blocks):
            carried = block
            continue

        title = carried
        carried = None
        if title is None and len(lines) > 1 and _looks_like_heading(lines[0]):
            title = lines[0].strip()
            block = "\n".join(lines[1:]).strip()
        sections.append(Section(source_index=len(sections) + 1, title=title, text=block))
    return sections


def _usable(sections: list[Section]) -> list[Section]:
    """Drop sections with too little in them to teach from.

    A slide carrying only a title, or a stray heading with no body, would otherwise
    become a step with nothing on it.
    """
    return [s for s in sections if len(s.text.strip()) >= MIN_STEP_CHARS]


def _lesson_title(explicit: str | None, sections: list[Section], fallback: str) -> str:
    """The lesson's own title: what the caller asked for, else the source's.

    Never generated. A caller who names their lesson should get that name back, and
    a document's own first heading is a better title than an invented one.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    if sections and sections[0].title:
        return sections[0].title
    return fallback


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
async def _generate(
    client: httpx.AsyncClient,
    config: GenerationConfig,
    sections: list[Section],
    lesson_title: str,
    warnings: list[str],
) -> _LessonResponse:
    """Run the single generation call, or degrade to nothing with a warning.

    Connectivity and timeout errors propagate so the caller fails fast; unusable
    output leaves every step to its fallback, which is still a lesson.
    """
    numbered = [(i, s.title, s.text) for i, s in enumerate(sections, start=1)]
    try:
        raw = await chat_json_for(
            client,
            config,
            system=prompts.SYSTEM,
            user=prompts.lesson_prompt(numbered, lesson_title),
        )
        return _LessonResponse.model_validate(raw)
    except (LLMBadResponse, ValidationError) as exc:
        logger.warning("Could not generate the lesson: %s", exc)
        warnings.append(f"Could not generate the lesson body: {exc}")
        return _LessonResponse()


def _fallback_step(number: int, section: Section) -> LessonStep:
    """A step built from the source alone, for a section the model skipped.

    Deliberately the section's own words rather than anything invented: a learner
    seeing the source text is worse than a polished step, and far better than a
    step that silently vanished from the lesson.
    """
    text = section.text.strip()
    body = text if len(text) <= _FALLBACK_CHARS else text[:_FALLBACK_CHARS].rsplit(" ", 1)[0] + "…"
    return LessonStep(
        index=number,
        title=section.title or f"Step {number}",
        bullets=[body],
        notes="",
        source_index=section.source_index,
    )


def _assemble(
    sections: list[Section], generated: dict[int, _Step], warnings: list[str]
) -> list[LessonStep]:
    """One step per section, in order, whatever the model did or did not return."""
    steps: list[LessonStep] = []
    missing: list[int] = []
    for number, section in enumerate(sections, start=1):
        written = generated.get(number)
        # The author's heading wins over the model's. If someone wrote
        # "Evaporation", the step is called "Evaporation", not "Evaporation
        # Process" — retitling a section the author already named is a change
        # nobody asked for, and it breaks the match between the lesson and the
        # document it came from. The model's heading is the fallback for a section
        # that had none.
        title = section.title or (written.title.strip() if written else "") or f"Step {number}"
        bullets = [b.strip() for b in (written.bullets if written else []) if b and b.strip()]
        if written is None or not bullets:
            missing.append(number)
            steps.append(_fallback_step(number, section))
            continue
        steps.append(
            LessonStep(
                index=number,
                title=title,
                bullets=bullets,
                notes=(written.notes or "").strip(),
                source_index=section.source_index,
            )
        )
    if missing:
        warnings.append(
            f"The model returned nothing usable for step(s) {', '.join(map(str, missing))}; "
            "they fall back to the source text."
        )
    extra = sorted(set(generated) - set(range(1, len(sections) + 1)))
    if extra:
        # A step for a section that does not exist has nothing behind it, so there
        # is nowhere for a reviewer to check it. Drop it and say so.
        warnings.append(
            f"Discarded {len(extra)} step(s) the model invented for section(s) "
            f"{', '.join(map(str, extra))}, which the source does not have."
        )
    return steps


async def build_micro_lesson(
    client: httpx.AsyncClient,
    sections: list[Section],
    source: LessonSource,
    config: GenerationConfig,
    *,
    title: str | None = None,
    language: str = "en",
) -> MicroLesson:
    """Turn already-extracted sections into a lesson.

    Takes sections rather than a source so the three entry points share one path;
    the selector above decides which extractor produced them.
    """
    usable = _usable(sections)
    if not usable:
        raise EmptyDocumentError("The source has no section long enough to build a lesson from.")

    kept, warnings = bounded(usable, MAX_STEPS, config.max_source_chars, verb="used")
    lesson_title = _lesson_title(title, kept, source.filename or "Micro-lesson")

    response = await _generate(client, config, kept, lesson_title, warnings)
    generated = {step.index: step for step in response.steps}
    steps = _assemble(kept, generated, warnings)

    return MicroLesson(
        lesson_id=str(uuid.uuid4()),
        source=source,
        title=lesson_title,
        language=language,
        generator=config.provider,
        model=config.model,
        generated_at=datetime.now(timezone.utc),
        objectives=[o.strip() for o in response.objectives if o and o.strip()],
        steps=steps,
        warnings=warnings,
    )


async def lesson_from_document(
    client: httpx.AsyncClient,
    doc: ParsedDocument,
    config: GenerationConfig,
    *,
    title: str | None = None,
    language: str = "en",
) -> MicroLesson:
    sections = sections_from_document(doc)
    source = LessonSource(
        kind="document",
        filename=doc.source.filename,
        title=doc.source.title,
        unit_count=len(sections),
    )
    return await build_micro_lesson(
        client, sections, source, config, title=title or doc.source.title, language=language
    )


async def lesson_from_transcript(
    client: httpx.AsyncClient,
    chaptered: ChapteredTranscript,
    config: GenerationConfig,
    *,
    title: str | None = None,
    language: str = "en",
) -> MicroLesson:
    sections = sections_from_transcript(chaptered)
    source = LessonSource(
        kind="transcript",
        filename=chaptered.source.filename,
        unit_count=len(sections),
    )
    return await build_micro_lesson(
        client, sections, source, config, title=title, language=language
    )


async def lesson_from_text(
    client: httpx.AsyncClient,
    text: str,
    config: GenerationConfig,
    *,
    title: str | None = None,
    language: str = "en",
) -> MicroLesson:
    sections = sections_from_text(text)
    source = LessonSource(kind="text", unit_count=len(sections))
    return await build_micro_lesson(
        client, sections, source, config, title=title, language=language
    )
