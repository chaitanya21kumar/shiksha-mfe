"""Unit tests for the narration pipeline's deterministic parts.

Section-building and the duration maths are pure and are tested directly; the
one model call is exercised with a stubbed ``chat_json`` so these stay offline.
"""

import asyncio
from datetime import datetime, timezone

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


def test_generate_narration_builds_segments(monkeypatch):
    import app.narration.pipeline as pipeline

    async def fake_chat_json(_client, **_kwargs):
        return {
            "segments": [
                {"index": 1, "script": "One two three four five."},
                {"index": 2, "script": "Six seven eight."},
            ]
        }

    monkeypatch.setattr(pipeline, "chat_json", fake_chat_json)
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
    result = asyncio.run(generate_narration(None, _doc(pages), _config()))
    assert [s.index for s in result.segments] == [1, 2]
    assert [s.source_index for s in result.segments] == [1, 2]
    assert result.segments[0].word_count == 5
    assert result.total_words == 8
    assert result.estimated_seconds == _estimated_seconds(8)
    assert result.warnings == []


def test_generate_narration_warns_on_missing_section(monkeypatch):
    import app.narration.pipeline as pipeline

    async def fake_chat_json(_client, **_kwargs):
        return {"segments": [{"index": 1, "script": "Only the first."}]}

    monkeypatch.setattr(pipeline, "chat_json", fake_chat_json)
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
    result = asyncio.run(generate_narration(None, _doc(pages), _config()))
    assert len(result.segments) == 1
    assert any("section 2" in w for w in result.warnings)


def test_generate_narration_raises_on_empty_document():
    empty = _doc([Page(index=1, kind="page", blocks=[])], fmt="pdf")
    with pytest.raises(EmptyDocumentError):
        asyncio.run(generate_narration(None, empty, _config()))
