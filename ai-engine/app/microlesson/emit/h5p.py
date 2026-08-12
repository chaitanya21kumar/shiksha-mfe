r"""Maps a `MicroLesson` onto an H5P Course Presentation.

This is the domain half of Module D's H5P packaging. The format half — versions,
the manifest, the ZIP — lives in `app.packaging.h5p`, unchanged from Module B.

Every fact below was read out of the package the **H5P Hub** serves today
(``GET https://api.h5p.org/v1/content-types/H5P.CoursePresentation``), not from
documentation and not from GitHub master. That distinction is the same one
`versions.py` makes for Question Set, and for the same reason: an LMS installs its
content types from the Hub, and master routinely runs ahead of it.

**The shape.** ``presentation.slides[]`` — each slide holds ``elements[]``, and an
element is ``{x, y, width, height, action, ...}`` where ``action`` is a library
instance wrapped exactly as a Question Set wraps its children. The four geometry
fields are **percentages of the slide**, verified in the runtime rather than
assumed: ``left: e.x + "%", top: e.y + "%", width: e.width + "%", height:
e.height + "%"``.

**One step, one slide.** The lesson already decided how many steps there are, in
Python, for the reasons ADR-0011 gives. This emitter does not get to disagree —
it renders what it is handed, so the package has exactly as many slides as the
lesson had steps, plus an objectives slide when there are objectives.

**Where the notes go, and what it costs.** Course Presentation has **no
speaker-notes field**. There is no ``notes``, no ``presenterNotes``, nothing of
the kind anywhere in its semantics. The nearest real field is ``solution``,
labelled *"Comments — shown when the user displays the suggested answers for all
slides"*. Reading the runtime settles what it actually does::

    e.solution && this.addElementSolutionButton(e, t, a)

— it is created for **any** element carrying comment text, with no dependency on
the slide holding a question, and it renders as a button that opens the text in a
popup. So the notes get a real, working home, and a learner reaches them in one
click rather than having them crowd the slide.

The cost, stated plainly rather than hidden: they are Comments, so H5P's global
"show solutions" action reveals them all at once. For a lesson with no questions
that is harmless, and it is a far better outcome than the alternative. Inventing
a ``notes`` key would have been worse than useless — ``H5PContentValidator``
drops fields it does not recognise **without raising**, so the package would
import cleanly, look correct, and simply never show the notes. That is the same
trap that hid four defects in the interactive video and made a timer impossible
in the Question Set.

**``l10n`` is deliberately not emitted, and that is a checked decision rather than
an omission.** Module C emits all 47 of Interactive Video's interface strings,
because that player defaults only 35 of them and the twelve it misses are exactly
the ones on the submit path. The same check run against this library gives the
opposite answer: its runtime does
``this.l10n = n.extend({slide: "Slide", …}, params.l10n)`` over a literal covering
**52** keys, against **49** declared in its semantics — every declared string is
defaulted, with three spare. So the block would be 49 keys of dead weight, and
copying Module C's approach here without re-running the check would have been
cargo-culting a fix for a problem this library does not have.

**Everything the model wrote is escaped.** H5P.AdvancedText injects its ``text``
field as HTML, and that text originates in a tenant's uploaded document, so an
unescaped bullet is a script-injection path into their LMS. The escaping helper is
the same one Module B uses.
"""

from __future__ import annotations

from ...packaging.h5p import (
    ADVANCED_TEXT,
    COURSE_PRESENTATION,
    COURSE_PRESENTATION_CLOSURE,
    H5PPackage,
    build_manifest,
    escape_text,
    sanitise_filename,
    sanitise_title,
    wrap,
    write_h5p,
)
from ..schema import MicroLesson
from .errors import EmptyLessonError

#: Slide geometry, in percent. A title band across the top and a body beneath it,
#: with equal margins. These are the only numbers in this module that are a design
#: choice rather than something the format dictates, so they are named rather than
#: scattered through the builder.
_MARGIN_X = 6.0
_TITLE_TOP = 7.0
_TITLE_HEIGHT = 14.0
_BODY_TOP = 25.0
_BODY_HEIGHT = 66.0
_WIDTH = 100.0 - (2 * _MARGIN_X)

#: What H5P shows for a child in an editor. No runtime behaviour, but every real
#: package sets it, and a package that omits it looks machine-made in a way that
#: invites doubt about the rest.
_TEXT_CONTENT_TYPE = "Text"


def _text_element(
    *,
    html: str,
    lesson_id: str,
    element_id: str,
    label: str,
    y: float,
    height: float,
    comment: str = "",
) -> dict[str, object]:
    """One positioned `H5P.AdvancedText` on a slide.

    ``comment`` becomes the element's ``solution``, which the player turns into a
    button. Empty means no button, because the runtime guards on the field being
    truthy — so a step without notes simply has nothing extra on it.

    ``label`` is what an editor shows for the child, and it is deliberately a
    generated string rather than the step's own heading. The heading is model text
    from a tenant's document, and ``metadata.title`` is a **plain-text** field:
    escaping it would store entities where the author wrote characters, and passing
    it raw would put untrusted text somewhere this module has not verified the
    handling of. Module B answered the same question by passing a question id, and
    matching that is better than inventing a third answer.
    """
    element: dict[str, object] = {
        "x": _MARGIN_X,
        "y": y,
        "width": _WIDTH,
        "height": height,
        "action": wrap(
            library=ADVANCED_TEXT,
            params={"text": html},
            content_type=_TEXT_CONTENT_TYPE,
            title=label,
            assessment_id=lesson_id,
            question_id=element_id,
        ),
        "displayAsButton": False,
        "invisible": False,
    }
    if comment:
        element["solution"] = comment
        # Misleading name, and the reason a first version of this shipped notes
        # nobody could reach. It does not mean "show the comment text"; it is the
        # *only* thing that builds the button at all::
        #
        #     void 0 !== e.alwaysDisplayComments && e.alwaysDisplayComments
        #         && t.showCPComments()
        #
        # Left false, `showCPComments` is defined and never called, so the notes sit
        # in the package and are unreachable. The other two callers are both on the
        # show-solutions path, which a lesson never offers — there are no questions,
        # and the summary slide that carries the button is switched off below.
        # Setting it true renders the button; the popup still only opens on click.
        element["alwaysDisplayComments"] = True
    return element


def _slide(elements: list[dict[str, object]]) -> dict[str, object]:
    """A slide with no keywords and no background of its own.

    ``keywords`` is optional in the semantics and the keyword list is switched off
    for the whole presentation below, so emitting an empty one would add a field
    nothing reads. ``slideBackgroundSelector`` is a group with every member
    optional, and an empty group is how "inherit the global background" is spelt.
    """
    return {"elements": elements, "slideBackgroundSelector": {}}


def _bullet_list(bullets: list[str]) -> str:
    """The on-screen points as an escaped HTML list.

    A list rather than paragraphs because that is what the source is: a slide's
    points. Blank entries are dropped instead of rendering an empty bullet.
    """
    items = "".join(f"<li>{escape_text(b.strip())}</li>" for b in bullets if b and b.strip())
    return f"<ul>{items}</ul>" if items else ""


def _objectives_slide(lesson: MicroLesson) -> dict[str, object] | None:
    """An opening slide listing what a learner should be able to do.

    Skipped entirely when the lesson has no objectives, rather than emitted empty:
    a title-only slide with nothing under it reads as a mistake to anyone opening
    the package.
    """
    body = _bullet_list(lesson.objectives)
    if not body:
        return None
    heading = escape_text("What you will be able to do")
    return _slide(
        [
            _text_element(
                html=f"<h2>{heading}</h2>",
                lesson_id=lesson.lesson_id,
                element_id="objectives-title",
                label="Objectives",
                y=_TITLE_TOP,
                height=_TITLE_HEIGHT,
            ),
            _text_element(
                html=body,
                lesson_id=lesson.lesson_id,
                element_id="objectives-body",
                label="Objectives",
                y=_BODY_TOP,
                height=_BODY_HEIGHT,
            ),
        ]
    )


def _step_slide(lesson: MicroLesson, step_index: int) -> dict[str, object] | None:
    """One slide for one step, or None when the step carries no renderable text.

    A step is dropped only if *both* its heading and its points come out empty
    after escaping. That is close to impossible given the contract requires a
    non-empty title, but "close to impossible" is where silent corruption lives:
    without this guard an all-whitespace step would emit a blank slide that
    imports fine and confuses whoever opens it.
    """
    step = lesson.steps[step_index]
    heading = escape_text(step.title.strip())
    body = _bullet_list(step.bullets)
    if not heading and not body:
        return None

    elements: list[dict[str, object]] = []
    if heading:
        elements.append(
            _text_element(
                html=f"<h2>{heading}</h2>",
                lesson_id=lesson.lesson_id,
                element_id=f"step-{step.index}-title",
                label=f"Step {step.index}",
                y=_TITLE_TOP,
                height=_TITLE_HEIGHT,
            )
        )
    if body:
        elements.append(
            _text_element(
                html=body,
                lesson_id=lesson.lesson_id,
                element_id=f"step-{step.index}-body",
                label=f"Step {step.index}",
                y=_BODY_TOP,
                height=_BODY_HEIGHT,
                # The teacher's spoken notes, as the element's Comments. See the
                # module docstring for why this field and what it costs.
                comment=escape_text(step.notes.strip()),
            )
        )
    return _slide(elements)


def build_content(lesson: MicroLesson) -> dict[str, object]:
    """The ``content.json`` payload for a Course Presentation."""
    slides: list[dict[str, object]] = []
    opening = _objectives_slide(lesson)
    if opening is not None:
        slides.append(opening)
    for index in range(len(lesson.steps)):
        slide = _step_slide(lesson, index)
        if slide is not None:
            slides.append(slide)

    if not slides:
        raise EmptyLessonError("The lesson has no step with any text to put on a slide.")

    return {
        "presentation": {
            "slides": slides,
            # The keyword sidebar is a navigation aid built from per-slide keywords
            # we do not have. Left off rather than shipped empty, which renders as
            # a blank panel taking a third of the width.
            "keywordListEnabled": False,
            "keywordListAlwaysShow": False,
            "keywordListAutoHide": False,
            "keywordListOpacity": 90,
            "globalBackgroundSelector": {},
        },
        # `override` is where a package states what a learner may do. Both members
        # are booleans in the semantics; the defaults are permissive and we keep
        # them, because a lesson has nothing to withhold.
        "override": {"activeSurface": False, "hideSummarySlide": True},
    }


def emit_h5p(lesson: MicroLesson) -> H5PPackage:
    """Package a lesson as an H5P Course Presentation (`.h5p`)."""
    content = build_content(lesson)
    manifest = build_manifest(
        title=sanitise_title(lesson.title, fallback="Micro-lesson"),
        language=lesson.language,
        main_library=COURSE_PRESENTATION,
        dependencies=COURSE_PRESENTATION_CLOSURE,
    )
    stem = sanitise_filename(lesson.title, fallback="micro-lesson")
    return H5PPackage(
        content=write_h5p(manifest=manifest, content=content),
        filename=f"{stem}.h5p",
        warnings=list(lesson.warnings),
    )
