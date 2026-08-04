"""Maps an `AssessmentSet` onto a SCORM 1.2 package.

The domain half of SCORM packaging. The format mechanics — the data model, the
manifest, the ZIP — live in `app.packaging.scorm`.

Three things make this genuinely different from `emit/h5p.py`, and they are worth
knowing before reading the code:

**We own the player, so nothing is ever dropped.** H5P must refuse a question it
cannot render, because H5P's own libraries do the rendering. Here the package
carries its own player, so every question renders and scores. When SCORM's
*reporting* cannot express something — more than 36 options, a pattern over 255
characters — we skip that interaction and warn. The report degrades; the
assessment never does.

**`points` is honoured exactly.** H5P has no per-question weight and scores on its
own scale, which `emit_h5p` has to warn about. Our grader multiplies by `points`
directly, so `max_points` is authoritative and no such warning exists.

**The patterns are SCORM 1.2, not 2004.** A plain comma between responses, a plain
period inside a matching pair. The ``[,]``/``[.]``/``{case_matters=}`` forms are
2004 and would be stored verbatim and rendered as junk — Moodle does not validate
the format (it literally ships ``CMIFeedback = CMIString256; // This must be
redefined``) and Open edX ignores interactions entirely. Nothing catches a mistake
here, so the encoders below are the only guard.
"""

from __future__ import annotations

import html
import json
from importlib import resources
from typing import NamedTuple

from ...packaging.naming import sanitise_filename
from ...packaging.scorm import (
    LAUNCH_NAME,
    RESPONSE_MAX_CHARS,
    WEIGHTING_MAX,
    build_manifest,
    response_char,
    write_scorm,
)
from ..schema import (
    AssessmentSet,
    FillBlankItem,
    MatchItem,
    MCQItem,
    Question,
    ShortAnswerItem,
    default_score_bands,
)
from .errors import EmptyAssessmentError

#: The player's browser-side half, shipped as package data on `packaging.scorm`
#: itself — `assets/` is a data directory, not an importable package, and reading
#: it through its parent keeps the wheel from depending on namespace discovery.
_ASSETS_PACKAGE = "app.packaging.scorm"
_ASSETS_DIR = "assets"

_PLAYER_FILES = {
    "scorm/api.js": "api.js",
    "scorm/player.js": "player.js",
    "scorm/player.css": "player.css",
}


class ScormPackage(NamedTuple):
    """A built SCORM 1.2 package and anything the caller should know about it."""

    content: bytes
    filename: str
    warnings: list[str]


def _asset(name: str) -> str:
    return resources.files(_ASSETS_PACKAGE).joinpath(_ASSETS_DIR, name).read_text(encoding="utf-8")


def _script_safe(payload: str) -> str:
    """Make JSON safe to inline inside a ``<script>`` block.

    HTML escaping does not apply inside a script element — the tokenizer simply
    ends the block at the first literal ``</script``, so an uploaded document
    containing one would break out of the data island and into markup. These three
    escapes are valid inside JSON strings and close that hole, and the ``<!--``
    comment quirk with it.
    """
    return payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# --- interaction encoding ----------------------------------------------------


def _weighting(points: float, parts: int = 1) -> tuple[str, str | None]:
    """Render `points` as a CMIDecimal weighting, clamped, with a reason if clamped."""
    share = points / parts if parts else points
    if share > WEIGHTING_MAX:
        return f"{WEIGHTING_MAX:g}", (
            f"weighting clamped to {WEIGHTING_MAX:g}: SCORM 1.2 caps a decimal at "
            "three integer digits"
        )
    return f"{share:g}", None



def _too_many_options(item_id: str, what: str) -> str:
    """The warning for a question SCORM cannot identify, but we still ask and score."""
    return (
        f"Not reporting {item_id} to the LMS: it has more than 36 {what} and SCORM 1.2 "
        "identifies each one with a single character. The question is still asked and "
        "still scored."
    )


def _pattern_too_long(item_id: str) -> str:
    return f"Not reporting {item_id} to the LMS: its answer pattern exceeds 255 characters."


def _mcq_interaction(item: MCQItem, warnings: list[str]) -> dict[str, object] | None:
    """`choice`: single-character ids, comma-separated. No brackets — that is 2004."""
    try:
        chars = [response_char(index) for index, _ in enumerate(item.choices)]
    except ValueError:
        warnings.append(_too_many_options(item.id, "options"))
        return None

    correct = ",".join(
        char for char, choice in zip(chars, item.choices) if choice.is_correct
    )
    if len(correct) > RESPONSE_MAX_CHARS:
        warnings.append(_pattern_too_long(item.id))
        return None

    weighting, clamped = _weighting(item.points)
    if clamped:
        warnings.append(f"{item.id}: {clamped}.")
    return {"type": "choice", "correct_responses": [correct], "weighting": weighting}


def _match_interaction(item: MatchItem, warnings: list[str]) -> dict[str, object] | None:
    """`matching`: ``source.target`` pairs — period within a pair, comma between."""
    try:
        source_chars = {s.id: response_char(i) for i, s in enumerate(item.sources)}
        target_chars = {t.id: response_char(i) for i, t in enumerate(item.targets)}
    except ValueError:
        warnings.append(_too_many_options(item.id, "terms"))
        return None

    pattern = ",".join(
        f"{source_chars[s.id]}.{target_chars[s.target_id]}"
        for s in item.sources
        if s.target_id in target_chars
    )
    if len(pattern) > RESPONSE_MAX_CHARS:
        warnings.append(_pattern_too_long(item.id))
        return None

    weighting, clamped = _weighting(item.points)
    if clamped:
        warnings.append(f"{item.id}: {clamped}.")
    return {
        "type": "matching",
        "correct_responses": [pattern],
        "weighting": weighting,
        "source_chars": source_chars,
        "target_chars": target_chars,
    }


def _blank_interaction(
    item: FillBlankItem, blank_index: int, warnings: list[str]
) -> dict[str, object] | None:
    """`fill-in`: one interaction per blank, one pattern record per accepted answer.

    SCORM 1.2's fill-in is a flat string with no construct for several blanks, so
    a question with N blanks becomes N interactions keyed on each blank's own id.
    That is lossless, and it is what makes each blank show up separately in
    Moodle's report — the interaction count exceeding the question count is
    correct, not a bug.
    """
    blank = item.blanks[blank_index]
    answers = [a for a in blank.answers if len(a) <= RESPONSE_MAX_CHARS]
    if not answers:
        warnings.append(
            f"Not reporting {blank.id} to the LMS: every accepted answer exceeds 255 "
            "characters. The blank is still asked and still scored."
        )
        return None

    weighting, clamped = _weighting(item.points, len(item.blanks))
    if clamped:
        warnings.append(f"{item.id}: {clamped}.")
    return {"type": "fill-in", "correct_responses": answers, "weighting": weighting}


# --- the payload the player reads --------------------------------------------


def _chars_for(count: int) -> list[str]:
    """The reporting character for each of `count` options, blank past the 36th.

    A question with more options than SCORM can identify still renders — the
    emitter has already warned that it will not be reported.
    """
    chars: list[str] = []
    for index in range(count):
        try:
            chars.append(response_char(index))
        except ValueError:
            chars.append("")
    return chars


def _mcq_payload(item: MCQItem, warnings: list[str]) -> dict[str, object]:
    interaction = _mcq_interaction(item, warnings)
    chars = _chars_for(len(item.choices))
    return {
        "prompt": item.prompt,
        "single_answer": item.single_answer,
        # Each option carries the character SCORM will report it as, so the player
        # never has to reimplement the alphabet.
        "choices": [
            {
                "id": choice.id,
                "char": char,
                "text": choice.text,
                "is_correct": choice.is_correct,
                "feedback": choice.feedback,
            }
            for choice, char in zip(item.choices, chars)
        ],
        "interactions": [interaction] if interaction else [],
    }


def _match_payload(item: MatchItem, warnings: list[str]) -> dict[str, object]:
    interaction = _match_interaction(item, warnings)
    source_chars = interaction.get("source_chars", {}) if interaction else {}
    target_chars = interaction.get("target_chars", {}) if interaction else {}
    if interaction:
        # The char maps were only needed to build the pattern; the player reads
        # them off the options themselves.
        interaction = {k: v for k, v in interaction.items() if not k.endswith("_chars")}
    return {
        "prompt": item.prompt,
        "sources": [
            {"id": s.id, "char": source_chars.get(s.id, ""), "text": s.text, "target_id": s.target_id}
            for s in item.sources
        ],
        "targets": [
            {"id": t.id, "char": target_chars.get(t.id, ""), "text": t.text} for t in item.targets
        ],
        "interactions": [interaction] if interaction else [],
    }


def _blanks_payload(item: FillBlankItem, warnings: list[str]) -> dict[str, object]:
    interactions = []
    blanks = []
    for index, blank in enumerate(item.blanks):
        interaction = _blank_interaction(item, index, warnings)
        blanks.append(
            {
                "id": blank.id,
                "answers": list(blank.answers),
                "tip": blank.tip,
                "reported": interaction is not None,
            }
        )
        if interaction:
            interactions.append({**interaction, "id": blank.id})
    return {
        "prompt": item.prompt,
        "text": item.text,
        "blanks": blanks,
        "case_sensitive": item.case_sensitive,
        "interactions": interactions,
    }


def _short_answer_payload(item: ShortAnswerItem, warnings: list[str]) -> dict[str, object]:
    """One `fill-in` interaction per key point, like a blank — and for the same reason.

    Reporting a single verdict for the whole answer would tell a teacher only that it
    was wrong. One interaction per key point makes each mark show up separately in
    Moodle's Interactions report, so they can see *which* points the learner made.

    `student_response` carries the matched phrase rather than the learner's prose.
    That is not a workaround for CMIString255 — the contract caps a phrase at 60
    characters, so it cannot overflow — it is the more useful value, because it is
    the evidence that earned the mark. The prose itself travels in `cmi.comments`.
    """
    interactions = []
    for point in item.key_points:
        weighting, clamped = _weighting(float(point.weight))
        if clamped:
            warnings.append(f"{item.id}: {clamped}.")
        interactions.append(
            {
                "id": point.id,
                "type": "fill-in",
                "correct_responses": list(point.accepted),
                "weighting": weighting,
            }
        )
    return {
        "prompt": item.prompt,
        "model_answer": item.model_answer,
        "min_chars": item.min_chars,
        "max_chars": item.max_chars,
        "key_points": [
            {
                "id": point.id,
                "text": point.text,
                "accepted": list(point.accepted),
                "weight": point.weight,
                "feedback_hit": point.feedback_hit,
                "feedback_miss": point.feedback_miss,
            }
            for point in item.key_points
        ],
        "interactions": interactions,
    }


def _question_payload(question: Question, warnings: list[str]) -> dict[str, object]:
    """Everything the player needs for one question, including its answer key."""
    common: dict[str, object] = {
        "id": question.id,
        "type": question.type,
        "points": question.points,
        "has_latex": question.has_latex,
        "explanation": question.explanation,
    }
    if isinstance(question, MCQItem):
        common.update(_mcq_payload(question, warnings))
    elif isinstance(question, MatchItem):
        common.update(_match_payload(question, warnings))
    elif isinstance(question, ShortAnswerItem):
        common.update(_short_answer_payload(question, warnings))
    elif isinstance(question, FillBlankItem):
        common.update(_blanks_payload(question, warnings))
    else:  # pragma: no cover - a type nobody taught this emitter about
        # Explicit rather than a catch-all `else`, which previously routed anything
        # unrecognised into the fill-in-the-blank branch and raised AttributeError on
        # a field it did not have.
        raise ValueError(f"{question.type} is not a type the SCORM emitter can package")
    return common


def _filename(assessment: AssessmentSet) -> str:
    stem = assessment.source.filename.rsplit(".", 1)[0]
    return f"{sanitise_filename(stem, fallback='assessment')}-scorm.zip"


def emit_scorm(assessment: AssessmentSet) -> ScormPackage:
    """Package an assessment as a SCORM 1.2 course.

    Raises `EmptyAssessmentError` if the set has no questions — an empty SCO would
    report a score of nothing out of nothing.
    """
    if not assessment.questions:
        raise EmptyAssessmentError("No questions to package as a SCORM course.")

    warnings: list[str] = []
    title = assessment.source.title or assessment.source.filename
    bands = assessment.score_bands or default_score_bands(assessment.pass_percentage)

    latex = [q.id for q in assessment.questions if q.has_latex]
    if latex:
        # A deliberate divergence from emit_h5p, which drops these. SCORM has no
        # maths support and no LMS supplies a renderer, so the choice is between
        # showing the source and withholding the question. We own the player, and
        # a formula the learner can read beats a question that is not there.
        warnings.append(
            f"LaTeX in {', '.join(latex)} is shown as written: a SCORM package has to "
            "carry its own maths renderer and this one does not bundle MathJax."
        )

    payload = {
        "title": title,
        "language": assessment.language,
        # The player keys its stored deadline on this, so two assessments open in
        # the same tab cannot inherit each other's clock.
        "assessment_id": assessment.assessment_id,
        "solution_visibility": assessment.solution_visibility,
        "time_limit_seconds": assessment.time_limit_seconds,
        "pass_percentage": assessment.pass_percentage,
        "max_points": assessment.max_points,
        "score_bands": [
            {"from_percent": b.from_percent, "to_percent": b.to_percent, "feedback": b.feedback}
            for b in bands
        ],
        "questions": [_question_payload(q, warnings) for q in assessment.questions],
    }

    index = (
        _asset("index.html")
        .replace("{lang}", html.escape(assessment.language, quote=True))
        .replace("{title}", html.escape(title))
        .replace("{data}", _script_safe(json.dumps(payload, ensure_ascii=False)))
    )

    files: dict[str, bytes] = {LAUNCH_NAME: index.encode("utf-8")}
    for archive_name, asset_name in _PLAYER_FILES.items():
        files[archive_name] = _asset(asset_name).encode("utf-8")

    manifest = build_manifest(
        assessment_id=assessment.assessment_id,
        title=title,
        launch_href=LAUNCH_NAME,
        files=list(files),
    )
    return ScormPackage(
        content=write_scorm(manifest=manifest, files=files),
        filename=_filename(assessment),
        warnings=warnings,
    )
