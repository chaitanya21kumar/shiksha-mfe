"""Return a built package as a download, with its warnings in the headers.

Every packaging endpoint hands back a ZIP, so the warnings cannot ride in the
body. Putting them in a header is easy to get subtly wrong, and the two rules
below are why this lives in one place rather than once per router:

- **Header values are latin-1 encoded** by Starlette. Warnings are not latin-1:
  they contain em dashes, and on a Hindi or Marathi recording they can quote the
  model's own reply. Joining them raw raises ``UnicodeEncodeError`` *after* the
  package was successfully built — a 500 for work that actually succeeded.
- **A warning can contain a newline.** A pydantic ``ValidationError`` rendered
  into a warning does, and a raw newline in a header value is a response-splitting
  attempt: h11 rejects it and the response dies.

``json.dumps`` solves both — it escapes to ASCII by default and escapes newlines —
and it leaves the header machine-readable, which a joined string is not.
"""

from __future__ import annotations

import json
from typing import Protocol

from fastapi import Response

#: An .h5p and a SCORM course are both ZIPs. There is no registered media type for
#: either, and every consumer identifies them by extension or by manifest, so the
#: honest label is the one that describes the bytes.
ZIP_MEDIA_TYPE = "application/zip"

#: A header is not a log. Past a handful of warnings the caller should be reading
#: the JSON endpoint instead, so the header states the total and carries a prefix.
MAX_HEADER_WARNINGS = 10


class BuiltPackage(Protocol):
    """What every emitter returns: the bytes, a filename, and what to know."""

    content: bytes
    filename: str
    warnings: list[str]


def package_response(package: BuiltPackage, media_type: str = ZIP_MEDIA_TYPE) -> Response:
    """Serve a built package as an attachment, warnings included."""
    headers = {
        "Content-Disposition": f'attachment; filename="{package.filename}"',
        "X-Package-Warning-Count": str(len(package.warnings)),
    }
    if package.warnings:
        headers["X-Package-Warnings"] = json.dumps(package.warnings[:MAX_HEADER_WARNINGS])
    return Response(content=package.content, media_type=media_type, headers=headers)
