"""The SCORM 1.2 CMI data model: the value spaces an LMS will actually accept.

Every rule here is copied from a primary source rather than paraphrased, because
SCORM's failure mode is silence — a malformed value is refused with a numeric
error code the SCO must go and ask for, so a wrong format does not crash, it just
means nothing is ever recorded.

Sources:

- ADL *SCORM Run-Time Environment* v1.2 (2001-10-01), the RTE book.
- Moodle 4.5 ``mod/scorm/datamodels/scorm_12.js`` — the regexes below are its
  ``CMITimespan`` / ``CMIDecimal`` / ``CMIStatus`` verbatim.
- ``overhangio/openedx-scorm-xblock`` — the other target.

Where the two LMSs disagree, we encode **Moodle's** rule. Moodle validates and
returns 405; Open edX accepts almost anything. So Moodle is the strict superset:
every Moodle-legal value is Open edX-legal, and the reverse is badly false. That
also means Open edX can never fail a package, and is therefore worthless as a
test oracle.
"""

from __future__ import annotations

import re

# --- vocabularies (RTE book; exact, case-sensitive) --------------------------

#: What a SCO may WRITE to cmi.core.lesson_status.
#:
#: The RTE book lists six values, but "not attempted" is not one a SCO may set:
#: Moodle binds writes to a five-value ``CMIStatus`` and returns 405 for the
#: sixth. It is the LMS's own initial value, readable but not writable — so a SCO
#: that reads the status and echoes it back fails on its first write.
LESSON_STATUS_WRITABLE = ("passed", "completed", "failed", "incomplete", "browsed")

#: cmi.core.exit. "" is a real member and means a normal exit; "normal" is not a
#: member at all, despite reading like one.
EXIT_VALUES = ("time-out", "suspend", "logout", "")

#: cmi.interactions.n.type
INTERACTION_TYPES = (
    "true-false",
    "choice",
    "fill-in",
    "matching",
    "performance",
    "sequencing",
    "likert",
    "numeric",
)

#: cmi.interactions.n.result — a vocabulary, or a CMIDecimal.
INTERACTION_RESULTS = ("correct", "wrong", "unanticipated", "neutral")

# --- formats (Moodle scorm_12.js, verbatim) ----------------------------------

#: A duration: HHHH:MM:SS.SS. Note the hours are **2 to 4 digits** — "0:12:30"
#: is rejected, which would otherwise break every session under ten hours.
CMI_TIMESPAN = re.compile(r"^([0-9]{2,4}):([0-9]{2}):([0-9]{2})(\.[0-9]{1,2})?$")

#: A time of day: HH:MM:SS.SS. Different from a timespan, and easy to confuse —
#: cmi.interactions.n.time is a time of day, cmi.interactions.n.latency is a
#: duration.
CMI_TIME = re.compile(r"^([0-2][0-9]):([0-5][0-9]):([0-5][0-9])(\.[0-9]{1,2})?$")

#: At most three integer digits, so a score or weighting over 999 fails on format
#: before it ever fails on range.
CMI_DECIMAL = re.compile(r"^-?([0-9]{0,3})(\.[0-9]*)?$")

#: Printable ASCII, up to 255.
CMI_IDENTIFIER = re.compile(r"^[!-~]{0,255}$")

#: Moodle's score_range is '0#100'. This is not a house convention: the RTE book
#: requires cmi.core.score.raw to be normalised 0-100 in SCORM 1.2 (unbounded raw
#: plus score.scaled is a 2004 thing). Open edX reads raw/100 and ignores
#: score.max entirely, so a raw of 850 out of 1000 grades as 850% there and is
#: refused outright by Moodle.
SCORE_MIN = 0.0
SCORE_MAX = 100.0

#: cmi.interactions.n.weighting shares CMI_DECIMAL, so anything over 999 is
#: unrepresentable; Moodle's documented range is tighter still.
WEIGHTING_MAX = 100.0

#: A single interaction response is CMIString255.
RESPONSE_MAX_CHARS = 255

#: choice / matching / likert / sequencing identifiers are ONE character, from
#: 0-9 and a-z. Our contract's ids ("q1-c1") are not, so the emitter maps them by
#: position through this alphabet — which is also why a question with more than
#: 36 options simply cannot be reported.
RESPONSE_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def format_timespan(seconds: float) -> str:
    """Render a duration as CMITimespan (``HHHH:MM:SS.SS``).

    Two details are load-bearing: the hours are zero-padded to two digits because
    Moodle's regex demands 2-4 of them, and the fraction is centiseconds because
    it allows at most two decimal places. ISO 8601 (``PT1H30M``) is SCORM 2004
    and is refused here.
    """
    centiseconds = max(0, int(round(seconds * 100)))
    hours, rest = divmod(centiseconds, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, cents = divmod(rest, 100)
    return f"{min(hours, 9999):02d}:{minutes:02d}:{secs:02d}.{cents:02d}"


def format_score(earned: float, possible: float) -> str:
    """Render a score as a CMIDecimal percentage, clamped to 0-100.

    The ``.2f`` is not cosmetic. It guarantees the string contains a ``.``, which
    is what stops the ``rstrip("0")`` from eating a significant zero: "50.00" ->
    "50." -> "50", whereas rstrip on a bare "100" would give "1". Do not reorder
    or "simplify" these two steps.
    """
    if possible <= 0:
        return "0"
    percentage = max(SCORE_MIN, min(SCORE_MAX, (earned / possible) * 100.0))
    rendered = f"{percentage:.2f}".rstrip("0").rstrip(".")
    return rendered or "0"


def response_char(index: int) -> str:
    """The single-character identifier SCORM 1.2 uses for the nth option."""
    if index < 0 or index >= len(RESPONSE_ALPHABET):
        raise ValueError(
            f"SCORM 1.2 identifiers are a single character, so option {index} "
            f"cannot be encoded (limit {len(RESPONSE_ALPHABET)})"
        )
    return RESPONSE_ALPHABET[index]
