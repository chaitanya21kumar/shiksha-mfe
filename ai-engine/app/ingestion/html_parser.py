"""Parse an HTML (.html / .htm) file into a `ParsedDocument`.

The DOM is walked in document order: headings, paragraphs, lists, tables and
images become the matching blocks, and unknown wrapper elements (div, section,
article, …) are descended into. Script, style and head content is dropped first.
The page is one ``document``; the HTML title, if any, becomes the source title.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from bs4.element import Tag

from .schema import Block, BlockKind, ImageRef, Page, ParsedDocument, SourceInfo
from .textio import read_text

_PARSER = "beautifulsoup4"
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _parser_version() -> str:
    import bs4

    return getattr(bs4, "__version__", "unknown")


def _dim(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _image_block(img: Tag, index: int) -> Block:
    return Block(
        kind=BlockKind.image,
        image=ImageRef(
            id=f"img{index}",
            caption=(img.get("alt") or None),
            width=_dim(img.get("width")),
            height=_dim(img.get("height")),
        ),
    )


def _table_rows(table: Tag) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    return rows


def _emit_heading(el: Tag, blocks: list[Block], counter: list[int]) -> None:
    text = el.get_text(" ", strip=True)
    if text:
        blocks.append(Block(kind=BlockKind.heading, text=text, level=int(el.name[1])))


def _emit_paragraph(el: Tag, blocks: list[Block], counter: list[int]) -> None:
    text = el.get_text(" ", strip=True)
    if text:
        blocks.append(Block(kind=BlockKind.paragraph, text=text))
    for img in el.find_all("img"):
        counter[0] += 1
        blocks.append(_image_block(img, counter[0]))


def _emit_list(el: Tag, blocks: list[Block], counter: list[int]) -> None:
    items = [li.get_text(" ", strip=True) for li in el.find_all("li", recursive=False)]
    items = [it for it in items if it]
    if items:
        blocks.append(Block(kind=BlockKind.list, items=items))


def _emit_table(el: Tag, blocks: list[Block], counter: list[int]) -> None:
    rows = _table_rows(el)
    if rows:
        blocks.append(Block(kind=BlockKind.table, rows=rows))


def _emit_pre(el: Tag, blocks: list[Block], counter: list[int]) -> None:
    text = el.get_text("\n", strip=True)
    if text:
        blocks.append(Block(kind=BlockKind.paragraph, text=text))


def _emit_blockquote(el: Tag, blocks: list[Block], counter: list[int]) -> None:
    text = el.get_text(" ", strip=True)
    if text:
        blocks.append(Block(kind=BlockKind.paragraph, text=text))


def _emit_image(el: Tag, blocks: list[Block], counter: list[int]) -> None:
    counter[0] += 1
    blocks.append(_image_block(el, counter[0]))


_HANDLERS = {
    "p": _emit_paragraph,
    "ul": _emit_list,
    "ol": _emit_list,
    "table": _emit_table,
    "pre": _emit_pre,
    "blockquote": _emit_blockquote,
    "img": _emit_image,
}


def _walk(node: Tag, blocks: list[Block], counter: list[int]) -> None:
    for child in node.children:
        if not isinstance(child, Tag):
            continue
        name = (child.name or "").lower()
        if name in _HEADINGS:
            _emit_heading(child, blocks, counter)
        elif name in _HANDLERS:
            _HANDLERS[name](child, blocks, counter)
        else:
            _walk(child, blocks, counter)


def parse_html(path: str | Path) -> ParsedDocument:
    """Read an HTML file and return its structured representation."""
    text, warnings = read_text(path)
    soup = BeautifulSoup(text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else None
    for tag in soup(["script", "style", "noscript", "head"]):
        tag.decompose()

    blocks: list[Block] = []
    _walk(soup.body or soup, blocks, [0])
    if not blocks:
        warnings.append("The file contained no readable text.")

    return ParsedDocument(
        source=SourceInfo(filename=Path(path).name, format="html", page_count=1, title=title or None),
        parser=_PARSER,
        parser_version=_parser_version(),
        parsed_at=datetime.now(timezone.utc),
        pages=[Page(index=1, kind="document", blocks=blocks)],
        warnings=warnings,
    )
