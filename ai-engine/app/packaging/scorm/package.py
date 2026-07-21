"""Writes the SCORM 1.2 package: a ZIP holding a manifest and a self-contained SCO.

Unlike an ``.h5p``, where the LMS supplies the player, a SCORM package carries its
own: the LMS only hands it a JavaScript API to report through. So the ZIP holds a
small web app::

    quiz.zip
    |-- imsmanifest.xml     <- ROOT, exactly this lowercase name
    |-- index.html          <- the SCO entry point; the assessment is inlined
    `-- scorm/
        |-- api.js          <- finds the LMS's API and wraps it
        |-- player.js       <- renders, grades, and reports
        `-- player.css

The ZIP conventions match ``packaging/h5p/package.py`` deliberately: no directory
entries, DEFLATE, and fixed timestamps so the same assessment emits byte-identical
bytes and tests can assert on the artifact itself.

Like the H5P layer, this module knows nothing about assessments — Modules C and D
can emit their own SCOs through it.
"""

from __future__ import annotations

import io
import zipfile

from .manifest import MANIFEST_NAME

LAUNCH_NAME = "index.html"

# The earliest timestamp a ZIP can express; any constant works, this is the
# conventional choice for reproducible archives.
_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _entry(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def write_scorm(*, manifest: bytes, files: dict[str, bytes]) -> bytes:
    """Serialise a manifest and the SCO's files into SCORM package bytes.

    ``files`` maps archive path to content; every path must also appear in the
    manifest's ``<file>`` list, which `build_manifest` takes separately so the two
    cannot drift silently — the emitter passes the same names to both.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_entry(MANIFEST_NAME), manifest)
        for name, content in files.items():
            archive.writestr(_entry(name), content)
    return buffer.getvalue()
