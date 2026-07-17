"""Writes the ``.h5p`` file itself: a ZIP holding a manifest and one content JSON.

The layout is fixed and unforgiving::

    quiz.h5p
    |-- h5p.json            <- root, exactly this lowercase name
    `-- content/
        `-- content.json    <- exactly this lowercase path

Three things about the ZIP matter more than they look:

- **No directory entries.** H5P routes a bare ``content/`` entry into its content
  branch, tests it against a whitelist anchored on file extensions, finds it has
  none, and rejects *the whole package*. ``ZipFile.writestr`` never creates
  directory entries; walking a directory tree does. Never do that here.
- **Names are case-sensitive on read** but lowercased during detection, so
  ``H5P.JSON`` fails with the unrelated-sounding "Unable to read file from the
  package".
- **Fixed timestamps.** With the deterministic subcontent ids, this makes the
  same assessment emit byte-identical bytes, which is what lets tests assert on
  the package rather than on a re-implementation of it.

This module deliberately knows nothing about assessments: it takes a manifest and
a content dict, so Module C and Module D can emit their own H5P content types
through it unchanged.
"""

from __future__ import annotations

import io
import json
import zipfile

MANIFEST_NAME = "h5p.json"
CONTENT_NAME = "content/content.json"

# The earliest timestamp a ZIP can represent. Any constant works; this one is the
# conventional choice for reproducible archives.
_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _dump(payload: dict[str, object]) -> bytes:
    """Serialise to UTF-8 JSON, leaving non-ASCII text as-is.

    ``ensure_ascii=False`` is not cosmetic: the questions may be in Hindi or
    Marathi, and escaping them to ``\\uXXXX`` would bloat the package and lose
    the readability that makes a generated artifact reviewable.
    """
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _entry(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def write_h5p(*, manifest: dict[str, object], content: dict[str, object]) -> bytes:
    """Serialise a manifest and a content payload into ``.h5p`` bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_entry(MANIFEST_NAME), _dump(manifest))
        archive.writestr(_entry(CONTENT_NAME), _dump(content))
    return buffer.getvalue()
