r"""Marks a short answer against its key points.

This is a faithful port of the matcher inside ``H5P.Essay 1.5.13`` — the version
the H5P Hub serves — and it exists in three places by necessity: here, in the
SCORM player's JavaScript, and in H5P's own library. All three must award the same
mark for the same text, or one learner gets a different result depending on which
package their LMS imported. The parity test in ``tests/test_assessment_grading.py``
is what holds them together.

Two details are reproduced *exactly*, including one that is arguably a bug:

- ``.replace(/\s\s/g, " ")`` is a single non-overlapping pass, so it **halves runs
  of whitespace rather than collapsing them**: ``"a    b"`` becomes ``"a  b"``.
  Writing ``\s+`` here would be tidier and would silently disagree with H5P.
- A match only counts when it is *word-isolated* — the characters either side must
  be a word delimiter or a string boundary. This is what stops ``"grassland heats
  up"`` from matching the key point ``"land heats"``.

What this does **not** do is judge an answer. It detects whether specific phrases —
each of which was quoted out of the source document — are present. A learner who
writes those phrases in a meaningless order scores full marks; one who is entirely
correct in different words scores zero. Both are demonstrated in the tests, and
both are disclosed to the learner on the results screen rather than papered over.
"""

from __future__ import annotations

import re

from .schema import KeyPoint, ShortAnswerItem

#: ``H5P.TextUtilities.isIsolated``'s word delimiters, verbatim.
_WORD_DELIMITER = re.compile(r"[\s.?!,';\"]")

#: ``Essay.prototype.getInput``: newlines become spaces before anything else. The
#: default ``behaviour.linebreakReplacement`` is a single space, which is what we
#: emit.
_NEWLINES = re.compile(r"(\r\n|\r|\n)")


def normalise(text: str) -> str:
    r"""Normalise learner text exactly as ``Essay.prototype.getInput`` does.

    The double-space replacement is deliberately not ``\s+``: H5P's is a single
    non-overlapping pass, so four spaces become two rather than one. Matching that
    quirk is the difference between the two packages agreeing and disagreeing.
    """
    collapsed = _NEWLINES.sub(" ", text or "")
    return collapsed.replace("  ", " ").lower()


def _is_isolated(needle: str, haystack: str, position: int) -> bool:
    """Whether a match sits on word boundaries, as H5P.TextUtilities requires."""
    before = "" if position == 0 else _WORD_DELIMITER.sub("", haystack[position - 1])
    end = position + len(needle)
    after = "" if end == len(haystack) else _WORD_DELIMITER.sub("", haystack[end])
    return before == "" and after == ""


def point_is_made(point: KeyPoint, text: str) -> bool:
    """Whether any of a key point's accepted forms appears, word-isolated, in the text."""
    haystack = normalise(text)
    for form in point.accepted:
        needle = form.strip().lower()
        if not needle:
            continue
        start = haystack.find(needle)
        while start != -1:
            if _is_isolated(needle, haystack, start):
                return True
            start = haystack.find(needle, start + 1)
    return False


def key_points_made(item: ShortAnswerItem, text: str) -> list[str]:
    """The ids of the key points this answer made, in the order they are marked."""
    return [point.id for point in item.key_points if point_is_made(point, text)]


def score_short_answer(item: ShortAnswerItem, text: str) -> float:
    """Mark an answer: the summed weight of every key point it makes.

    The contract pins ``points`` to the sum of the weights, so a full-marks answer
    scores exactly ``item.points`` — which is also what H5P's ``getMaxScore()``
    returns for the emitted keywords, and what our SCORM grader uses as the
    denominator.
    """
    made = set(key_points_made(item, text))
    return float(sum(point.weight for point in item.key_points if point.id in made))
