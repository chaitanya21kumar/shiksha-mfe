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

#: …and a count is not a size. A single warning can quote the model's own reply, so
#: ten of them can run to five figures of bytes, and a header line over roughly 8 KB
#: is refused by nginx, Apache and most proxies with a 431 or a dropped response.
#: The count header remains authoritative about how many there really were.
MAX_HEADER_WARNING_BYTES = 3000


class BuiltPackage(Protocol):
    """What every emitter returns: the bytes, a filename, and what to know.

    Declared as read-only properties rather than plain attributes. A Protocol
    attribute is invariant and must be *settable*, and every emitter here returns a
    NamedTuple, whose fields are not — so the obvious spelling describes a contract
    that none of its own implementers can satisfy.
    """

    @property
    def content(self) -> bytes: ...

    @property
    def filename(self) -> str: ...

    @property
    def warnings(self) -> list[str]: ...


def _warning_header(warnings: list[str]) -> str:
    """As many whole warnings as fit the byte budget, always valid JSON.

    Dropping whole entries rather than truncating the encoded string: a caller
    parsing a half-written JSON array gets an exception, which is a worse failure
    than being told about eight problems instead of ten.
    """
    kept: list[str] = []
    for warning in warnings[:MAX_HEADER_WARNINGS]:
        candidate = kept + [warning]
        if len(json.dumps(candidate)) > MAX_HEADER_WARNING_BYTES:
            break
        kept = candidate
    return json.dumps(kept)


def package_response(package: BuiltPackage, media_type: str = ZIP_MEDIA_TYPE) -> Response:
    """Serve a built package as an attachment, warnings included."""
    headers = {
        "Content-Disposition": f'attachment; filename="{package.filename}"',
        "X-Package-Warning-Count": str(len(package.warnings)),
    }
    if package.warnings:
        headers["X-Package-Warnings"] = _warning_header(package.warnings)
    return Response(content=package.content, media_type=media_type, headers=headers)
