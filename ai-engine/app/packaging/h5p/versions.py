"""The H5P library versions we emit, and where each number came from.

Every version in this file is read from the package the **H5P Hub** serves today
(``POST https://api.h5p.org/v1/content-types/`` then
``GET https://api.h5p.org/v1/content-types/H5P.QuestionSet``), not from the
libraries' GitHub master branches. That distinction is the whole reason this file
exists: an LMS installs its content types *from the Hub*, so the Hub is what a
real Moodle actually has. Master is routinely ahead of it — when this table was
built, master's Question Set was 1.21 and its Drag Question 1.15, while the Hub
served 1.20 and 1.14.

Getting that wrong is not a soft failure. ``questions[].library`` is matched by
**exact string equality** against `ALLOWED_QUESTION_LIBRARIES`, which is baked
into the *installed* Question Set's own ``semantics.json``. A package naming a
version the host does not have is rejected outright.

So: this is the single place any version is re-pinned. If a tenant runs an older
Question Set, read its installed ``semantics.json`` and change these constants —
never hunt through the emitter.
"""

from __future__ import annotations

# (machineName, majorVersion, minorVersion) — H5P versions are matched on major
# and minor only; the patch version is not part of a library's identity.
Library = tuple[str, int, int]

QUESTIONSET: Library = ("H5P.QuestionSet", 1, 20)
MULTICHOICE: Library = ("H5P.MultiChoice", 1, 16)
BLANKS: Library = ("H5P.Blanks", 1, 14)
DRAGTEXT: Library = ("H5P.DragText", 1, 10)
ESSAY: Library = ("H5P.Essay", 1, 5)
INTERACTIVE_VIDEO: Library = ("H5P.InteractiveVideo", 1, 27)
COURSE_PRESENTATION: Library = ("H5P.CoursePresentation", 1, 26)
#: The text element a slide carries. One field, ``text``, and no dependencies of
#: its own — which is why a lesson made of prose pulls in nothing extra.
ADVANCED_TEXT: Library = ("H5P.AdvancedText", 1, 1)

# Shared dependencies, named for the same reason: every closure below is built
# from these constants rather than re-spelling the tuples, so re-pinning a version
# is one edit and a closure cannot quietly disagree with the library it declares.
_QUESTION: Library = ("H5P.Question", 1, 5)
_JOUBELUI: Library = ("H5P.JoubelUI", 1, 3)
_TRANSITION: Library = ("H5P.Transition", 1, 0)
_FONTICONS: Library = ("H5P.FontIcons", 1, 0)
_TEXTUTILITIES: Library = ("H5P.TextUtilities", 1, 3)
_VIDEO: Library = ("H5P.Video", 1, 6)
_FONTAWESOME: Library = ("FontAwesome", 4, 5)
_JQUERY_UI: Library = ("jQuery.ui", 1, 10)
_DRAGNBAR: Library = ("H5P.DragNBar", 1, 5)
_DRAGNDROP: Library = ("H5P.DragNDrop", 1, 1)
_DRAGNRESIZE: Library = ("H5P.DragNResize", 1, 2)

#: The flattened transitive closure of the four libraries above, resolved by
#: walking ``preloadedDependencies`` through the ``library.json`` of every
#: library inside the Hub's own Question Set package.
#:
#: Editor dependencies are deliberately absent: H5P's exporter skips them
#: (``if ($dependency['type'] === 'editor') { continue; }``), and a naive
#: "copy every dependency" pass would wrongly pull in H5PEditor.RangeList and
#: friends, which Blanks and DragText both declare.
CLOSURE: tuple[Library, ...] = (
    QUESTIONSET,
    MULTICHOICE,
    BLANKS,
    DRAGTEXT,
    # Essay's own dependencies — H5P.Question, H5P.JoubelUI and H5P.TextUtilities —
    # were already in this closure for the other three types, so it adds itself and
    # nothing else. TextUtilities is the one that matters: its `isIsolated` is what
    # decides whether a keyword matched.
    ESSAY,
    _QUESTION,
    _JOUBELUI,
    _TRANSITION,
    _FONTICONS,
    _TEXTUTILITIES,
    _VIDEO,
    _FONTAWESOME,
    _JQUERY_UI,
)

#: Verbatim from ``H5P.QuestionSet-1.20/semantics.json`` → ``questions.field.options``.
#: A Question Set will only accept a child question whose ``library`` string is in
#: this set, compared literally.
ALLOWED_QUESTION_LIBRARIES: frozenset[str] = frozenset(
    {
        "H5P.MultiChoice 1.16",
        "H5P.DragQuestion 1.14",
        "H5P.Blanks 1.14",
        "H5P.MarkTheWords 1.11",
        "H5P.DragText 1.10",
        "H5P.TrueFalse 1.8",
        "H5P.Essay 1.5",
        "H5P.MultiMediaChoice 0.3",
    }
)


#: Verbatim from ``H5P.InteractiveVideo-1.27/semantics.json`` →
#: ``interactiveVideo.assets.interactions.field.action.options``. An interaction's
#: ``action.library`` is matched against this list literally, exactly as a Question
#: Set matches its own children.
#:
#: **H5P.Essay is deliberately absent — it is not in the list.** Interactive Video
#: permits eighteen libraries and Essay is not one of them, so a short-answer
#: question cannot be embedded in a video and has to be dropped and reported.
#: Every library present in *both* whitelists is pinned at the same version in
#: each, so a question this engine already emits maps across unchanged.
ALLOWED_INTERACTION_LIBRARIES: frozenset[str] = frozenset(
    {
        "H5P.Nil 1.0",
        "H5P.Text 1.1",
        "H5P.Table 1.2",
        "H5P.Link 1.3",
        "H5P.Image 1.1",
        "H5P.Summary 1.10",
        "H5P.SingleChoiceSet 1.11",
        "H5P.MultiChoice 1.16",
        "H5P.TrueFalse 1.8",
        "H5P.Blanks 1.14",
        "H5P.DragQuestion 1.14",
        "H5P.MarkTheWords 1.11",
        "H5P.DragText 1.10",
        "H5P.GoToQuestion 1.3",
        "H5P.IVHotspot 1.2",
        "H5P.Questionnaire 1.3",
        "H5P.FreeTextQuestion 1.0",
        "H5P.MultiMediaChoice 0.3",
    }
)

#: The closure for an Interactive Video carrying the three question types this
#: engine can embed. Interactive Video's own runtime closure is eight libraries;
#: four of them (FontAwesome, jQuery.ui, H5P.Video, H5P.FontIcons) were already
#: here for the Question Set path, so it adds exactly four.
#:
#: The editor exclusion matters even more here than it did for the Question Set:
#: following ``editorDependencies`` as well would take this from 15 libraries to
#: 53, declaring the entire H5PEditor tree as a runtime requirement. The Hub's own
#: package proves the rule — 53 library folders on disk, 33 entries in its
#: ``h5p.json``.
INTERACTIVE_VIDEO_CLOSURE: tuple[Library, ...] = (
    INTERACTIVE_VIDEO,
    # Interactive Video's own four…
    _VIDEO,
    _DRAGNBAR,
    _FONTAWESOME,
    _JQUERY_UI,
    # …and DragNBar's two, which nothing else here pulls in.
    _DRAGNDROP,
    _DRAGNRESIZE,
    _FONTICONS,
    # The embeddable question types and their shared dependencies.
    MULTICHOICE,
    BLANKS,
    DRAGTEXT,
    _QUESTION,
    _JOUBELUI,
    _TRANSITION,
    _TEXTUTILITIES,
)


def library_string(library: Library) -> str:
    """Render a library as H5P names it in ``questions[].library``: ``"Name 1.2"``."""
    machine_name, major, minor = library
    return f"{machine_name} {major}.{minor}"


def dependency(library: Library) -> dict[str, object]:
    """Render a library as a ``preloadedDependencies`` entry.

    The versions are ints. H5P validates them with the regex ``/^[0-9]{1,5}$/``
    guarded by ``is_string($v) || is_int($v)``, so ints and numeric strings are
    both accepted; ints are simply the natural JSON form.
    """
    machine_name, major, minor = library
    return {"machineName": machine_name, "majorVersion": major, "minorVersion": minor}

#: Verbatim from ``H5P.CoursePresentation-1.26/semantics.json`` →
#: ``presentation.slides.field.elements.field.action.options``. A slide element's
#: ``action.library`` is matched against this list literally, exactly as a Question
#: Set matches its children and an Interactive Video matches its interactions.
ALLOWED_SLIDE_ELEMENT_LIBRARIES: frozenset[str] = frozenset(
    {
        "H5P.AdvancedText 1.1",
        "H5P.Link 1.3",
        "H5P.Image 1.1",
        "H5P.Shape 1.0",
        "H5P.Video 1.6",
        "H5P.Audio 1.5",
        "H5P.Blanks 1.14",
        "H5P.SingleChoiceSet 1.11",
        "H5P.MultiChoice 1.16",
        "H5P.TrueFalse 1.8",
        "H5P.DragQuestion 1.14",
        "H5P.Summary 1.10",
        "H5P.DragText 1.10",
        "H5P.MarkTheWords 1.11",
        "H5P.Dialogcards 1.9",
        "H5P.ContinuousText 1.2",
        "H5P.ExportableTextArea 1.3",
        "H5P.Table 1.2",
        "H5P.InteractiveVideo 1.27",
        "H5P.TwitterUserFeed 1.0",
        "H5P.AudioRecorder 1.0",
        "H5P.MultiMediaChoice 0.3",
    }
)

#: The closure for a Course Presentation built out of text slides.
#:
#: Resolved by walking ``preloadedDependencies`` through every ``library.json`` in
#: the Hub's own Course Presentation package, not by reading the top-level list.
#: That distinction produced a real difference here: Course Presentation declares
#: only three direct dependencies, but **H5P.Transition arrives through
#: H5P.JoubelUI**, so a package built from the direct list alone would be missing a
#: library. Six in total, and four of them were already in this file for the
#: assessment and interactive-video closures.
COURSE_PRESENTATION_CLOSURE: tuple[Library, ...] = (
    COURSE_PRESENTATION,
    ADVANCED_TEXT,
    _JOUBELUI,
    _TRANSITION,
    _FONTICONS,
    _FONTAWESOME,
)
