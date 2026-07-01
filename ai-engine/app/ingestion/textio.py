"""Shared helper for reading text-based source files (TXT, CSV, Markdown, HTML).

Keeps decoding consistent across the text parsers: prefer UTF-8 (tolerating a
BOM), and fall back to latin-1 with a recorded warning rather than failing on a
file in an unexpected encoding.
"""

from __future__ import annotations

from pathlib import Path


def _normalise(text: str) -> str:
    """Normalise line endings so paragraph/line splitting is OS-independent."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text(path: str | Path) -> tuple[str, list[str]]:
    """Return the file's text plus any non-fatal warnings about decoding."""
    data = Path(path).read_bytes()
    try:
        return _normalise(data.decode("utf-8-sig")), []
    except UnicodeDecodeError:
        return _normalise(data.decode("latin-1")), ["File was not valid UTF-8; decoded as latin-1."]
