"""Tests for the summarisation layer (Module A.2).

The model gateway is mocked with an httpx ``MockTransport`` injected through the
``get_llm_client`` dependency, so these run offline and deterministically — no
model needed. The mock speaks the OpenAI chat-completions shape and inspects the
prompt to decide which section is being asked for, which lets one handler serve
all three generations and lets a test fail just one of them.
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
        source=SourceInfo(
            filename="lesson.pdf", format="pdf", page_count=1, title="Photosynthesis"
        ),
        parser="pymupdf",
        parser_version="1.24.0",
        parsed_at=datetime.now(timezone.utc),
        pages=[
            Page(
                index=1,
                kind="page",
                blocks=[
                    Block(kind=BlockKind.heading, text="Photosynthesis", level=1),
                    Block(kind=BlockKind.paragraph, text="Plants turn light into chemical energy."),
                    Block(kind=BlockKind.list, items=["Light reactions", "Calvin cycle"]),
                ],
            )
        ],
    )


def _sample_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    doc.set_metadata({"title": "Photosynthesis", "author": "Test"})
    page = doc.new_page()
    page.insert_text((72, 72), "Photosynthesis", fontsize=20)
    page.insert_text((72, 110), "Plants turn light into chemical energy.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


_SECTION_CONTENT = {
    "summary": {
        "summary": "A short lesson on photosynthesis.",
        "key_takeaways": ["Plants use light", "It happens in two stages"],
    },
    "glossary": {
        "glossary": [{"term": "Calvin cycle", "definition": "The light-independent reactions."}]
    },
    "outline": {"outline": [{"title": "Overview", "points": ["What it is", "Why it matters"]}]},
}


def _section_of(request: httpx.Request) -> str:
    """Identify the requested section from the user prompt."""
    prompt = json.loads(request.content)["messages"][1]["content"].lower()
    if "glossary" in prompt:
        return "glossary"
    if "outline" in prompt:
        return "outline"
    return "summary"


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    )


def _happy_handler(request: httpx.Request) -> httpx.Response:
    return _chat_response(json.dumps(_SECTION_CONTENT[_section_of(request)]))


@pytest.fixture
def use_model():
    """Install a fake model gateway backed by the given request handler."""
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


def test_summarize_returns_all_sections(use_model):
    use_model(_happy_handler)
    resp = client.post("/summarize", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]
    assert len(body["key_takeaways"]) == 2
    assert body["glossary"][0]["term"] == "Calvin cycle"
    assert body["outline"][0]["title"] == "Overview"
    assert body["generator"]  # provenance: provider label from config
    assert body["model"]  # provenance: model name from config
    assert body["source"]["filename"] == "lesson.pdf"
    assert body["warnings"] == []


def test_summarize_rejects_document_with_no_text(use_model):
    use_model(_happy_handler)
    doc = _sample_document()
    doc.pages = [Page(index=1, kind="page", blocks=[Block(kind=BlockKind.image)])]

    resp = client.post("/summarize", json=doc.model_dump(mode="json"))
    assert resp.status_code == 400
    assert "no extractable text" in resp.json()["detail"].lower()


def test_summarize_reports_unreachable_gateway(use_model):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    use_model(handler)
    resp = client.post("/summarize", json=_sample_document().model_dump(mode="json"))
    assert resp.status_code == 503


def test_summarize_treats_connect_timeout_as_unavailable(use_model):
    # A connect timeout means we never reached the gateway, so it is unavailable
    # (503), not a generation timeout (504).
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    use_model(handler)
    resp = client.post("/summarize", json=_sample_document().model_dump(mode="json"))
    assert resp.status_code == 503


def _no_sleep(monkeypatch):
    """Make the client's retry backoff instant so rate-limit tests stay fast."""
    import app.summarization.llm_client as llm_client

    async def _instant(_seconds: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(llm_client, "_sleep", _instant)


def test_summarize_treats_persistent_rate_limit_as_unavailable(use_model, monkeypatch):
    # If the gateway keeps returning 429 after retries, surface it as 503, not 500.
    _no_sleep(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limit reached"})

    use_model(handler)
    resp = client.post("/summarize", json=_sample_document().model_dump(mode="json"))
    assert resp.status_code == 503


def test_summarize_retries_after_a_rate_limit_then_succeeds(use_model, monkeypatch):
    # A transient 429 on the first call should be retried and then succeed.
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limit reached"})
        return _chat_response(json.dumps(_SECTION_CONTENT[_section_of(request)]))

    use_model(handler)
    resp = client.post("/summarize", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]
    assert body["warnings"] == []


def test_summarize_reports_timeout(use_model):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    use_model(handler)
    resp = client.post("/summarize", json=_sample_document().model_dump(mode="json"))
    assert resp.status_code == 504


def test_summarize_degrades_when_one_section_is_unusable(use_model):
    def handler(request: httpx.Request) -> httpx.Response:
        section = _section_of(request)
        if section == "glossary":
            return _chat_response("this is not json")
        return _chat_response(json.dumps(_SECTION_CONTENT[section]))

    use_model(handler)
    resp = client.post("/summarize", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]  # the good sections still came through
    assert body["outline"]
    assert body["glossary"] == []  # the bad one degraded to empty
    assert any("glossary" in warning for warning in body["warnings"])


def test_summarize_coerces_list_summary_to_string(use_model):
    # Some models return "summary" as a list of sentences; it should be joined
    # into a string rather than lost to a validation error.
    def handler(request: httpx.Request) -> httpx.Response:
        section = _section_of(request)
        if section == "summary":
            return _chat_response(
                json.dumps({"summary": ["First sentence.", "Second sentence."], "key_takeaways": ["a"]})
            )
        return _chat_response(json.dumps(_SECTION_CONTENT[section]))

    use_model(handler)
    resp = client.post("/summarize", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == "First sentence. Second sentence."
    assert body["warnings"] == []


def test_summarize_handles_malformed_choices(use_model):
    # A gateway reply whose "choices" is not a well-formed list must degrade to a
    # warning, never crash with an AttributeError/TypeError.
    def handler(request: httpx.Request) -> httpx.Response:
        section = _section_of(request)
        if section == "summary":
            return httpx.Response(200, json={"choices": "not-a-list"})
        return _chat_response(json.dumps(_SECTION_CONTENT[section]))

    use_model(handler)
    resp = client.post("/summarize", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"] == ""
    assert body["glossary"]  # the well-formed sections still came through
    assert any("summary" in warning for warning in body["warnings"])


def test_summarize_file_parses_then_enriches(use_model):
    use_model(_happy_handler)
    resp = client.post(
        "/summarize/file",
        files={"file": ("sample.pdf", _sample_pdf_bytes(), "application/pdf")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["filename"] == "sample.pdf"
    assert body["key_takeaways"]


def test_summarize_file_rejects_unsupported_type(use_model):
    use_model(_happy_handler)
    resp = client.post(
        "/summarize/file", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert resp.status_code == 415
