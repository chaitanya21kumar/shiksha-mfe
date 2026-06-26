"""Unit tests for the summarisation pipeline's pure helpers.

These cover the deterministic parts — turning a parsed document into source
text, and the length guard — without involving the model at all.
"""

from datetime import datetime, timezone

from app.ingestion.schema import Block, BlockKind, Page, ParsedDocument, SourceInfo
from app.summarization.pipeline import _truncate, flatten_document


def _doc(pages: list[Page]) -> ParsedDocument:
    return ParsedDocument(
        source=SourceInfo(filename="x.pdf", format="pdf", page_count=len(pages)),
        parser="pymupdf",
        parser_version="1.24.0",
        parsed_at=datetime.now(timezone.utc),
        pages=pages,
    )


def test_flatten_renders_each_block_kind():
    page = Page(
        index=1,
        kind="page",
        blocks=[
            Block(kind=BlockKind.heading, text="Title", level=1),
            Block(kind=BlockKind.paragraph, text="Body text."),
            Block(kind=BlockKind.list, items=["one", "two"]),
            Block(kind=BlockKind.table, rows=[["a", "b"], ["c", "d"]]),
        ],
    )
    text = flatten_document(_doc([page]))

    assert "Title" in text
    assert "Body text." in text
    assert "- one" in text and "- two" in text
    assert "a | b" in text and "c | d" in text


def test_flatten_includes_speaker_notes():
    page = Page(
        index=1,
        kind="slide",
        blocks=[Block(kind=BlockKind.paragraph, text="Slide body")],
        notes="Say this part aloud",
    )
    assert "Say this part aloud" in flatten_document(_doc([page]))


def test_flatten_ignores_blocks_without_text():
    page = Page(index=1, kind="page", blocks=[Block(kind=BlockKind.image)])
    assert flatten_document(_doc([page])) == ""


def test_truncate_flags_text_over_the_limit():
    out, truncated = _truncate("x" * 100, 50)
    assert truncated is True
    assert len(out) == 50


def test_truncate_leaves_short_text_untouched():
    out, truncated = _truncate("short", 50)
    assert truncated is False
    assert out == "short"
