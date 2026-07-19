"""Parse a CSV file into a `ParsedDocument`.

A CSV is tabular by nature, so it maps to a single table block on one ``sheet``
page. The delimiter is sniffed (comma, semicolon, tab, …) with a comma fallback,
and very large files are capped to keep memory bounded — recording a warning if
rows were dropped.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from .schema import Block, BlockKind, Page, ParsedDocument, SourceInfo
from .textio import read_text

_PARSER = "csv"
_PARSER_VERSION = "1.0"
_MAX_ROWS = 1000


def _detect_dialect(sample: str) -> type[csv.Dialect] | csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel  # comma-separated default


def parse_csv(path: str | Path) -> ParsedDocument:
    """Read a CSV file and return its structured representation."""
    text, warnings = read_text(path)
    dialect = _detect_dialect(text[:4096])

    rows: list[list[str]] = []
    truncated = False
    for row in csv.reader(StringIO(text), dialect):
        if len(rows) >= _MAX_ROWS:
            truncated = True
            break
        rows.append([cell.strip() for cell in row])

    if truncated:
        warnings.append(f"CSV had more than {_MAX_ROWS} rows; kept the first {_MAX_ROWS}.")
    blocks = [Block(kind=BlockKind.table, rows=rows)] if rows else []
    if not rows:
        warnings.append("The CSV contained no rows.")

    return ParsedDocument(
        source=SourceInfo(filename=Path(path).name, format="csv", page_count=1),
        parser=_PARSER,
        parser_version=_PARSER_VERSION,
        parsed_at=datetime.now(timezone.utc),
        pages=[Page(index=1, kind="sheet", blocks=blocks)],
        warnings=warnings,
    )
