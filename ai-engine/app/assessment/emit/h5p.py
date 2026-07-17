r"""Maps an `AssessmentSet` onto an H5P Question Set.

This is the domain half of H5P packaging: it knows what a question is and what
H5P calls it. The format half — versions, manifest, the ZIP — lives in
`app.packaging.h5p`, so Modules C and D can emit their own H5P content types
without importing anything from the assessment module.

The three mappings, all against the versions the H5P Hub serves today:

| contract       | H5P library          |
|----------------|----------------------|
| `MCQItem`      | H5P.MultiChoice 1.16 |
| `FillBlankItem`| H5P.Blanks 1.14      |
| `MatchItem`    | H5P.DragText 1.10    |

`MatchItem` → Drag Text deserves a word, because H5P has no first-class
match-the-pair type. Drag Text renders a text with `*gaps*` and shuffled
draggables, so one line per pair (``Term — *definition*``) *is* matching; and its
``distractors`` field gives our unmatched targets a real home. The alternative,
H5P.DragQuestion, is true drag-and-drop but needs pixel geometry for its drop
zones, which a document-derived question has no basis to invent.

**Everything the model wrote is escaped before it goes in.** H5P injects these
fields as HTML (Drag Text literally does ``span.innerHTML = text``), and the text
originates in a tenant's uploaded document, so an unescaped stem would be a
script-injection path into their LMS. Escaping is safe for both consumers:
``innerHTML`` decodes the entities back for display, Drag Text compares draggable
and solution with both sides escaped, and H5P.Blanks decodes entities before
grading.
"""

from __future__ import annotations

import html
import re
from typing import NamedTuple

from ...packaging.h5p import (
    BLANKS,
    DRAGTEXT,
    MULTICHOICE,
    build_manifest,
    write_h5p,
    wrap,
)
from ..schema import (
    AssessmentSet,
    FillBlankItem,
    MatchItem,
    MCQItem,
    Question,
    ScoreBand,
    default_score_bands,
)
from .errors import EmptyAssessmentError

_BLANK_MARKER = re.compile(r"\[\[(\d+)\]\]")

# Math spans as H5P's MathDisplay recognises them. Its own trigger regex has no
# DOTALL flag, so a span containing a newline never matches and the learner is
# shown raw LaTeX. We match WITH DOTALL precisely to catch that case.
_MATH_SPAN = re.compile(r"\$\$.+?\$\$|\\\[.+?\\\]|\\\(.+?\\\)", re.DOTALL)

# Just the opening delimiters, for "is there maths here at all?".
_MATH_OPENER = re.compile(r"\$\$|\\\(|\\\[")


class H5PPackage(NamedTuple):
    """A built ``.h5p`` and anything the caller should know about it."""

    content: bytes
    filename: str
    warnings: list[str]


def _escape(text: str) -> str:
    """Escape model text for an H5P field. Quotes are left alone: these are text
    nodes, not attribute values, and escaping them only hurts readability."""
    return html.escape(text or "", quote=False)


def _paragraph(text: str) -> str:
    """H5P's rich-text fields hold a paragraph; this is the shape its editor writes."""
    return f"<p>{_escape(text)}</p>\n"


# --- markup safety -----------------------------------------------------------
#
# H5P.Blanks and H5P.DragText both carry answers inside `*...*` in a plain string,
# and NEITHER parser has an escape mechanism:
#
#   blanks.js:     text.split(/(\*.*?\*)/)     -> any stray * re-pairs the gaps
#                  solutionText.indexOf(':')   -> first colon starts the tip
#                  solution.split('/')         -> every slash splits alternatives
#   drag-text:     same asterisk tokenizer, plus :tip and \+ \- feedback markers
#
# Every one of these fails *silently*: the package imports and looks fine, but the
# answers are wrong. A ratio ("3:4"), a unit ("m/s") or a literal asterisk is
# enough to trigger it. So we refuse to emit a question we cannot render
# faithfully, and say so — the same drop-and-warn discipline ADR-0003 uses for
# questions it cannot ground.


def _safe_outside_markup(text: str) -> bool:
    """Text around the gaps: only an asterisk can break the tokenizer."""
    return "*" not in text


def _safe_blank_answer(text: str) -> bool:
    """Inside `*...*` for H5P.Blanks: `*` re-pairs, `:` starts a tip, `/` splits."""
    return not any(char in text for char in "*:/")


def _safe_blank_tip(text: str) -> bool:
    """A tip is everything after the first colon, so only `*` is dangerous there."""
    return "*" not in text


def _safe_drag_target(text: str) -> bool:
    """Inside `*...*` for H5P.DragText: `*` and `:` bite; `\\+`/`\\-` are feedback."""
    if any(char in text for char in "*:"):
        return False
    return "\\+" not in text and "\\-" not in text


def _latex_is_single_line(text: str) -> bool:
    """MathDisplay's trigger regex is not DOTALL, so multi-line math never renders."""
    return all("\n" not in span.group(0) for span in _MATH_SPAN.finditer(text))


def _contains_math(text: str) -> bool:
    """Whether the text opens a math span at all, however malformed."""
    return bool(_MATH_OPENER.search(text or ""))


def _typeset_surfaces(question: Question) -> list[str]:
    """Every string H5P will render as HTML, and so could typeset as maths."""
    if isinstance(question, MCQItem):
        return [question.prompt, *(choice.text for choice in question.choices)]
    if isinstance(question, FillBlankItem):
        return [question.prompt or "", question.text]
    if isinstance(question, MatchItem):
        return [
            question.prompt,
            *(source.text for source in question.sources),
            *(target.text for target in question.targets),
        ]
    return []


def _latex_problem(question: Question) -> str | None:
    """Why this question's maths would not render, or None if it is fine."""
    if not question.has_latex:
        return None
    for text in _typeset_surfaces(question):
        if not _latex_is_single_line(text):
            return "its LaTeX spans multiple lines, which H5P's MathDisplay does not render"
    if isinstance(question, FillBlankItem):
        for blank in question.blanks:
            if any(_contains_math(answer) for answer in blank.answers):
                # A blank's answer is typed into an <input>. There is no markup
                # there to typeset, so the learner would have to type raw LaTeX.
                return "it puts LaTeX in a blank's answer, which H5P renders as a plain text box"
    return None


# --- per-type params ---------------------------------------------------------


def _overall_feedback(bands: list[ScoreBand]) -> list[dict[str, object]]:
    """H5P's score-band structure. Flat — `overallFeedback` is the list itself.

    Nesting it one level deeper (``overallFeedback.overallFeedback``) is the
    classic bug here: it imports cleanly and simply never shows any feedback.

    The text is escaped: it reaches the results screen through
    ``H5P.Question.determineOverallFeedback`` and is injected as HTML, and a
    rubric can be supplied by a caller rather than written by us.
    """
    return [
        {"from": band.from_percent, "to": band.to_percent, "feedback": _escape(band.feedback)}
        for band in bands
    ]


def _explanation_feedback(explanation: str | None) -> list[dict[str, object]]:
    """A question's rationale has no dedicated H5P slot; a 0-100 band always shows."""
    if not explanation:
        return []
    return [{"from": 0, "to": 100, "feedback": _escape(explanation)}]


def _mcq_params(item: MCQItem) -> dict[str, object]:
    return {
        "question": _paragraph(item.prompt),
        "answers": [
            {
                # `correct` must be present on EVERY answer. multichoice.js does a
                # deep $.extend over its defaults, and jQuery merges arrays by
                # index; its defaults.answers[0] is {correct: true}, so an omitted
                # key on the first answer silently turns it correct.
                "correct": choice.is_correct,
                "text": f"<div>{_escape(choice.text)}</div>\n",
                "tipsAndFeedback": {
                    "tip": "",
                    "chosenFeedback": _escape(choice.feedback or ""),
                    "notChosenFeedback": "",
                },
            }
            for choice in item.choices
        ],
        "overallFeedback": _explanation_feedback(item.explanation),
        "behaviour": {
            # `type`, never `singleAnswer` (absent from semantics; multichoice.js
            # derives it from `type` before any read). Never "auto" either: with
            # auto, a multi-answer question that happens to have one correct choice
            # renders as radio buttons, and our contract explicitly allows that.
            "type": "single" if item.single_answer else "multi",
            # singlePoint and randomAnswers are emitted explicitly because the JS
            # defaults contradict the semantics defaults, and a machine-written
            # content.json bypasses the editor that would have applied semantics.
            "singlePoint": True,
            "randomAnswers": True,
            "enableRetry": True,
            "enableSolutionsButton": True,
            "enableCheckButton": True,
            "showSolutionsRequiresInput": True,
            "confirmCheckDialog": False,
            "confirmRetryDialog": False,
            "autoCheck": False,
            "passPercentage": 100,
            "showScorePoints": True,
        },
    }


def _blanks_text(item: FillBlankItem) -> str | None:
    """Render `[[n]]` markers into H5P's `*answer/alt:tip*` markup, or None if unsafe."""
    if not _safe_outside_markup(item.text):
        return None

    replacements: dict[int, str] = {}
    for position, blank in enumerate(item.blanks, start=1):
        answers = [answer.strip() for answer in blank.answers]
        # Guard the raw text: escaping never introduces * : or /, so checking
        # before or after is equivalent, and the author's own text is what the
        # warning will talk about.
        if not all(_safe_blank_answer(answer) for answer in answers):
            return None
        # Escaped, like every other field: these land inside the question's HTML.
        # It round-trips — parseSolution entity-decodes each solution before
        # grading, and a tip is rendered through jQuery's .html(), which parses
        # the entity back.
        markup = "/".join(_escape(answer) for answer in answers)
        if blank.tip:
            tip = blank.tip.strip()
            if not _safe_blank_tip(tip):
                return None
            markup = f"{markup}:{_escape(tip)}"
        replacements[position] = f"*{markup}*"

    def substitute(match: re.Match[str]) -> str:
        return replacements[int(match.group(1))]

    # Escape the sentence first, then write the markup into it: escaping afterwards
    # would mangle the asterisks we just added.
    escaped = _escape(item.text)
    return f"<p>{_BLANK_MARKER.sub(substitute, escaped)}</p>\n"


def _blanks_params(item: FillBlankItem, text: str) -> dict[str, object]:
    return {
        "text": _paragraph(item.prompt) if item.prompt else "<p>Fill in the missing words</p>\n",
        "questions": [text],
        "overallFeedback": _explanation_feedback(item.explanation),
        "behaviour": {
            # H5P defaults caseSensitive to TRUE and our contract defaults it to
            # False, so omitting it would invert the author's intent.
            "caseSensitive": item.case_sensitive,
            "enableRetry": True,
            "enableSolutionsButton": True,
            "enableCheckButton": True,
            "autoCheck": False,
            "separateLines": False,
            "showSolutionsRequiresInput": True,
            "acceptSpellingErrors": False,
            "confirmCheckDialog": False,
            "confirmRetryDialog": False,
        },
    }


def _dragtext_fields(item: MatchItem) -> tuple[str, str] | None:
    """Build (textField, distractors), or None if any text would corrupt the markup."""
    targets_by_id = {target.id: target for target in item.targets}
    lines: list[str] = []
    for source in item.sources:
        target = targets_by_id.get(source.target_id)
        if target is None:  # the contract forbids this, but never render a guess
            return None
        if not _safe_outside_markup(source.text) or not _safe_drag_target(target.text):
            return None
        lines.append(f"{_escape(source.text)} — *{_escape(target.text)}*")

    matched = {source.target_id for source in item.sources}
    distractors: list[str] = []
    for target in item.targets:
        if target.id in matched:
            continue
        if not _safe_drag_target(target.text):
            return None
        distractors.append(f"*{_escape(target.text)}*")

    return "<br/>".join(lines), " ".join(distractors)


def _dragtext_params(item: MatchItem, text_field: str, distractors: str) -> dict[str, object]:
    params: dict[str, object] = {
        "taskDescription": _paragraph(item.prompt),
        "textField": text_field,
        "overallFeedback": _explanation_feedback(item.explanation),
        # DragText's behaviour group has exactly these four keys.
        "behaviour": {
            "enableRetry": True,
            "enableSolutionsButton": True,
            "enableCheckButton": True,
            "instantFeedback": False,
        },
    }
    if distractors:
        params["distractors"] = distractors
    return params


# --- assembly ----------------------------------------------------------------


def _implied_points(question: Question) -> int:
    """What H5P will actually score this question out of.

    H5P has no per-question weight we can set: `params.weight` is absent from
    semantics and gets stripped. Each type has its own fixed scale.
    """
    if isinstance(question, MCQItem):
        return 1  # singlePoint collapses a multi-select to one mark
    if isinstance(question, FillBlankItem):
        return len(question.blanks)
    if isinstance(question, MatchItem):
        return len(question.sources)
    return 1


def _wrap_question(
    question: Question, assessment_id: str, warnings: list[str]
) -> dict[str, object] | None:
    """Map one question to a subcontent wrapper, or drop it with a warning."""
    problem = _latex_problem(question)
    if problem is not None:
        warnings.append(f"Dropped {question.id} from the H5P package: {problem}.")
        return None

    if isinstance(question, MCQItem):
        return wrap(
            library=MULTICHOICE,
            params=_mcq_params(question),
            content_type="Multiple Choice",
            title=question.id,
            assessment_id=assessment_id,
            question_id=question.id,
        )

    if isinstance(question, FillBlankItem):
        text = _blanks_text(question)
        if text is None:
            warnings.append(
                f"Dropped {question.id} from the H5P package: an answer or tip contains "
                "a character (* : /) that H5P's fill-in-the-blank markup cannot express."
            )
            return None
        return wrap(
            library=BLANKS,
            params=_blanks_params(question, text),
            content_type="Fill in the Blanks",
            title=question.id,
            assessment_id=assessment_id,
            question_id=question.id,
        )

    if isinstance(question, MatchItem):
        fields = _dragtext_fields(question)
        if fields is None:
            warnings.append(
                f"Dropped {question.id} from the H5P package: a term contains a character "
                "(* or :) that H5P's drag-text markup cannot express."
            )
            return None
        text_field, distractors = fields
        return wrap(
            library=DRAGTEXT,
            params=_dragtext_params(question, text_field, distractors),
            content_type="Drag the Words",
            title=question.id,
            assessment_id=assessment_id,
            question_id=question.id,
        )

    return None


def _question_set(assessment: AssessmentSet, questions: list[dict[str, object]]) -> dict[str, object]:
    bands = assessment.score_bands or default_score_bands(assessment.pass_percentage)
    title = assessment.source.title or assessment.source.filename
    return {
        "introPage": {
            "showIntroPage": True,
            # Escaped: questionset.js concatenates this straight into the intro
            # page's HTML, and it can come from an uploaded document's filename.
            "title": _escape(title),
            "introduction": "<p>Answer all questions.</p>\n",
            "startButtonText": "Start Quiz",
            "backgroundImageAltText": "",
        },
        "progressType": "dots",
        "passPercentage": assessment.pass_percentage,
        "questions": questions,
        "texts": {
            "prevButton": "Previous question",
            "nextButton": "Next question",
            "finishButton": "Finish",
            "submitButton": "Submit",
            "textualProgress": "Question: @current of @total questions",
            "jumpToQuestion": "Question %d of %total",
            "questionLabel": "Question",
            "readSpeakerProgress": "Question @current of @total",
            "unansweredText": "Unanswered",
            "answeredText": "Answered",
            "currentQuestionText": "Current question",
            "navigationLabel": "Questions",
        },
        "disableBackwardsNavigation": False,
        "randomQuestions": False,
        "endGame": {
            "showResultPage": True,
            "showSolutionButton": True,
            "showRetryButton": True,
            "noResultMessage": "Finished",
            "message": "Your result:",
            "scoreBarLabel": "You got @finals out of @totals points",
            "overallFeedback": _overall_feedback(bands),
            "solutionButtonText": "Show solution",
            "retryButtonText": "Retry",
            "finishButtonText": "Finish",
            "showAnimations": False,
            "skippable": False,
        },
        "override": {"checkButton": True},
    }


def _filename(assessment: AssessmentSet) -> str:
    stem = assessment.source.filename.rsplit(".", 1)[0] or "assessment"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-") or "assessment"
    return f"{safe}.h5p"


def emit_h5p(assessment: AssessmentSet) -> H5PPackage:
    """Package an assessment as an H5P Question Set.

    Raises `EmptyAssessmentError` if nothing renders: a Question Set requires at
    least one question, so there is no valid empty package to hand back.
    """
    warnings: list[str] = []
    questions: list[dict[str, object]] = []
    kept: list[Question] = []
    for question in assessment.questions:
        wrapped = _wrap_question(question, assessment.assessment_id, warnings)
        if wrapped is not None:
            questions.append(wrapped)
            kept.append(question)

    if not questions:
        raise EmptyAssessmentError("No questions could be packaged as an H5P Question Set.")

    # Compare against what we actually packaged, not the set's max_points: any
    # dropped question already has its own warning, and counting it here would
    # report the same loss twice under a misleading cause.
    implied = sum(_implied_points(question) for question in kept)
    intended = round(sum(question.points for question in kept), 2)
    if abs(implied - intended) > 0.01:
        warnings.append(
            f"H5P scores this set out of {implied} points, not {intended}: H5P has no "
            "per-question weight, so it counts one mark per choice question and one per "
            "blank or pair. The pass percentage is a percentage, so mastery is unaffected."
        )

    package = write_h5p(
        manifest=build_manifest(
            title=assessment.source.title or assessment.source.filename,
            language=assessment.language,
        ),
        content=_question_set(assessment, questions),
    )
    return H5PPackage(content=package, filename=_filename(assessment), warnings=warnings)
