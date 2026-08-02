"""Checks that run over generated artefacts before they are packaged or published.

Schema validation says the shape is right. This says the content is plausible —
a distinct question, and the one the mentors asked for after the midpoint review.

The gateway reports; it does not rewrite and it does not block. Spelling is a
quality signal, not a correctness one, and grounding — which *is* a correctness
guarantee — is enforced separately and already refuses to ship what it cannot
verify.
"""

from .artefacts import check_assessment, check_chapters, check_insights, check_narration
from .prose import ProseChecker, supported_languages
from .schema import Severity, Status, ValidationIssue, ValidationReport

__all__ = [
    "ProseChecker",
    "Severity",
    "Status",
    "ValidationIssue",
    "ValidationReport",
    "check_assessment",
    "check_chapters",
    "check_insights",
    "check_narration",
    "supported_languages",
]
