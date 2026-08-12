"""H5P packaging primitives: versions, manifest, subcontent, and the ``.h5p`` ZIP.

Format knowledge only — nothing here knows what an assessment is. The mapping
from a domain model onto an H5P content type lives with the module that owns
that model (for Module B, ``app/assessment/emit/h5p.py``).
"""

from .manifest import build_manifest, sanitise_language, sanitise_title
from .package import CONTENT_NAME, MANIFEST_NAME, H5PPackage, write_h5p
from ..naming import escape_text, sanitise_filename
from .subcontent import subcontent_id, wrap
from .versions import (
    ADVANCED_TEXT,
    ALLOWED_INTERACTION_LIBRARIES,
    ALLOWED_QUESTION_LIBRARIES,
    ALLOWED_SLIDE_ELEMENT_LIBRARIES,
    BLANKS,
    COURSE_PRESENTATION,
    COURSE_PRESENTATION_CLOSURE,
    CLOSURE,
    DRAGTEXT,
    ESSAY,
    INTERACTIVE_VIDEO,
    INTERACTIVE_VIDEO_CLOSURE,
    MULTICHOICE,
    QUESTIONSET,
    Library,
    dependency,
    library_string,
)

__all__ = [
    "ADVANCED_TEXT",
    "ALLOWED_INTERACTION_LIBRARIES",
    "ALLOWED_QUESTION_LIBRARIES",
    "ALLOWED_SLIDE_ELEMENT_LIBRARIES",
    "BLANKS",
    "CLOSURE",
    "COURSE_PRESENTATION",
    "COURSE_PRESENTATION_CLOSURE",
    "CONTENT_NAME",
    "DRAGTEXT",
    "ESSAY",
    "H5PPackage",
    "INTERACTIVE_VIDEO",
    "INTERACTIVE_VIDEO_CLOSURE",
    "Library",
    "MANIFEST_NAME",
    "MULTICHOICE",
    "QUESTIONSET",
    "build_manifest",
    "dependency",
    "escape_text",
    "library_string",
    "sanitise_filename",
    "sanitise_language",
    "sanitise_title",
    "subcontent_id",
    "wrap",
    "write_h5p",
]
