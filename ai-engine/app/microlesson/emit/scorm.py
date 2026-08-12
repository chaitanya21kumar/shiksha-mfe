"""A `MicroLesson` as a SCORM 1.2 course.

The third target, and the only one that reports anything back. H5P and HTML5 both
hand a learner a lesson and hear nothing more; a SCORM package tells the LMS who
opened it and how far they got, which is what makes it the format a gradebook can
use.

The presentation is `deck.render_deck` — byte for byte the same renderer the HTML5
download uses. That is deliberate: two renderers would drift, and a teacher who
compares the two downloads should see the same lesson, not a family resemblance.
SCORM adds exactly two things to it, both injected through the seams the deck
exposes: the API wrapper, and a reporting script that listens on
``LessonDeck.onSlide``.

An earlier version also injected a small JSON island describing the lesson, "in
case anything wanted it". Nothing did — the reporter reads the slide count off the
deck — and it was a live cross-site-scripting hole: ``json.dumps`` does not escape
``<``, so a lesson titled ``</script><script>…`` closed the island early and ran
whatever followed, inside a tenant's LMS. Deleted rather than escaped. Speculative
fields are the ones nobody audits.

``api.js`` is shared with Module B rather than copied — it implements ADL's own
discovery algorithm and has nothing assessment-specific in it.

**What a lesson can honestly report**, and why it is less than a quiz reports:

- **No score.** A lesson asks nothing. Writing ``cmi.core.score.raw`` of 0 out of
  0 is not "no score", it is a zero, and more than one LMS renders that as a
  failed attempt.
- **Completion means the last slide was reached.** It is the only signal the
  content actually carries. Time is not used as a proxy, because a tab left open
  is not a lesson read.
- **``lesson_status`` moves ``incomplete`` → ``completed`` and never touches
  ``passed`` or ``failed``**, because nothing here is being judged.

``adlcp:masteryscore`` stays absent for the reason ADR-0005 records for the
assessment package, and it matters more here: there is no score for a mastery
threshold to compare against, so an LMS deriving pass/fail from one would be
inventing a verdict out of nothing.
"""

from __future__ import annotations

from importlib import resources
from typing import NamedTuple

from ...packaging.naming import sanitise_filename
from ...packaging.scorm import LAUNCH_NAME, build_manifest, write_scorm
from ..schema import MicroLesson
from .deck import render_deck
from .errors import EmptyLessonError

#: `api.js` is Module B's, unchanged — it finds the LMS and wraps it, and knows
#: nothing about questions. `lesson.js` is this module's own reporting layer.
_SHARED_ASSETS = "app.packaging.scorm"
_OWN_ASSETS = "app.microlesson.emit"

API_NAME = "scorm/api.js"
REPORTER_NAME = "scorm/lesson.js"


class ScormPackage(NamedTuple):
    """A built SCORM `.zip` and anything the caller should know about it."""

    content: bytes
    filename: str
    warnings: list[str]


def _shared_asset(name: str) -> str:
    return resources.files(_SHARED_ASSETS).joinpath("assets", name).read_text(encoding="utf-8")


def _own_asset(name: str) -> str:
    return resources.files(_OWN_ASSETS).joinpath("assets", name).read_text(encoding="utf-8")


def emit_scorm(lesson: MicroLesson) -> ScormPackage:
    """Package a lesson as a SCORM 1.2 course (`.zip`)."""
    try:
        document = render_deck(
            lesson,
            # The scripts go after the deck's own, so `window.LessonDeck` exists by
            # the time the reporter looks for it. The reporter returns immediately
            # if it does not, which is what keeps the file openable outside an LMS.
            extra_body=(
                f'<script src="{API_NAME}"></script>\n'
                f'<script src="{REPORTER_NAME}"></script>\n'
            ),
        )
    except ValueError as exc:
        raise EmptyLessonError(str(exc)) from exc

    files = {
        LAUNCH_NAME: document.encode("utf-8"),
        API_NAME: _shared_asset("api.js").encode("utf-8"),
        REPORTER_NAME: _own_asset("lesson.js").encode("utf-8"),
    }
    manifest = build_manifest(
        assessment_id=lesson.lesson_id,
        title=lesson.title.strip() or "Micro-lesson",
        launch_href=LAUNCH_NAME,
        files=sorted(files),
    )
    stem = sanitise_filename(lesson.title, fallback="micro-lesson")
    return ScormPackage(
        content=write_scorm(manifest=manifest, files=files),
        filename=f"{stem}-scorm.zip",
        warnings=list(lesson.warnings),
    )
