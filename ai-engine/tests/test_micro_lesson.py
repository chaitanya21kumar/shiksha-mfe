"""Tests for the micro-lesson endpoints (Module D, week 9).

Four routes, one per way of arriving at a lesson, so each is exercised end to end
through the app rather than only at the pipeline. What is worth testing at this
layer is what the pipeline tests cannot see: that each route accepts the body shape
it advertises, that the query parameters reach the builder, that the domain errors
map to the documented status codes, and that what comes back over the wire still
validates as a `MicroLesson`.

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

from app.chaptering.schema import Chapter, ChapteredTranscript
from app.ingestion.schema import Block, BlockKind, Page, ParsedDocument, SourceInfo
from app.main import app
from app.microlesson.schema import MicroLesson
from app.summarization.router import get_llm_client
from app.transcription.schema import TranscriptSource

client = TestClient(app)

BODY_A = "Water moves between the oceans, the atmosphere and the land continuously."
BODY_B = "Energy from the sun heats the ocean surface until molecules escape as gas."


def _sample_document() -> ParsedDocument:
    return ParsedDocument(
        source=SourceInfo(filename="deck.pptx", format="pptx", page_count=2, title="The Water Cycle"),
        parser="python-pptx",
        parser_version="1.0",
        parsed_at=datetime.now(timezone.utc),
        pages=[
            Page(index=1, kind="slide", blocks=[
                Block(kind=BlockKind.heading, text="Evaporation", level=1),
                Block(kind=BlockKind.paragraph, text=BODY_A),
            ]),
            Page(index=2, kind="slide", blocks=[
                Block(kind=BlockKind.heading, text="Condensation", level=1),
                Block(kind=BlockKind.paragraph, text=BODY_B),
            ]),
        ],
    )


def _sample_transcript() -> ChapteredTranscript:
    return ChapteredTranscript(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=200.0),
        generator="test",
        model="m",
        generated_at=datetime.now(timezone.utc),
        chapters=[
            Chapter(index=1, start=0.0, end=100.0, title="Evaporation", text=BODY_A),
            Chapter(index=2, start=100.0, end=200.0, title="Condensation", text=BODY_B),
        ],
    )


def _sample_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    doc.set_metadata({"title": "The Water Cycle"})
    page = doc.new_page()
    page.insert_text((72, 72), "Evaporation", fontsize=20)
    page.insert_text((72, 110), BODY_A, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _lesson_json(n: int, objectives: list[str] | None = None) -> str:
    return json.dumps({
        "objectives": objectives or ["Describe the water cycle"],
        "steps": [
            {"index": i, "title": f"Model title {i}", "bullets": [f"Point {i}"], "notes": f"Notes {i}"}
            for i in range(1, n + 1)
        ],
    })


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


# --- the four routes -------------------------------------------------------------


def test_a_document_becomes_a_lesson(use_model):
    use_model(lambda req: _chat_response(_lesson_json(2)))
    resp = client.post("/micro-lesson", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["kind"] == "document"
    assert body["step_count"] == 2
    assert [s["source_index"] for s in body["steps"]] == [1, 2]
    assert body["objectives"] == ["Describe the water cycle"]
    assert body["model"]  # provenance from config
    assert body["warnings"] == []


def test_a_file_is_parsed_then_turned_into_a_lesson(use_model):
    use_model(lambda req: _chat_response(_lesson_json(1)))
    resp = client.post(
        "/micro-lesson/file", files={"file": ("cycle.pdf", _sample_pdf_bytes(), "application/pdf")}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["kind"] == "document"
    assert body["source"]["filename"] == "cycle.pdf"
    assert body["steps"]


def test_a_file_of_an_unsupported_type_is_refused(use_model):
    use_model(lambda req: _chat_response(_lesson_json(1)))
    resp = client.post("/micro-lesson/file", files={"file": ("a.zip", b"PK\x03\x04", "application/zip")})
    assert resp.status_code == 415


def test_a_transcript_becomes_one_step_per_chapter(use_model):
    use_model(lambda req: _chat_response(_lesson_json(2)))
    resp = client.post("/micro-lesson/transcript", json=_sample_transcript().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["kind"] == "transcript"
    assert body["source"]["filename"] == "lecture.mp4"
    # The chapter titles are the author's, so they survive the model's own.
    assert [s["title"] for s in body["steps"]] == ["Evaporation", "Condensation"]


def test_pasted_text_becomes_one_step_per_block(use_model):
    use_model(lambda req: _chat_response(_lesson_json(2)))
    resp = client.post(
        "/micro-lesson/text",
        content=f"{BODY_A}\n\n{BODY_B}",
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["kind"] == "text"
    assert body["step_count"] == 2


# --- what the query parameters do ------------------------------------------------


def test_an_explicit_title_reaches_the_builder(use_model):
    use_model(lambda req: _chat_response(_lesson_json(2)))
    resp = client.post(
        "/micro-lesson",
        params={"title": "My Own Lesson"},
        json=_sample_document().model_dump(mode="json"),
    )
    assert resp.json()["title"] == "My Own Lesson"


def test_the_title_otherwise_comes_from_the_source(use_model):
    use_model(lambda req: _chat_response(_lesson_json(2)))
    resp = client.post("/micro-lesson", json=_sample_document().model_dump(mode="json"))
    assert resp.json()["title"] == "The Water Cycle"


def test_the_language_reaches_the_lesson(use_model):
    use_model(lambda req: _chat_response(_lesson_json(2)))
    resp = client.post(
        "/micro-lesson", params={"language": "hi"}, json=_sample_document().model_dump(mode="json")
    )
    assert resp.json()["language"] == "hi"


# --- what goes wrong, and what the caller is told --------------------------------


def test_a_source_with_nothing_to_teach_is_refused(use_model):
    use_model(lambda req: _chat_response(_lesson_json(1)))
    doc = _sample_document()
    doc.pages = [Page(index=1, kind="page", blocks=[Block(kind=BlockKind.image)])]

    resp = client.post("/micro-lesson", json=doc.model_dump(mode="json"))
    assert resp.status_code == 400


def test_an_unreachable_gateway_is_reported(use_model):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    use_model(handler)
    resp = client.post("/micro-lesson", json=_sample_document().model_dump(mode="json"))
    assert resp.status_code == 503


def test_a_timeout_is_reported(use_model):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=req)

    use_model(handler)
    resp = client.post("/micro-lesson", json=_sample_document().model_dump(mode="json"))
    assert resp.status_code == 504


def test_an_unusable_reply_still_returns_a_whole_lesson(use_model):
    """Degrading to the source text beats returning an error: the caller still gets
    every step, and the warnings say the words are not generated ones."""
    use_model(lambda req: _chat_response("this is not json"))
    resp = client.post("/micro-lesson", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["step_count"] == 2
    assert any("Could not generate" in w for w in body["warnings"])


def test_a_step_the_model_skipped_is_warned_about_over_the_wire(use_model):
    """The warning is the whole point of the fallback, so it has to survive
    serialisation — a silently shortened lesson is the failure being guarded."""
    use_model(lambda req: _chat_response(_lesson_json(1)))  # only 1 of 2 sections
    resp = client.post("/micro-lesson", json=_sample_document().model_dump(mode="json"))

    body = resp.json()
    assert body["step_count"] == 2
    assert any("step(s) 2" in w for w in body["warnings"])


# --- the contract, over the wire -------------------------------------------------


def test_the_response_still_validates_as_a_micro_lesson(use_model):
    """A response that cannot be read back into the contract is one no consumer can
    rely on, and JSON round-tripping is where computed fields usually break it."""
    use_model(lambda req: _chat_response(_lesson_json(2)))
    resp = client.post("/micro-lesson", json=_sample_document().model_dump(mode="json"))

    lesson = MicroLesson.model_validate(resp.json())
    assert lesson.step_count == 2
    assert [s.index for s in lesson.steps] == [1, 2]
