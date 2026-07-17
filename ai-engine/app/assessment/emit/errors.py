"""Errors raised while turning an `AssessmentSet` into a packaged artifact."""

from __future__ import annotations


class EmptyAssessmentError(ValueError):
    """There is nothing left to package.

    Either the set arrived with no questions, or every question was dropped for
    being unsafe to render (see the markup guards in the H5P emitter). An H5P
    Question Set requires at least one question, so an empty package is not a
    thing we can emit — this surfaces as a 400 rather than a corrupt download.
    """
