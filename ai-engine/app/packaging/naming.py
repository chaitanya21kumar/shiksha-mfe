"""Text that crosses into a generated package, and out again as a header.

Both helpers here were originally private to the assessment emitter. They moved
here when Module C started emitting a second content type, because both are the kind
of rule that is silently wrong when it is re-implemented rather than shared:

- **Escaping.** H5P injects these strings into the DOM by concatenation, so a
  model-written title carrying ``<`` is markup by the time a learner sees it. The
  one escaper here is what makes "everything the model wrote is escaped" a fact
  about the package rather than a claim in a docstring.
- **Filenames.** The package name is interpolated into ``Content-Disposition``,
  whose value Starlette encodes as latin-1. A Hindi lecture called
  ``व्याख्यान.mp4`` would otherwise raise ``UnicodeEncodeError`` *after* the
  package was built, and a name carrying ``"`` or CRLF would corrupt the header.
"""

from __future__ import annotations

import html
import re

#: Everything outside this set is replaced. Deliberately an allow-list: it is the
#: only form that stays correct as new scripts and new punctuation turn up.
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def escape_text(text: str) -> str:
    """Escape model text for an H5P field.

    Quotes are left alone: these are text nodes, not attribute values, and
    escaping them only hurts readability.
    """
    return html.escape(text or "", quote=False)


def sanitise_filename(stem: str, *, fallback: str) -> str:
    """Reduce a filename stem to what a ``Content-Disposition`` header can carry."""
    safe = _UNSAFE_IN_FILENAME.sub("-", stem or "").strip("-")
    return safe or fallback
