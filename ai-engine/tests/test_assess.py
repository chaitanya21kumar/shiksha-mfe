"""Tests for the assessment endpoints (Module B).

The model gateway is mocked with an httpx ``MockTransport`` injected through the
``get_llm_client`` dependency, so these run offline and deterministically.
"""

import asyncio
import io
import json
import zipfile
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
    if "short-answer" in user:
        return [
            {
                "source_section": 1,
                "evidence": _EVIDENCE,
                "prompt": "Describe where and how plants make food.",
                "key_points": [
                    {"text": "They use light", "accepted": ["from light"]},
                    {"text": "It happens in the chloroplast", "accepted": ["in the chloroplast"]},
                ],
                # Contains both accepted phrases, so it passes the self-check.
                "model_answer": "Plants make food from light in the chloroplast.",
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
    assert body["counts"] == {"mcq": 1, "match": 1, "fill_blank": 1, "short_answer": 1}
    # The short answer is worth one mark per key point, and the fixture gives it two.
    assert body["max_points"] == pytest.approx(5.0)
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


def test_assess_honours_pass_percentage(use_model):
    use_model(_typed_handler)
    resp = client.post(
        "/assess",
        params=[("question_types", "mcq"), ("pass_percentage", "70")],
        json=_sample_document().model_dump(mode="json"),
    )
    assert resp.status_code == 200
    assert resp.json()["pass_percentage"] == 70


@pytest.mark.parametrize("value", ["-1", "101"])
def test_assess_rejects_out_of_range_pass_percentage(use_model, value):
    use_model(_typed_handler)
    resp = client.post(
        "/assess",
        params=[("question_types", "mcq"), ("pass_percentage", value)],
        json=_sample_document().model_dump(mode="json"),
    )
    assert resp.status_code == 422


# --- packaging endpoints -----------------------------------------------------


def _generated_set(use_model) -> dict:
    """Run /assess and hand its body back, the way a real caller chains the two."""
    use_model(_typed_handler)
    resp = client.post("/assess", json=_sample_document().model_dump(mode="json"))
    assert resp.status_code == 200
    return resp.json()


def _members(payload: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(payload)).namelist()


def test_assess_h5p_packages_a_generated_set(use_model):
    resp = client.post("/assess/h5p", json=_generated_set(use_model))

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["content-disposition"] == 'attachment; filename="bio.h5p"'
    assert _members(resp.content) == ["h5p.json", "content/content.json"]


def test_assess_h5p_output_names_the_libraries_the_lms_must_have(use_model):
    resp = client.post("/assess/h5p", json=_generated_set(use_model))

    manifest = json.loads(zipfile.ZipFile(io.BytesIO(resp.content)).read("h5p.json"))
    assert manifest["mainLibrary"] == "H5P.QuestionSet"
    declared = {d["machineName"] for d in manifest["preloadedDependencies"]}
    assert {"H5P.QuestionSet", "H5P.MultiChoice", "H5P.Blanks", "H5P.DragText"} <= declared


def test_assess_h5p_reports_no_warnings_when_nothing_was_dropped(use_model):
    body = _generated_set(use_model)
    # One MCQ, one match with 2 pairs, one blank: H5P scores that out of 1+2+1=4
    # while we intended 3, so the scale warning is expected and is not a drop.
    resp = client.post("/assess/h5p", json=body)
    assert resp.status_code == 200
    assert "Dropped" not in resp.headers.get("X-Package-Warnings", "")


def test_assess_h5p_surfaces_dropped_questions_in_a_header(use_model):
    body = _generated_set(use_model)
    # "m/s" cannot be expressed in H5P's blank markup at all -- no escape exists.
    for question in body["questions"]:
        if question["type"] == "fill_blank":
            question["blanks"][0]["answers"] = ["m/s"]

    resp = client.post("/assess/h5p", json=body)

    assert resp.status_code == 200
    assert int(resp.headers["X-Package-Warning-Count"]) >= 1
    warnings = json.loads(resp.headers["X-Package-Warnings"])
    assert any("Dropped" in warning for warning in warnings)
    # The header must survive latin-1 encoding, which the warning text itself
    # would not: json.dumps escapes it to ASCII.
    resp.headers["X-Package-Warnings"].encode("latin-1")


def test_assess_h5p_rejects_a_set_with_no_packagable_questions(use_model):
    body = _generated_set(use_model)
    body["questions"] = []

    resp = client.post("/assess/h5p", json=body)

    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_assess_h5p_carries_a_supplied_rubric_into_the_package(use_model):
    body = _generated_set(use_model)
    body["pass_percentage"] = 80
    body["score_bands"] = [
        {"from_percent": 0, "to_percent": 79, "feedback": "Not yet"},
        {"from_percent": 80, "to_percent": 100, "feedback": "Mastered"},
    ]

    resp = client.post("/assess/h5p", json=body)

    content = json.loads(zipfile.ZipFile(io.BytesIO(resp.content)).read("content/content.json"))
    assert content["passPercentage"] == 80
    assert [b["feedback"] for b in content["endGame"]["overallFeedback"]] == ["Not yet", "Mastered"]


def test_assess_h5p_rejects_a_rubric_with_a_hole_in_it(use_model):
    body = _generated_set(use_model)
    body["score_bands"] = [
        {"from_percent": 0, "to_percent": 40, "feedback": "low"},
        {"from_percent": 60, "to_percent": 100, "feedback": "high"},  # 41-59 unreachable
    ]

    resp = client.post("/assess/h5p", json=body)

    # H5P would import this happily and show nothing to a learner scoring 50.
    assert resp.status_code == 422


def test_assess_h5p_file_parses_generates_and_packages_in_one_call(use_model):
    use_model(_typed_handler)
    resp = client.post(
        "/assess/h5p/file",
        files={"file": ("lesson.pdf", _sample_pdf_bytes(), "application/pdf")},
    )

    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="lesson.h5p"'
    assert _members(resp.content) == ["h5p.json", "content/content.json"]


def test_assess_h5p_file_rejects_an_unsupported_type(use_model):
    use_model(_typed_handler)
    resp = client.post(
        "/assess/h5p/file",
        files={"file": ("notes.rtf", b"x", "application/rtf")},
    )
    assert resp.status_code == 415


def test_assess_scorm_packages_a_generated_set(use_model):
    resp = client.post("/assess/scorm", json=_generated_set(use_model))

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.headers["content-disposition"] == 'attachment; filename="bio-scorm.zip"'
    assert _members(resp.content) == [
        "imsmanifest.xml",
        "index.html",
        "scorm/api.js",
        "scorm/player.js",
        "scorm/player.css",
    ]


def test_assess_scorm_output_is_a_scorm_12_package_both_target_lms_will_read(use_model):
    resp = client.post("/assess/scorm", json=_generated_set(use_model))
    manifest = zipfile.ZipFile(io.BytesIO(resp.content)).read("imsmanifest.xml").decode()

    # Open edX's version sniff, and Moodle's literal scormtype lookup.
    assert "<schemaversion>1.2</schemaversion>" in manifest
    assert 'ADLCP:SCORMTYPE="SCO"' in manifest.upper()


def test_assess_scorm_rejects_a_set_with_no_questions(use_model):
    body = _generated_set(use_model)
    body["questions"] = []

    resp = client.post("/assess/scorm", json=body)

    assert resp.status_code == 400


def test_assess_scorm_file_parses_generates_and_packages_in_one_call(use_model):
    use_model(_typed_handler)
    resp = client.post(
        "/assess/scorm/file",
        files={"file": ("lesson.pdf", _sample_pdf_bytes(), "application/pdf")},
    )

    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="lesson-scorm.zip"'
    assert "imsmanifest.xml" in _members(resp.content)


def test_assess_scorm_reports_nothing_dropped_because_the_package_owns_its_player(use_model):
    # The H5P path can drop a question it cannot render. SCORM carries its own
    # player, so it never does -- only the LMS reporting can degrade.
    resp = client.post("/assess/scorm", json=_generated_set(use_model))
    assert int(resp.headers["X-Package-Warning-Count"]) == 0
