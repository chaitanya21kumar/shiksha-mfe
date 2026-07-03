"""Unit tests for the narration pipeline's deterministic parts.

Section-building and the duration maths are pure and are tested directly; the
one model call is exercised through a mocked gateway so these stay offline.
"""

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.ingestion.schema import Block, BlockKind, Page, ParsedDocument, SourceInfo
from app.narration.pipeline import _build_sections, _estimated_seconds, generate_narration
from app.summarization.pipeline import EmptyDocumentError, GenerationConfig


def _doc(pages: list[Page], fmt: str = "pptx") -> ParsedDocument:
    return ParsedDocument(
        source=SourceInfo(filename="x." + fmt, format=fmt, page_count=len(pages), title="Deck"),
        parser="test",
        parser_version="1.0",
        parsed_at=datetime.now(timezone.utc),
        pages=pages,
    )


def _config() -> GenerationConfig:
    return GenerationConfig(
        base_url="https://gateway/v1",
        api_key="k",
        model="m",
        provider="test",
        temperature=0.0,
        max_source_chars=24000,
    )


def _reply(segments: list[dict]) -> httpx.Response:
    """A chat-completions response carrying the given narration segments."""
    content = json.dumps({"segments": segments})
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _run(doc: ParsedDocument, handler, config: GenerationConfig | None = None):
    """Run generate_narration against a mocked gateway and return the result."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0))
    try:
        return asyncio.run(generate_narration(client, doc, config or _config()))
    finally:
        asyncio.run(client.aclose())


def test_build_sections_one_per_slide():
    pages = [
        Page(
            index=1,
            kind="slide",
            blocks=[
                Block(kind=BlockKind.heading, text="Intro", level=1),
                Block(kind=BlockKind.paragraph, text="Welcome to the lesson."),
            ],
            notes="Say hello warmly.",
        ),
        Page(
            index=2,
            kind="slide",
            blocks=[
                Block(kind=BlockKind.heading, text="Body", level=1),
                Block(kind=BlockKind.list, items=["one", "two"]),
            ],
        ),
    ]
    sections = _build_sections(_doc(pages))
    assert len(sections) == 2
    assert sections[0].title == "Intro" and sections[0].source_index == 1
    assert "Welcome" in sections[0].text
    assert "Say hello warmly." in sections[0].text  # speaker notes folded in
    assert sections[1].title == "Body" and "- one" in sections[1].text


def test_build_sections_splits_flow_doc_by_headings():
    page = Page(
        index=1,
        kind="document",
        blocks=[
            Block(kind=BlockKind.heading, text="A", level=1),
            Block(kind=BlockKind.paragraph, text="Alpha."),
            Block(kind=BlockKind.heading, text="B", level=1),
            Block(kind=BlockKind.paragraph, text="Beta."),
        ],
    )
    sections = _build_sections(_doc([page], fmt="docx"))
    assert [s.title for s in sections] == ["A", "B"]
    assert sections[0].text == "Alpha." and sections[1].text == "Beta."


def test_build_sections_falls_back_to_one_when_no_headings():
    page = Page(
        index=1,
        kind="page",
        blocks=[
            Block(kind=BlockKind.paragraph, text="First."),
            Block(kind=BlockKind.paragraph, text="Second."),
        ],
    )
    sections = _build_sections(_doc([page], fmt="pdf"))
    assert len(sections) == 1
    assert sections[0].title is None
    assert "First." in sections[0].text and "Second." in sections[0].text


def test_build_sections_skips_empty_pages():
    pages = [Page(index=1, kind="page", blocks=[Block(kind=BlockKind.image)])]
    assert _build_sections(_doc(pages, fmt="pdf")) == []


def test_build_sections_slide_with_multiple_headings_is_one_section():
    # A slide with two same-level headings must stay a single segment (one script
    # per slide), so source_index stays 1:1 with slides.
    page = Page(
        index=1,
        kind="slide",
        blocks=[
            Block(kind=BlockKind.heading, text="Left", level=1),
            Block(kind=BlockKind.paragraph, text="Left body."),
            Block(kind=BlockKind.heading, text="Right", level=1),
            Block(kind=BlockKind.paragraph, text="Right body."),
        ],
    )
    sections = _build_sections(_doc([page]))
    assert len(sections) == 1
    assert sections[0].title == "Left"  # first heading becomes the title
    assert "Left body." in sections[0].text
    assert "Right" in sections[0].text and "Right body." in sections[0].text


def test_generate_narration_builds_segments():
    def handler(_request: httpx.Request) -> httpx.Response:
        return _reply([
            {"index": 1, "script": "One two three four five."},
            {"index": 2, "script": "Six seven eight."},
        ])

    pages = [
        Page(index=1, kind="slide", blocks=[
            Block(kind=BlockKind.heading, text="Intro", level=1),
            Block(kind=BlockKind.paragraph, text="Hello there."),
        ]),
        Page(index=2, kind="slide", blocks=[
            Block(kind=BlockKind.heading, text="Body", level=1),
            Block(kind=BlockKind.paragraph, text="More content."),
        ]),
    ]
    result = _run(_doc(pages), handler)
    assert [s.index for s in result.segments] == [1, 2]
    assert [s.source_index for s in result.segments] == [1, 2]
    assert result.segments[0].word_count == 5
    assert result.total_words == 8
    assert result.estimated_seconds == _estimated_seconds(8)
    assert result.warnings == []


def test_generate_narration_warns_on_missing_section():
    def handler(_request: httpx.Request) -> httpx.Response:
        return _reply([{"index": 1, "script": "Only the first."}])

    pages = [
        Page(index=1, kind="slide", blocks=[
            Block(kind=BlockKind.heading, text="A", level=1),
            Block(kind=BlockKind.paragraph, text="Alpha."),
        ]),
        Page(index=2, kind="slide", blocks=[
            Block(kind=BlockKind.heading, text="B", level=1),
            Block(kind=BlockKind.paragraph, text="Beta."),
        ]),
    ]
    result = _run(_doc(pages), handler)
    assert len(result.segments) == 1
    assert any("section 2" in w for w in result.warnings)


def test_generate_narration_raises_on_empty_document():
    empty = _doc([Page(index=1, kind="page", blocks=[])], fmt="pdf")
    run = generate_narration(None, empty, _config())
    with pytest.raises(EmptyDocumentError):
        asyncio.run(run)


def test_generate_narration_raises_when_size_limit_leaves_no_sections():
    # A tiny max_source_chars empties the sections; fail fast with
    # EmptyDocumentError rather than call the model with an empty prompt.
    config = GenerationConfig(
        base_url="https://gateway/v1", api_key="k", model="m",
        provider="test", temperature=0.0, max_source_chars=0,
    )
    doc = _doc([Page(index=1, kind="page", blocks=[Block(kind=BlockKind.paragraph, text="Some text.")])], fmt="pdf")
    run = generate_narration(None, doc, config)
    with pytest.raises(EmptyDocumentError):
        asyncio.run(run)
