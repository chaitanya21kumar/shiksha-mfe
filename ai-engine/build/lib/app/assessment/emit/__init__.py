"""Maps the assessment contract onto the portable formats an LMS can import.

The format mechanics live in `app.packaging`; these modules only decide what an
`AssessmentSet` *means* in each target.
"""

from .errors import EmptyAssessmentError
from .h5p import H5PPackage, emit_h5p
from .scorm import ScormPackage, emit_scorm

__all__ = ["EmptyAssessmentError", "H5PPackage", "ScormPackage", "emit_h5p", "emit_scorm"]
