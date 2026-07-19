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

#: The flattened transitive closure of the four libraries above, resolved by
#: walking ``preloadedDependencies`` through the ``library.json`` of every
#: library inside the Hub's own Question Set package.
#:
#: Editor dependencies are deliberately absent: H5P's exporter skips them
#: (``if ($dependency['type'] === 'editor') { continue; }``), and a naive
#: "copy every dependency" pass would wrongly pull in H5PEditor.RangeList and
#: friends, which Blanks and DragText both declare.
CLOSURE: tuple[Library, ...] = (
    ("H5P.QuestionSet", 1, 20),
    ("H5P.MultiChoice", 1, 16),
    ("H5P.Blanks", 1, 14),
    ("H5P.DragText", 1, 10),
    # Essay's own dependencies — H5P.Question, H5P.JoubelUI and H5P.TextUtilities —
    # were already in this closure for the other three types, so it adds itself and
    # nothing else. TextUtilities is the one that matters: its `isIsolated` is what
    # decides whether a keyword matched.
    ("H5P.Essay", 1, 5),
    ("H5P.Question", 1, 5),
    ("H5P.JoubelUI", 1, 3),
    ("H5P.Transition", 1, 0),
    ("H5P.FontIcons", 1, 0),
    ("H5P.TextUtilities", 1, 3),
    ("H5P.Video", 1, 6),
    ("FontAwesome", 4, 5),
    ("jQuery.ui", 1, 10),
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
