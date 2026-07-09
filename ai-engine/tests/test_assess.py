"""Tests for the assessment endpoints (Module B).

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

_EVIDENCE = "Plants make food from light in the chloroplast."


def _sample_document() -> ParsedDocument:
    return ParsedDocument(
        source=SourceInfo(filename="bio.pptx", format="pptx", page_count=1, title="Photosynthesis"),
        parser="python-pptx",
        parser_version="1.0",
        parsed_at=datetime.now(timezone.utc),
        pages=[
            Page(
                index=1,
                kind="slide",
                blocks=[
                    Block(kind=BlockKind.heading, text="Photosynthesis", level=1),
                    Block(kind=BlockKind.paragraph, text=_EVIDENCE),
                ],
            )
        ],
    )


def _sample_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Photosynthesis", fontsize=20)
    page.insert_text((72, 110), _EVIDENCE, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _questions_for(user: str) -> list[dict]:
    """A valid, grounded question for whichever type the prompt asks for."""
    if "multiple-choice" in user:
        return [
            {
                "source_section": 1,
                "evidence": _EVIDENCE,
                "prompt": "Where do plants make food?",
                "options": [
                    {"text": "Chloroplast", "is_correct": True},
                    {"text": "Nucleus", "is_correct": False},
                    {"text": "Vacuole", "is_correct": False},
                ],
            }
        ]
    if "match-the-pair" in user:
        return [
            {
                "source_section": 1,
                "evidence": _EVIDENCE,
                "prompt": "Match the terms.",
                "pairs": [{"left": "Plants", "right": "Food"}, {"left": "Light", "right": "Energy"}],
            }
        ]
    return [
        {
            "source_section": 1,
            "evidence": _EVIDENCE,
            "text": "Plants make food from light in the [[1]].",
            "blanks": [{"answers": ["chloroplast"]}],
        }
    ]


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
    )


def _typed_handler(req: httpx.Request) -> httpx.Response:
    user = json.loads(req.content)["messages"][1]["content"]
    return _chat_response(json.dumps({"questions": _questions_for(user)}))


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


def test_assess_generates_every_type_by_default(use_model):
    use_model(_typed_handler)
    resp = client.post("/assess", json=_sample_document().model_dump(mode="json"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {"mcq": 1, "match": 1, "fill_blank": 1}
    assert body["max_points"] == 3.0
    assert body["assessment_id"] and body["language"] == "en"
    assert body["model"]  # provenance from config
    assert body["warnings"] == []


def test_assess_honours_question_types_filter(use_model):
    use_model(_typed_handler)
    resp = client.post(
        "/assess", params=[("question_types", "mcq")], json=_sample_document().model_dump(mode="json")
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {"mcq": 1}
    assert body["questions"][0]["choices"][0]["id"] == "q1-c1"


def test_assess_rejects_unknown_question_type(use_model):
    use_model(_typed_handler)
    resp = client.post(
        "/assess", params=[("question_types", "essay")], json=_sample_document().model_dump(mode="json")
    )
    assert resp.status_code == 422  # not one of the allowed literals


def test_assess_rejects_document_with_no_text(use_model):
    use_model(_typed_handler)
    doc = _sample_document()
    doc.pages = [Page(index=1, kind="slide", blocks=[Block(kind=BlockKind.image)])]

    resp = client.post("/assess", json=doc.model_dump(mode="json"))
    assert resp.status_code == 400


def test_assess_reports_unreachable_gateway(use_model):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=req)

    use_model(handler)
    resp = client.post(
        "/assess", params=[("question_types", "mcq")], json=_sample_document().model_dump(mode="json")
    )
    assert resp.status_code == 503


def test_assess_reports_timeout(use_model):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=req)

    use_model(handler)
    resp = client.post(
        "/assess", params=[("question_types", "mcq")], json=_sample_document().model_dump(mode="json")
    )
    assert resp.status_code == 504


def test_assess_file_parses_then_generates(use_model):
    use_model(_typed_handler)
    resp = client.post(
        "/assess/file",
        params=[("question_types", "mcq")],
        files={"file": ("sample.pdf", _sample_pdf_bytes(), "application/pdf")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["filename"] == "sample.pdf"
    assert body["questions"][0]["type"] == "mcq"


def test_assess_file_rejects_unsupported_type(use_model):
    use_model(_typed_handler)
    resp = client.post(
        "/assess/file", files={"file": ("a.zip", b"PK\x03\x04", "application/zip")}
    )
    assert resp.status_code == 415


def test_assess_dedupes_and_orders_question_types(use_model):
    use_model(_typed_handler)
    resp = client.post(
        "/assess",
        params=[("question_types", "match"), ("question_types", "mcq"), ("question_types", "mcq")],
        json=_sample_document().model_dump(mode="json"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {"match": 1, "mcq": 1}  # duplicate mcq collapsed
    assert [q["type"] for q in body["questions"]] == ["match", "mcq"]  # request order kept


@pytest.mark.parametrize("count", ["0", "21"])
def test_assess_rejects_out_of_range_count(use_model, count):
    use_model(_typed_handler)
    resp = client.post(
        "/assess",
        params=[("question_types", "mcq"), ("count", count)],
        json=_sample_document().model_dump(mode="json"),
    )
    assert resp.status_code == 422


def test_assess_echoes_language(use_model):
    use_model(_typed_handler)
    resp = client.post(
        "/assess",
        params=[("question_types", "mcq"), ("language", "hi")],
        json=_sample_document().model_dump(mode="json"),
    )
    assert resp.status_code == 200
    assert resp.json()["language"] == "hi"
