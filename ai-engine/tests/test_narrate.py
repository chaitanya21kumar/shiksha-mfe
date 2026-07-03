"""Tests for the narration endpoints (Module A.3).

The model gateway is mocked with an httpx ``MockTransport`` injected through the
``get_llm_client`` dependency, so these run offline and deterministically.
"""

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.ingestion.schema import Block, BlockKind, Page, ParsedDocument, SourceInfo
from app.main import app
from app.summarization.router import get_llm_client

client = TestClient(app)


def _sample_document() -> ParsedDocument:
    return ParsedDocument(
        source=SourceInfo(filename="deck.pptx", format="pptx", page_count=2, title="Photosynthesis"),
        parser="python-pptx",
        parser_version="1.0",
        parsed_at=datetime.now(timezone.utc),
        pages=[
            Page(index=1, kind="slide", blocks=[
                Block(kind=BlockKind.heading, text="Intro", level=1),
                Block(kind=BlockKind.paragraph, text="Plants make food from light."),
            ], notes="Greet the class."),
            Page(index=2, kind="slide", blocks=[
                Block(kind=BlockKind.heading, text="Stages", level=1),
                Block(kind=BlockKind.list, items=["Light reactions", "Calvin cycle"]),
            ]),
        ],
    )


def _sample_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    doc.set_metadata({"title": "Photosynthesis"})
    page = doc.new_page()
    page.insert_text((72, 72), "Photosynthesis", fontsize=20)
    page.insert_text((72, 110), "Plants turn light into chemical energy.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _narration_json(n: int) -> str:
    return json.dumps(
        {"segments": [{"index": i, "script": f"Spoken script for section {i}."} for i in range(1, n + 1)]}
    )


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    )


@pytest.fixture
def use_model():
    created: list[httpx.AsyncClient] = []

    def _install(handler):
        fake = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0))
        created.append(fake)
        app.dependency_overrides[get_llm_client] = lambda: fake
        return fake

    yield _install
    app.dependency_overrides.pop(get_llm_client, None)
    for fake in created:
        asyncio.run(fake.aclose())


def test_narrate_returns_segments(use_model):
    use_model(lambda req: _chat_response(_narration_json(2)))
    resp = client.post("/narrate", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["segments"]) == 2
    assert body["segments"][0]["source_index"] == 1
    assert body["segments"][0]["word_count"] > 0
    assert body["segments"][0]["estimated_seconds"] > 0
    assert body["total_words"] > 0
    assert body["model"]  # provenance from config
    assert body["warnings"] == []


def test_narrate_warns_when_a_section_is_missing(use_model):
    use_model(lambda req: _chat_response(_narration_json(1)))  # only 1 of 2 sections
    resp = client.post("/narrate", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["segments"]) == 1
    assert any("section 2" in w for w in body["warnings"])


def test_narrate_degrades_on_unusable_output(use_model):
    use_model(lambda req: _chat_response("this is not json"))
    resp = client.post("/narrate", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["segments"] == []
    assert any("narration" in w.lower() for w in body["warnings"])


def test_narrate_rejects_document_with_no_text(use_model):
    use_model(lambda req: _chat_response(_narration_json(1)))
    doc = _sample_document()
    doc.pages = [Page(index=1, kind="page", blocks=[Block(kind=BlockKind.image)])]

    resp = client.post("/narrate", json=doc.model_dump(mode="json"))
    assert resp.status_code == 400


def test_narrate_reports_unreachable_gateway(use_model):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    use_model(handler)
    resp = client.post("/narrate", json=_sample_document().model_dump(mode="json"))
    assert resp.status_code == 503


def test_narrate_reports_timeout(use_model):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=req)

    use_model(handler)
    resp = client.post("/narrate", json=_sample_document().model_dump(mode="json"))
    assert resp.status_code == 504


def test_narrate_file_parses_then_narrates(use_model):
    use_model(lambda req: _chat_response(_narration_json(2)))
    resp = client.post(
        "/narrate/file", files={"file": ("sample.pdf", _sample_pdf_bytes(), "application/pdf")}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["filename"] == "sample.pdf"
    assert body["segments"]


def test_narrate_file_rejects_unsupported_type(use_model):
    use_model(lambda req: _chat_response(_narration_json(1)))
    resp = client.post("/narrate/file", files={"file": ("a.zip", b"PK\x03\x04", "application/zip")})
    assert resp.status_code == 415
