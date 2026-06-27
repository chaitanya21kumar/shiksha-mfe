"""Parse a Markdown (.md) file into a `ParsedDocument`.

Markdown maps almost directly onto the block schema: ATX headings become heading
blocks, lists become list blocks, GFM tables become table blocks, fenced code
becomes a paragraph block (so it is not lost), and everything else is a
paragraph. The file is one ``document`` page. Inline markup (bold, links, …) is
flattened to plain text, which is what the downstream modules want.
"""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .schema import Block, BlockKind, Page, ParsedDocument, SourceInfo
from .textio import read_text

_PARSER = "markdown-it-py"


def _parser_version() -> str:
    try:
        return version("markdown-it-py")
    except Exception:  # pragma: no cover - metadata always present in practice
        return "unknown"


def _inline_plain(token: Token | None) -> str:
    """Flatten an inline token to plain text, dropping markup."""
    if token is None or token.type != "inline":
        return ""
    if not token.children:
        return token.content.strip()
    parts: list[str] = []
    for child in token.children:
        if child.type in ("text", "code_inline", "image"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts).strip()


def _finalise_item(parts: list[str], items: list[str]) -> None:
    text = " ".join(p for p in parts if p).strip()
    if text:
        items.append(text)


def _collect_list_items(tokens: list[Token], start: int) -> tuple[list[str], int]:
    """Collect a list's item texts; returns (items, index after the list)."""
    items: list[str] = []
    current: list[str] = []
    in_item = False
    depth = 0
    i = start
    while i < len(tokens):
        t = tokens[i].type
        if t in ("bullet_list_open", "ordered_list_open"):
            depth += 1
        elif t in ("bullet_list_close", "ordered_list_close"):
            depth -= 1
            if depth == 0:
                return items, i + 1
        elif t == "list_item_open" and depth == 1:
            current, in_item = [], True
        elif t == "list_item_close" and depth == 1:
            _finalise_item(current, items)
            in_item = False
        elif t == "inline" and in_item:
            current.append(_inline_plain(tokens[i]))
        i += 1
    return items, i


def _append_row(rows: list[list[str]], row: list[str] | None) -> None:
    if row:
        rows.append(row)


def _append_cell(row: list[str] | None, cell: str | None) -> None:
    if row is not None:
        row.append(cell or "")


def _collect_table_rows(tokens: list[Token], start: int) -> tuple[list[list[str]], int]:
    """Collect a table's rows, preserving empty cells; returns (rows, index after)."""
    rows: list[list[str]] = []
    row: list[str] | None = None
    cell: str | None = None
    i = start
    while i < len(tokens):
        t = tokens[i].type
        if t == "table_close":
            return rows, i + 1
        if t == "tr_open":
            row = []
        elif t == "tr_close":
            _append_row(rows, row)
            row = None
        elif t in ("th_open", "td_open"):
            cell = ""
        elif t in ("th_close", "td_close"):
            _append_cell(row, cell)
            cell = None
        elif t == "inline" and cell is not None:
            cell = _inline_plain(tokens[i])
        i += 1
    return rows, i


def _handle_heading(tokens: list[Token], i: int, blocks: list[Block]) -> int:
    content = _inline_plain(tokens[i + 1]) if i + 1 < len(tokens) else ""
    if content:
        tag = tokens[i].tag
        level = int(tag[1:]) if tag[1:].isdigit() else 1
        blocks.append(Block(kind=BlockKind.heading, text=content, level=level))
    return i + 3


def _handle_paragraph(tokens: list[Token], i: int, blocks: list[Block]) -> int:
    content = _inline_plain(tokens[i + 1]) if i + 1 < len(tokens) else ""
    if content:
        blocks.append(Block(kind=BlockKind.paragraph, text=content))
    return i + 3


def _handle_list(tokens: list[Token], i: int, blocks: list[Block]) -> int:
    items, nxt = _collect_list_items(tokens, i)
    if items:
        blocks.append(Block(kind=BlockKind.list, items=items))
    return nxt


def _handle_table(tokens: list[Token], i: int, blocks: list[Block]) -> int:
    rows, nxt = _collect_table_rows(tokens, i)
    if rows:
        blocks.append(Block(kind=BlockKind.table, rows=rows))
    return nxt


def _handle_code(tokens: list[Token], i: int, blocks: list[Block]) -> int:
    code = tokens[i].content.rstrip("\n")
    if code:
        blocks.append(Block(kind=BlockKind.paragraph, text=code))
    return i + 1


_BLOCK_HANDLERS = {
    "heading_open": _handle_heading,
    "paragraph_open": _handle_paragraph,
    "bullet_list_open": _handle_list,
    "ordered_list_open": _handle_list,
    "table_open": _handle_table,
    "fence": _handle_code,
    "code_block": _handle_code,
}


def parse_md(path: str | Path) -> ParsedDocument:
    """Read a Markdown file and return its structured representation."""
    text, warnings = read_text(path)
    tokens = MarkdownIt("commonmark").enable("table").parse(text)

    blocks: list[Block] = []
    i = 0
    while i < len(tokens):
        handler = _BLOCK_HANDLERS.get(tokens[i].type)
        i = handler(tokens, i, blocks) if handler else i + 1

    title = next((b.text for b in blocks if b.kind == BlockKind.heading and b.level == 1), None)
    if not blocks:
        warnings.append("The file contained no readable text.")

    return ParsedDocument(
        source=SourceInfo(filename=Path(path).name, format="md", page_count=1, title=title),
        parser=_PARSER,
        parser_version=_parser_version(),
        parsed_at=datetime.now(timezone.utc),
        pages=[Page(index=1, kind="document", blocks=blocks)],
        warnings=warnings,
    )
