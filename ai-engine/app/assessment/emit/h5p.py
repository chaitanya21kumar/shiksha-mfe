r"""Maps an `AssessmentSet` onto an H5P Question Set.

This is the domain half of H5P packaging: it knows what a question is and what
H5P calls it. The format half — versions, manifest, the ZIP — lives in
`app.packaging.h5p`, so Modules C and D can emit their own H5P content types
without importing anything from the assessment module.

The four mappings, all against the versions the H5P Hub serves today:

| contract          | H5P library          |
|-------------------|----------------------|
| `MCQItem`         | H5P.MultiChoice 1.16 |
| `FillBlankItem`   | H5P.Blanks 1.14      |
| `MatchItem`       | H5P.DragText 1.10    |
| `ShortAnswerItem` | H5P.Essay 1.5        |

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

import re

from ...packaging.h5p import (
    BLANKS,
    DRAGTEXT,
    ESSAY,
    MULTICHOICE,
    H5PPackage,
    build_manifest,
    escape_text,
    sanitise_filename,
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
    ShortAnswerItem,
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


class _Unrenderable(Exception):
    """A question H5P cannot express faithfully, and why.

    Carrying the reason out of the builder keeps each warning honest: the causes
    are genuinely different (unusable markup, a dangling reference, maths in a
    text box) and a caller reading `warnings` should be told which one it hit.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _paragraph(text: str) -> str:
    """H5P's rich-text fields hold a paragraph; this is the shape its editor writes."""
    return f"<p>{escape_text(text)}</p>\n"


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
    """Inside `*...*` for H5P.Blanks: `*` re-pairs, `:` starts a tip, `/` splits.

    Empty is rejected too: `**` is a well-formed token to H5P's tokenizer, so an
    empty answer becomes a real gap that nothing can ever satisfy.
    """
    if not text.strip():
        return False
    return not any(char in text for char in "*:/")


def _safe_blank_tip(text: str) -> bool:
    """A tip is everything after the first colon, so only `*` is dangerous there."""
    return "*" not in text


def _safe_drag_line(text: str) -> bool:
    """A source label sits between the gaps, and a newline now separates pairs."""
    return "*" not in text and "\n" not in text


def _safe_drag_target(text: str) -> bool:
    """Inside `*...*` for H5P.DragText: `*` and `:` bite; `\\+`/`\\-` are feedback.

    A newline would split the pair onto two lines, and an empty target produces
    `**` — which Drag Text lexes into a real draggable carrying no text at all.
    """
    if not text.strip() or "\n" in text:
        return False
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
    if isinstance(question, ShortAnswerItem):
        return [question.prompt, question.model_answer]
    return []


def _latex_problem(question: Question) -> str | None:
    """Why this question's maths would not render, or None if it is fine."""
    if not question.has_latex:
        return None
    if isinstance(question, ShortAnswerItem):
        # The learner answers a short-answer question in a plain textarea. There is
        # no markup for MathDisplay to typeset and no way to type LaTeX into it, so
        # a maths question here has no sensible form on the H5P path. It still ships
        # in the SCORM package, where the source renders as written.
        return "it needs LaTeX, which H5P's plain-text answer box cannot render"
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



def _behaviour(**overrides: object) -> dict[str, object]:
    """The behaviour flags every question type sets, plus that type's own.

    These are written out rather than left to default because H5P's *semantics*
    defaults are applied by its editor, and a machine-written ``content.json``
    never goes through the editor — so an omitted flag falls through to whatever
    the library's JavaScript happens to use, which is not always the same value.
    Each type then overrides what it genuinely differs on.
    """
    common: dict[str, object] = {
        "enableRetry": True,
        "enableSolutionsButton": True,
        "enableCheckButton": True,
        "showSolutionsRequiresInput": True,
        "confirmCheckDialog": False,
        "confirmRetryDialog": False,
        "autoCheck": False,
    }
    common.update(overrides)
    return common


def _overall_feedback(bands: list[ScoreBand]) -> list[dict[str, object]]:
    """H5P's score-band structure. Flat — `overallFeedback` is the list itself.

    Nesting it one level deeper (``overallFeedback.overallFeedback``) is the
    classic bug here: it imports cleanly and simply never shows any feedback.

    The text is escaped: it reaches the results screen through
    ``H5P.Question.determineOverallFeedback`` and is injected as HTML, and a
    rubric can be supplied by a caller rather than written by us.
    """
    return [
        {"from": band.from_percent, "to": band.to_percent, "feedback": escape_text(band.feedback)}
        for band in bands
    ]


def _explanation_feedback(explanation: str | None) -> list[dict[str, object]]:
    """A question's rationale has no dedicated H5P slot; a 0-100 band always shows."""
    if not explanation:
        return []
    return [{"from": 0, "to": 100, "feedback": escape_text(explanation)}]


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
                "text": f"<div>{escape_text(choice.text)}</div>\n",
                "tipsAndFeedback": {
                    "tip": "",
                    "chosenFeedback": escape_text(choice.feedback or ""),
                    "notChosenFeedback": "",
                },
            }
            for choice in item.choices
        ],
        "overallFeedback": _explanation_feedback(item.explanation),
        # multichoice.js reads these three but does not carry them in its own
        # defaults -- they exist only as semantics defaults, which the H5P editor
        # applies and a machine-written content.json never sees. Omitting them
        # renders the literal string "undefined" to the learner.
        "UI": {
            "tipsLabel": "Show tip",
            "correctAnswer": "Correct answer",
            "wrongAnswer": "Wrong answer",
        },
        "behaviour": _behaviour(
            # `type`, never `singleAnswer` (absent from semantics; multichoice.js
            # derives it from `type` before any read). Never "auto" either: with
            # auto, a multi-answer question that happens to have one correct choice
            # renders as radio buttons, and our contract explicitly allows that.
            type="single" if item.single_answer else "multi",
            # singlePoint and randomAnswers are emitted explicitly because the JS
            # defaults contradict the semantics defaults.
            singlePoint=True,
            randomAnswers=True,
            passPercentage=100,
            showScorePoints=True,
        ),
    }


_MARKUP_REASON = (
    "an answer or tip contains a character (* : /) that H5P's fill-in-the-blank "
    "markup cannot express"
)


def _blanks_text(item: FillBlankItem) -> str:
    """Render `[[n]]` markers into H5P's `*answer/alt:tip*` markup."""
    if not _safe_outside_markup(item.text):
        raise _Unrenderable(_MARKUP_REASON)

    replacements: dict[int, str] = {}
    for position, blank in enumerate(item.blanks, start=1):
        answers = [answer.strip() for answer in blank.answers]
        # Guard the raw text: escaping never introduces * : or /, so checking
        # before or after is equivalent, and the author's own text is what the
        # warning will talk about.
        if not all(_safe_blank_answer(answer) for answer in answers):
            raise _Unrenderable(_MARKUP_REASON)
        # Escaped, like every other field: these land inside the question's HTML.
        # It round-trips — parseSolution entity-decodes each solution before
        # grading, and a tip is rendered through jQuery's .html(), which parses
        # the entity back.
        markup = "/".join(escape_text(answer) for answer in answers)
        tip = (blank.tip or "").strip()
        if tip:
            # Only append a tip that survives stripping: a whitespace-only tip
            # would emit a bare trailing colon, which parseSolution reads as a
            # real (empty) tip.
            if not _safe_blank_tip(tip):
                raise _Unrenderable(_MARKUP_REASON)
            markup = f"{markup}:{escape_text(tip)}"
        replacements[position] = f"*{markup}*"

    def substitute(match: re.Match[str]) -> str:
        return replacements[int(match.group(1))]

    # Escape the sentence first, then write the markup into it: escaping afterwards
    # would mangle the asterisks we just added.
    escaped = escape_text(item.text)
    return f"<p>{_BLANK_MARKER.sub(substitute, escaped)}</p>\n"


def _blanks_params(item: FillBlankItem, text: str) -> dict[str, object]:
    return {
        "text": _paragraph(item.prompt) if item.prompt else "<p>Fill in the missing words</p>\n",
        "questions": [text],
        "overallFeedback": _explanation_feedback(item.explanation),
        "behaviour": _behaviour(
            # H5P defaults caseSensitive to TRUE and our contract defaults it to
            # False, so omitting it would invert the author's intent.
            caseSensitive=item.case_sensitive,
            separateLines=False,
            acceptSpellingErrors=False,
        ),
    }


def _dragtext_fields(item: MatchItem) -> tuple[str, str]:
    """Build (textField, distractors) for a match question.

    Pairs are separated by a newline, **not** by ``<br/>``. Drag Text's
    ``textField`` is declared ``widget: textarea`` with no ``tags``, so H5P's
    importer runs it through ``htmlspecialchars`` and a ``<br/>`` would reach the
    learner as those literal five characters. Drag Text does its own
    ``replace(/(\\r\\n|\\n|\\r)/gm, "<br/>")`` after the importer has run, so a
    newline is the separator the field is designed for — and it is what H5P's own
    semantics placeholder uses.
    """
    drag_reason = "a term contains a character (* or :) that H5P's drag-text markup cannot express"
    targets_by_id = {target.id: target for target in item.targets}
    lines: list[str] = []
    for source in item.sources:
        target = targets_by_id.get(source.target_id)
        if target is None:
            # The contract's own validator rejects a dangling target_id, so this
            # is unreachable in practice. It is still worth its own reason: a
            # broken reference is not a markup problem, and saying so would send
            # whoever reads the warning looking in the wrong place.
            raise _Unrenderable(f"{source.id} points at a target that does not exist")
        if not _safe_drag_line(source.text) or not _safe_drag_target(target.text):
            raise _Unrenderable(drag_reason)
        lines.append(f"{escape_text(source.text.strip())} — *{escape_text(target.text.strip())}*")

    matched = {source.target_id for source in item.sources}
    distractors: list[str] = []
    for target in item.targets:
        if target.id in matched:
            continue
        if not _safe_drag_target(target.text):
            raise _Unrenderable(drag_reason)
        distractors.append(f"*{escape_text(target.text.strip())}*")

    return "\n".join(lines), " ".join(distractors)


#: Prefixes for the Essay feedback table. Essay gives every row the same styling
#: and no hit/miss affordance of its own, so the distinction has to be carried by
#: the text. Plain U+2713/U+2717 rather than emoji: they render in the LMS's own
#: font at the LMS's own size, and degrade to a visible glyph everywhere.
_HIT_MARK = "✓"
_MISS_MARK = "✗"


def _criterion_feedback(mark: str, criterion: str, remark: str | None) -> str:
    """One row of the mark scheme, named and marked made-or-missed."""
    line = f"{mark} {escape_text(criterion)}"
    return f"{line} — {escape_text(remark)}" if remark else line


def _essay_params(item: ShortAnswerItem) -> dict[str, object]:
    """Params for H5P.Essay — the only open-response type a Question Set accepts.

    Essay's own matcher is the algorithm our grader ports, so the same answer scores
    the same in both packages. The rest of this is about not tripping over defaults
    that only ever get applied by H5P's editor, which a machine-written content.json
    never passes through.
    """
    # The mark scheme, spelled out. Without this the H5P package would mark an
    # answer and tell the learner nothing about why — the disclosure ADR-0006
    # promises would exist only in the SCORM player. Essay has no slot for a named
    # criterion, so each key point's text becomes its own hit/miss feedback and the
    # whole scheme is listed above the sample answer.
    scheme = "".join(f"<li>{escape_text(point.text)}</li>" for point in item.key_points)
    return {
        "taskDescription": _paragraph(item.prompt),
        "placeholderText": "Answer in two or three sentences, in your own words.",
        "solution": {
            "introduction": f"<p>A complete answer makes these points:</p>\n<ul>{scheme}</ul>\n",
            "sample": _paragraph(item.model_answer),
        },
        "keywords": [
            {
                "keyword": escape_text(point.accepted[0]),
                "alternatives": [escape_text(form) for form in point.accepted[1:]],
                # `options` must be COMPLETE. essay.js reads `alternativeGroup.options`
                # with no `|| {}` guard and then dereferences `.points` and
                # `.occurrences` — so a missing group is a TypeError that takes the
                # whole activity down, and a missing `points` makes the score NaN.
                "options": {
                    "points": point.weight,
                    "occurrences": 1,
                    # H5P defaults this to true. For free text that is plainly wrong:
                    # a learner should not lose a mark for a lower-case sentence start.
                    "caseSensitive": False,
                    # Fuzzy matching is off deliberately — porting its sliding
                    # Levenshtein window bit-exactly into our SCORM player is the kind
                    # of near-miss that would silently score the same answer
                    # differently in the two packages. Recall comes from `accepted`.
                    "forgiveMistakes": False,
                    # Always names the criterion and marks it made or missed.
                    # Essay renders every row of its feedback table identically, so
                    # a bare sentence leaves the learner unable to tell a point they
                    # made from one they missed — which is the whole disclosure
                    # ADR-0006 rests on. The model's own remark, when it wrote one,
                    # follows the criterion rather than replacing it.
                    "feedbackIncluded": _criterion_feedback(
                        _HIT_MARK, point.text, point.feedback_hit
                    ),
                    "feedbackMissed": _criterion_feedback(
                        _MISS_MARK, point.text, point.feedback_miss
                    ),
                    # Both are selects; "none" keeps the learner-facing wording ours
                    # and stops a missed-point message printing the answer it wanted.
                    "feedbackIncludedWord": "none",
                    "feedbackMissedWord": "none",
                },
            }
            for point in item.key_points
        ],
        "overallFeedback": _explanation_feedback(item.explanation),
        "behaviour": {
            "minimumLength": item.min_chars,
            "maximumLength": item.max_chars,
            # A select over the STRINGS "1" | "3" | "10", not a number.
            "inputFieldSize": "10",
            "enableRetry": True,
            "ignoreScoring": False,
            # Emitted explicitly: omitted, essay.js computes `undefined * scoreMax / 100
            # || 0` -> 0, so every submission including an empty one reports as passed.
            "percentagePassing": 100,
            # percentageMastering is deliberately ABSENT. Essay reads
            # `percentageMastering === undefined ? scoreMax : pct * scoreMax / 100`, so
            # omitting it makes the denominator exactly the sum of our weights; any
            # value below 100 would LOWER the max score rather than raise a threshold.
            "overrideCaseSensitive": "off",
            "overrideForgiveMistakes": "off",
            "linebreakReplacement": " ",
        },
    }


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
    if isinstance(question, ShortAnswerItem):
        # The one type where H5P's scale and ours agree by construction: Essay scores
        # out of the summed keyword points, and the contract pins `points` to exactly
        # that sum. So a short answer never contributes to the scale warning below.
        return sum(point.weight for point in question.key_points)
    return 1


def _wrap_question(
    question: Question, assessment_id: str, warnings: list[str]
) -> dict[str, object] | None:
    """Map one question to a subcontent wrapper, or drop it with a warning."""
    try:
        return _build(question, assessment_id)
    except _Unrenderable as unrenderable:
        warnings.append(f"Dropped {question.id} from the H5P package: {unrenderable.reason}.")
        return None


def _build(question: Question, assessment_id: str) -> dict[str, object]:
    """Map one question to a subcontent wrapper, or say why it cannot be."""
    problem = _latex_problem(question)
    if problem is not None:
        raise _Unrenderable(problem)

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
        return wrap(
            library=BLANKS,
            params=_blanks_params(question, _blanks_text(question)),
            content_type="Fill in the Blanks",
            title=question.id,
            assessment_id=assessment_id,
            question_id=question.id,
        )

    if isinstance(question, MatchItem):
        text_field, distractors = _dragtext_fields(question)
        return wrap(
            library=DRAGTEXT,
            params=_dragtext_params(question, text_field, distractors),
            content_type="Drag the Words",
            title=question.id,
            assessment_id=assessment_id,
            question_id=question.id,
        )

    if isinstance(question, ShortAnswerItem):
        return wrap(
            library=ESSAY,
            params=_essay_params(question),
            content_type="Essay",
            title=question.id,
            assessment_id=assessment_id,
            question_id=question.id,
        )

    # A question type nobody taught this emitter about. Saying so beats returning
    # None, which would drop it from the package without a word — the exact silent
    # failure this module is written against.
    raise _Unrenderable(f"{question.type} is not a type the H5P emitter can package")


#: Public name for the "cannot be expressed" signal, so other H5P emitters can
#: catch it without reaching for a private symbol.
UnrenderableQuestion = _Unrenderable


def build_question_subcontent(
    question: Question,
    assessment_id: str,
    *,
    allowed: frozenset[str] | None = None,
) -> dict[str, object]:
    """Map one question onto an H5P subcontent entry, or say why it cannot be.

    This is the seam Module C's interactive video builds on: an interaction's
    ``action`` is the same ``{library, params, subContentId, metadata}`` shape a
    Question Set child uses, so the mapping is shared rather than written twice.

    ``allowed`` is the host content type's own library whitelist. Passing it
    matters because **the whitelists genuinely differ** — Interactive Video
    permits eighteen libraries and ``H5P.Essay`` is not among them, so a
    short-answer question that packages fine into a Question Set cannot go into a
    video. Checking here keeps that rule in one place for every caller.
    """
    built = _build(question, assessment_id)
    library = str(built["library"])
    if allowed is not None and library not in allowed:
        raise _Unrenderable(f"{library} is not a library this content type accepts")
    return built


def _question_set(assessment: AssessmentSet, questions: list[dict[str, object]]) -> dict[str, object]:
    bands = assessment.score_bands or default_score_bands(assessment.pass_percentage)
    title = assessment.source.title or assessment.source.filename
    return {
        "introPage": {
            "showIntroPage": True,
            # Escaped: questionset.js concatenates this straight into the intro
            # page's HTML, and it can come from an uploaded document's filename.
            "title": escape_text(title),
            "introduction": "<p>Answer all questions.</p>\n",
            "startButtonText": "Start Quiz",
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
    stem = assessment.source.filename.rsplit(".", 1)[0]
    return f"{sanitise_filename(stem, fallback='assessment')}.h5p"


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
