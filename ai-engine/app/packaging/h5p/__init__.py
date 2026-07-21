"""H5P packaging primitives: versions, manifest, subcontent, and the ``.h5p`` ZIP.

Format knowledge only — nothing here knows what an assessment is. The mapping
from a domain model onto an H5P content type lives with the module that owns
that model (for Module B, ``app/assessment/emit/h5p.py``).
"""

from .manifest import build_manifest, sanitise_language, sanitise_title
from .package import CONTENT_NAME, MANIFEST_NAME, write_h5p
from .subcontent import subcontent_id, wrap
from .versions import (
    ALLOWED_QUESTION_LIBRARIES,
    BLANKS,
    CLOSURE,
    DRAGTEXT,
    ESSAY,
    MULTICHOICE,
    QUESTIONSET,
    Library,
    dependency,
    library_string,
)

__all__ = [
    "ALLOWED_QUESTION_LIBRARIES",
    "BLANKS",
    "CLOSURE",
    "CONTENT_NAME",
    "DRAGTEXT",
    "ESSAY",
    "Library",
    "MANIFEST_NAME",
    "MULTICHOICE",
    "QUESTIONSET",
    "build_manifest",
    "dependency",
    "library_string",
    "sanitise_language",
    "sanitise_title",
    "subcontent_id",
    "wrap",
    "write_h5p",
]
