"""SCORM 1.2 packaging primitives: the data model, the manifest, and the ZIP.

Format knowledge only — nothing here knows what an assessment is, so Modules C
and D can emit their own SCOs through it. The domain mapping for Module B lives
in ``app/assessment/emit/scorm.py``.

Unlike H5P, where the LMS supplies the player, a SCORM package carries its own.
The browser-side half of that lives in ``assets/`` and ships inside every package.
"""

from .datamodel import (
    EXIT_VALUES,
    INTERACTION_RESULTS,
    INTERACTION_TYPES,
    LESSON_STATUS_WRITABLE,
    RESPONSE_MAX_CHARS,
    WEIGHTING_MAX,
    format_score,
    format_timespan,
    response_char,
)
from .manifest import (
    ADLCP_NAMESPACE,
    CP_NAMESPACE,
    MANIFEST_NAME,
    SCHEMA,
    SCHEMA_VERSION,
    build_manifest,
    sanitise_identifier,
)
from .package import LAUNCH_NAME, write_scorm

__all__ = [
    "ADLCP_NAMESPACE",
    "CP_NAMESPACE",
    "EXIT_VALUES",
    "INTERACTION_RESULTS",
    "INTERACTION_TYPES",
    "LAUNCH_NAME",
    "LESSON_STATUS_WRITABLE",
    "MANIFEST_NAME",
    "RESPONSE_MAX_CHARS",
    "SCHEMA",
    "SCHEMA_VERSION",
    "WEIGHTING_MAX",
    "build_manifest",
    "format_score",
    "format_timespan",
    "response_char",
    "sanitise_identifier",
    "write_scorm",
]
