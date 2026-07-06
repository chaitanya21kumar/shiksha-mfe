"""Milestone 1 integration test: parse -> summarise -> narrate, end to end.

Proves the three Module A stages compose over a single real document: a PDF is
ingested into the structured contract, then that same `ParsedDocument` is both
summarised and narrated. The model gateway is mocked with one handler that
answers whichever generation it is asked for, so the test stays offline.
"""

import asyncio
import json

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.summarization.router import get_llm_client

client = TestClient(app)


def _lesson_pdf() -> bytes:
    doc = pymupdf.open()
    doc.set_metadata({"title": "Photosynthesis", "author": "Test"})
    page = doc.new_page()
    page.insert_text((72, 72), "Photosynthesis", fontsize=20)
    page.insert_text((72, 110), "Plants convert light into chemical energy.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"index": 0, "message": {"content": content}}]})


def _handler(request: httpx.Request) -> httpx.Response:
    """One fake gateway that answers whichever generation is being requested."""
    prompt = json.loads(request.content)["messages"][1]["content"].lower()
    if "narration" in prompt or "spoken" in prompt:
        return _chat_response(json.dumps({"segments": [{"index": 1, "script": "Plants turn light into energy."}]}))
    if "glossary" in prompt:
        return _chat_response(json.dumps({"glossary": [{"term": "chlorophyll", "definition": "a pigment"}]}))
    if "outline" in prompt:
        return _chat_response(json.dumps({"outline": [{"title": "Overview", "points": ["Light"]}]}))
    return _chat_response(json.dumps({"summary": "A lesson on photosynthesis.", "key_takeaways": ["Light to energy"]}))


@pytest.fixture
def gateway():
    fake = httpx.AsyncClient(transport=httpx.MockTransport(_handler), timeout=httpx.Timeout(5.0))
    app.dependency_overrides[get_llm_client] = lambda: fake
    yield
    app.dependency_overrides.pop(get_llm_client, None)
    asyncio.run(fake.aclose())


def test_m1_parse_summarise_narrate_end_to_end(gateway):
    # 1) Ingest a real PDF into the structured contract.
    ingest = client.post("/ingest", files={"file": ("lesson.pdf", _lesson_pdf(), "application/pdf")})
    assert ingest.status_code == 200
    document = ingest.json()
    assert document["source"]["format"] == "pdf"
    assert document["pages"]

    # 2) Summarise the same parsed document.
    summ = client.post("/summarize", json=document)
    assert summ.status_code == 200
    assert summ.json()["summary"]

    # 3) Narrate the same parsed document.
    narr = client.post("/narrate", json=document)
    assert narr.status_code == 200
    narration = narr.json()
    assert narration["segments"] and narration["segments"][0]["script"]

    # The three stages agree on the same source document.
    assert summ.json()["source"]["filename"] == "lesson.pdf"
    assert narration["source"]["filename"] == "lesson.pdf"
