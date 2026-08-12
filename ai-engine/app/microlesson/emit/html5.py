"""A `MicroLesson` as a standalone HTML5 slide deck.

The thinnest of the three targets, and deliberately so: it is `deck.render_deck`
written to a single file. One file, no folder, no unzipping, nothing fetched from
the network — a teacher can mail it to themselves and open it on any machine.

That is the whole point of this format existing alongside the other two. H5P needs
an LMS that has H5P installed; SCORM needs an LMS at all. This needs a browser.
"""

from __future__ import annotations

from typing import NamedTuple

from ...packaging.naming import sanitise_filename
from ..schema import MicroLesson
from .deck import render_deck
from .errors import EmptyLessonError


class Html5Package(NamedTuple):
    """A built `.html` and anything the caller should know about it."""

    content: bytes
    filename: str
    warnings: list[str]


def emit_html5(lesson: MicroLesson) -> Html5Package:
    """Package a lesson as one self-contained HTML file."""
    try:
        document = render_deck(lesson)
    except ValueError as exc:
        raise EmptyLessonError(str(exc)) from exc

    stem = sanitise_filename(lesson.title, fallback="micro-lesson")
    return Html5Package(
        content=document.encode("utf-8"),
        filename=f"{stem}.html",
        warnings=list(lesson.warnings),
    )
