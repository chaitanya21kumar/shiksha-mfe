"""Which fields of each artefact are ours, and therefore ours to check.

The split matters more than the checking does. Every artefact mixes text the
model composed with text lifted out of the source document, and only the first
kind may be flagged:

- a glossary **definition** is written by the model; the **term** is the author's
- a narration **script** is written by the model; the slide **title** it came from
  is the author's
- a question **prompt** and **explanation** are written by the model; the evidence
  quote that justified it is the author's, and is what the grounding gate checks
- a chapter **title** is written by the model; the transcript beneath it is what
  the speaker actually said

Getting this backwards would mean telling a teacher their own textbook is
misspelt, which is both wrong and the fastest way to have the feature switched
off.
"""

from __future__ import annotations

from .prose import ProseChecker
from .schema import ValidationReport

_CHECK = "spelling"


def _report(checker: ProseChecker) -> ValidationReport:
    report = ValidationReport()
    if checker.available:
        report.checks_run.append(_CHECK)
    else:
        reason = checker.skip_reason
        if reason:
            report.skipped.append(reason)
    return report


def check_insights(insights, source_text: str = "") -> ValidationReport:
    """Spell-check a `DocumentInsights`.

    The glossary term is added to the allow-list rather than checked: it was
    lifted from the document, so it is correct here by construction, and it is
    exactly the kind of subject vocabulary a general dictionary lacks.
    """
    checker = ProseChecker(getattr(insights, "language", "en") or "en", source_text)
    checker.allow(*[g.term for g in insights.glossary])
    report = _report(checker)
    if not checker.available:
        return report.finalise()

    report.issues += checker.check(insights.summary, "summary")
    for i, point in enumerate(insights.key_takeaways):
        report.issues += checker.check(point, f"key_takeaways.{i}")
    for i, entry in enumerate(insights.glossary):
        report.issues += checker.check(entry.definition, f"glossary.{i}.definition")
    for i, section in enumerate(insights.outline):
        report.issues += checker.check(section.title, f"outline.{i}.title")
        for j, point in enumerate(section.points):
            report.issues += checker.check(point, f"outline.{i}.points.{j}")
    return report.finalise()


def check_narration(narration, source_text: str = "") -> ValidationReport:
    """Spell-check a `NarrationScript`.

    Only `script` is generated. `title` is carried across from the slide, so it
    joins the allow-list instead — a deck's section headings are precisely where
    its unusual vocabulary lives.
    """
    checker = ProseChecker("en", source_text)
    checker.allow(*[s.title or "" for s in narration.segments])
    report = _report(checker)
    if not checker.available:
        return report.finalise()

    for segment in narration.segments:
        report.issues += checker.check(segment.script, f"segments.{segment.index}.script")
    return report.finalise()


def check_assessment(assessment, source_text: str = "") -> ValidationReport:
    """Spell-check an `AssessmentSet`.

    Everything a learner reads is generated: the prompt, the choices, the
    explanation, the key points and the model answer. The evidence quote that
    grounded each question is not stored on the question — it is verified against
    the source and discarded — so there is nothing here that could corrupt it.
    """
    checker = ProseChecker(assessment.language, source_text)
    report = _report(checker)
    if not checker.available:
        return report.finalise()

    for question in assessment.questions:
        at = f"questions.{question.id}"
        report.issues += checker.check(getattr(question, "prompt", ""), f"{at}.prompt")
        if question.explanation:
            report.issues += checker.check(question.explanation, f"{at}.explanation")
        for i, choice in enumerate(getattr(question, "choices", []) or []):
            report.issues += checker.check(choice.text, f"{at}.choices.{i}")
        for i, point in enumerate(getattr(question, "key_points", []) or []):
            report.issues += checker.check(point.text, f"{at}.key_points.{i}")
        model_answer = getattr(question, "model_answer", None)
        if model_answer:
            report.issues += checker.check(model_answer, f"{at}.model_answer")
    return report.finalise()


def check_chapters(chaptered, source_text: str = "") -> ValidationReport:
    """Spell-check chapter titles, and nothing else.

    The transcript is a record of what a speaker said. Whisper's mistakes are a
    transcription problem, not a spelling one, and flagging them here would bury
    the titles — the only text on this artefact the model actually wrote — under
    noise from every proper noun in the recording.
    """
    checker = ProseChecker("en", source_text)
    report = _report(checker)
    if not checker.available:
        return report.finalise()

    for i, chapter in enumerate(chaptered.chapters):
        report.issues += checker.check(chapter.title, f"chapters.{i}.title")
    return report.finalise()
