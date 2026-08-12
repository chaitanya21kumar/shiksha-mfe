"""Errors raised while turning a `MicroLesson` into a packaged artefact."""

from __future__ import annotations


class EmptyLessonError(ValueError):
    """There is nothing left to package.

    A lesson arrives with at least one step — the pipeline refuses to build one
    otherwise — but a step can still be dropped here if every piece of text on it
    is empty once escaped. A presentation with no slides imports as a broken shell
    rather than failing, so this surfaces as a 400 instead of a corrupt download.
    """
