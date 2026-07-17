"""Builds ``h5p.json``, the manifest at the root of every ``.h5p`` package.

The rules enforced here are read from H5P's own validator
(``h5p-php-library/h5p.classes.php``, ``$h5pRequired``/``$h5pOptional``), because
they are stricter and stranger than the prose documentation suggests:

- Exactly five keys are required: ``title``, ``language``, ``mainLibrary``,
  ``embedTypes``, ``preloadedDependencies``. They are checked with PHP's
  ``isset()``, which treats an explicit ``null`` as *missing* — so a key must
  never be emitted as null to mean "unset".
- ``language`` is ``/^[-a-zA-Z]{1,10}$/`` — **letters and hyphens only.** A
  perfectly valid BCP-47 tag like ``es-419`` is rejected because it has digits.
- ``mainLibrary`` is ``/^[$a-z_][0-9a-z_\\.$]{1,254}$/i``, which a versioned
  value like ``"H5P.QuestionSet 1.20"`` fails. Machine name only.
- ``license`` is optional but, if present, must come from a closed set in which
  ``"MIT"`` and ``"CC-BY"`` are *not* members (it is ``"CC BY"``, with a space).
"""

from __future__ import annotations

import re

from .versions import CLOSURE, QUESTIONSET, Library, dependency

# H5P's own $h5pRequired['language'].
_LANGUAGE_RE = re.compile(r"^[-a-zA-Z]{1,10}$")

# A BCP-47 primary language subtag: ISO 639-1 is two letters, 639-2/639-3 are
# three. Longer runs are reserved or registered and are not languages we would
# ever be handed, so anything else is better reported as undetermined than
# silently truncated into a plausible-looking lie.
_PRIMARY_SUBTAG_RE = re.compile(r"^[a-zA-Z]{2,3}$")

#: "Undisclosed" — the one license value that makes no claim about the content.
#: The generated questions derive from a tenant's own source document, so the
#: engine is in no position to assert a license on their behalf.
UNDISCLOSED_LICENSE = "U"

_MAX_TITLE = 255


def sanitise_language(tag: str) -> str:
    """Coerce a BCP-47 tag into something H5P's manifest regex will accept.

    Falls back to the primary subtag (``es-419`` → ``es``), and finally to
    ``und`` ("undetermined"), which is what H5P's own Hub packages ship.
    """
    candidate = (tag or "").strip()
    if _LANGUAGE_RE.match(candidate):
        return candidate
    primary = candidate.split("-", 1)[0]
    if _PRIMARY_SUBTAG_RE.match(primary):
        return primary
    return "und"


def sanitise_title(title: str, *, fallback: str = "Assessment") -> str:
    """Fit a title to ``/^.{1,255}$/`` — non-empty, and not longer than 255."""
    candidate = " ".join((title or "").split())
    if not candidate:
        candidate = fallback
    return candidate[:_MAX_TITLE]


def build_manifest(
    *,
    title: str,
    language: str,
    main_library: Library = QUESTIONSET,
    dependencies: tuple[Library, ...] = CLOSURE,
) -> dict[str, object]:
    """Build the ``h5p.json`` for a content-only package.

    ``dependencies`` must be the *flattened transitive closure*, and must contain
    ``main_library`` — the manifest names the main library without a version, so
    its version is only ever stated in the dependency list.
    """
    machine_name = main_library[0]
    if not any(dep[0] == machine_name for dep in dependencies):
        # H5P's validator does NOT catch this: the 'Missing main library' error is
        # guarded by !empty($mainDependency) and so fires in the opposite case. An
        # undeclared main library validates cleanly and then breaks in
        # H5PStorage::savePackage, producing broken content. Assert it ourselves.
        raise ValueError(f"main library {machine_name!r} is missing from the dependency closure")

    return {
        "title": sanitise_title(title),
        "language": sanitise_language(language),
        "mainLibrary": machine_name,
        "embedTypes": ["div"],
        "license": UNDISCLOSED_LICENSE,
        "preloadedDependencies": [dependency(lib) for lib in dependencies],
    }
