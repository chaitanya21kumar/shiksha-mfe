"""Parse a plain-text (.txt) file into a `ParsedDocument`.

Plain text has no markup, so structure is inferred lightly: blank lines separate
paragraphs, and a run of lines that all start with a bullet or number marker is
treated as a list. Everything else is a paragraph. The whole file is one
``document`` page, since .txt has no native pagination.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .schema import Block, BlockKind, Page, ParsedDocument, SourceInfo
from .textio import read_text

_PARSER = "text"
_PARSER_VERSION = "1.0"
_BULLET_PREFIXES = ("•", "‣", "·", "-", "–", "*")


def _is_list_marker(line: str) -> bool:
    if line[:1] in _BULLET_PREFIXES:
        return True
    first = line.split(" ", 1)[0].rstrip(".)")
    return first.isdigit()


def _strip_marker(line: str) -> str:
    if line[:1] in _BULLET_PREFIXES:
        return line[1:].strip()
    head, _, rest = line.partition(" ")
    return rest.strip() if head.rstrip(".)").isdigit() else line


def _looks_like_list(lines: list[str]) -> bool:
    return len(lines) >= 2 and all(_is_list_marker(ln) for ln in lines)


def _chunk_to_block(lines: list[str]) -> Block:
    if _looks_like_list(lines):
        items = [_strip_marker(ln) for ln in lines]
        return Block(kind=BlockKind.list, items=[it for it in items if it])
    return Block(kind=BlockKind.paragraph, text=" ".join(lines))


def parse_txt(path: str | Path) -> ParsedDocument:
    """Read a .txt file and return its structured representation."""
    text, warnings = read_text(path)
    blocks: list[Block] = []
    for chunk in re.split(r"\n[ \t]*\n", text.strip()):
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if lines:
            blocks.append(_chunk_to_block(lines))
    if not blocks:
        warnings.append("The file contained no readable text.")

    return ParsedDocument(
        source=SourceInfo(filename=Path(path).name, format="txt", page_count=1),
        parser=_PARSER,
        parser_version=_PARSER_VERSION,
        parsed_at=datetime.now(timezone.utc),
        pages=[Page(index=1, kind="document", blocks=blocks)],
        warnings=warnings,
    )
