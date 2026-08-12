"""Turning a `MicroLesson` into the three artefacts issue #7 asks for.

One lesson, three targets, and the split between them is deliberate:

| target      | what it is for                                            |
|-------------|-----------------------------------------------------------|
| H5P         | what a Moodle or Sunbird teacher expects; the LMS renders it |
| HTML5       | needs no LMS at all — a folder that opens in any browser   |
| SCORM 1.2   | the only one that reports progress back to a gradebook     |

The domain mapping lives here; the format mechanics (versions, manifests, ZIPs)
stay in `app.packaging`, exactly as Module B and Module C use them.
"""

from .errors import EmptyLessonError
from .h5p import emit_h5p
from .html5 import Html5Package, emit_html5
from .scorm import ScormPackage, emit_scorm

__all__ = [
    "EmptyLessonError",
    "Html5Package",
    "ScormPackage",
    "emit_h5p",
    "emit_html5",
    "emit_scorm",
]
