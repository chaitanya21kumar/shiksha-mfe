"""The four course endpoints, over HTTP.

The pipeline and bundle suites already prove the orchestration and the archive. What
this layer can still get wrong is the part that only shows up in a response: a route
returning the wrong media type, losing the filename, or answering 200 with a body
that does not say what happened. Module B's teacher controls were reachable in the
pipeline and unreachable over HTTP for a fortnight, which is the precedent for
testing here as well as underneath.
"""

from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.course import pipeline as P
from app.course.schema import Course, CourseSource, Stage, StageOutcome, StageReport
from app.main import app
from app.summarization.router import get_llm_client
from tests.test_course_bundle import WHEN, make_lesson

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_gateway():
    """Every route depends on an LLM client. These tests never reach a model — the
    orchestration is stubbed — but the dependency still resolves, and resolving it for
    real turns every one of them into a 503 against a live gateway."""
    fake = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    app.dependency_overrides[get_llm_client] = lambda: fake
    yield
    app.dependency_overrides.pop(get_llm_client, None)


def a_course(**kw) -> Course:
    defaults = dict(
        course_id="c-1", title="The Water Cycle", language="en",
        source=CourseSource(kind="document", filename="lesson.pdf", unit_count=1),
        generator="test", model="m", generated_at=WHEN, lesson=make_lesson(),
        stages=[StageReport(stage=Stage.LESSON, outcome=StageOutcome.PRODUCED)],
    )
    defaults.update(kw)
    return Course(**defaults)


def stub_build(monkeypatch, course: Course):
    """Replace the orchestration. These tests are about the HTTP layer; what the
    stages do is settled two suites down, and reaching a model here would make them
    slow and flaky for no extra coverage."""
    async def fake(*a, **k):
        return course
    monkeypatch.setattr("app.course.router.build_course", fake)


# --- the course as data ------------------------------------------------------------


def test_the_text_route_builds_a_course_from_pasted_notes(monkeypatch):
    stub_build(monkeypatch, a_course())
    r = client.post("/course/text", content="Some notes.", headers={"Content-Type": "text/plain"})
    assert r.status_code == 200
    assert r.json()["title"] == "The Water Cycle"


def test_the_stage_report_is_always_present_in_the_body(monkeypatch):
    """A caller must never infer success from the status code, so the thing they are
    meant to read instead has to be there every time."""
    stub_build(monkeypatch, a_course())
    body = client.post("/course/text", content="x", headers={"Content-Type": "text/plain"}).json()
    assert body["stages"]
    assert "is_complete" in body
    assert "produced" in body


def test_a_course_with_a_failed_stage_still_answers_200(monkeypatch):
    """Failing the request would throw away the stages that worked. The failure is
    reported in the body, which is the contract."""
    stub_build(monkeypatch, a_course(stages=[
        StageReport(stage=Stage.ASSESSMENT, outcome=StageOutcome.FAILED, detail="nothing groundable"),
    ]))
    r = client.post("/course/text", content="x", headers={"Content-Type": "text/plain"})
    assert r.status_code == 200
    assert r.json()["is_complete"] is False


# --- the bundle --------------------------------------------------------------------


def test_the_bundle_route_returns_a_zip_not_json():
    r = client.post("/course/bundle", json=a_course().model_dump(mode="json"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist()


def test_the_bundle_is_offered_as_a_named_download():
    r = client.post("/course/bundle", json=a_course().model_dump(mode="json"))
    assert 'filename="The-Water-Cycle-course.zip"' in r.headers["content-disposition"]


def test_a_course_can_be_edited_and_then_packaged():
    """The reason this route exists at all: a lesson a teacher has corrected is the
    one they want packaged, and a pipeline that can only package what it just
    generated cannot express that."""
    course = a_course()
    edited = course.model_dump(mode="json")
    edited["lesson"]["steps"][0]["title"] = "A Better Heading"

    r = client.post("/course/bundle", json=edited)
    lesson = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("lesson.json"))
    assert lesson["steps"][0]["title"] == "A Better Heading"


def test_what_could_not_be_produced_reaches_the_response_headers():
    course = a_course(stages=[
        StageReport(stage=Stage.ASSESSMENT, outcome=StageOutcome.FAILED, detail="nothing groundable"),
    ])
    r = client.post("/course/bundle", json=course.model_dump(mode="json"))
    assert r.headers["x-package-warning-count"] != "0"
    assert "nothing groundable" in r.headers["x-package-warnings"]


def test_a_course_object_survives_the_round_trip():
    """The course carries computed fields, so a caller echoing back what we sent must
    not be rejected for it — the rule ADR-0011 records for the lesson."""
    sent = a_course().model_dump(mode="json")
    assert "produced" in sent          # computed, and serialised
    assert "is_complete" in sent
    assert client.post("/course/bundle", json=sent).status_code == 200


# --- the surface itself -------------------------------------------------------------


def test_all_four_routes_are_published():
    """Read from the schema rather than from `app.routes`: an included router is not
    a route object, and counting those silently misses every mounted module."""
    paths = set(app.openapi()["paths"])
    assert {"/course/file", "/course/text", "/course/bundle", "/course/bundle/file"} <= paths


def test_the_defaults_are_the_fast_reliable_mix():
    """Short answer is the slowest to generate and the likeliest to find nothing
    groundable. A default should be the path that works."""
    assert "short_answer" not in P.DEFAULT_QUESTION_TYPES
    assert set(P.DEFAULT_QUESTION_TYPES) == {"mcq", "fill_blank", "match"}


# --- the upload routes, which is how a teacher actually arrives -------------------


def _pdf_bytes() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.set_metadata({"title": "The Water Cycle"})
    page = doc.new_page()
    page.insert_text((72, 72), "Evaporation", fontsize=20)
    page.insert_text((72, 110), "The sun heats the ocean surface.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _upload():
    return {"file": ("lesson.pdf", _pdf_bytes(), "application/pdf")}


def test_an_upload_is_parsed_and_built_into_a_course(monkeypatch):
    """The parse happens in the route, before the orchestration it stubs out, so this
    is the only place the upload path is exercised at all."""
    stub_build(monkeypatch, a_course())
    r = client.post("/course/file", files=_upload())
    assert r.status_code == 200
    assert r.json()["stages"]


def test_an_upload_can_come_back_as_the_finished_archive(monkeypatch):
    """The one-call publish: a file goes up, a course pack comes down."""
    stub_build(monkeypatch, a_course())
    r = client.post("/course/bundle/file", files=_upload())
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "lesson/lesson.h5p" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()


def test_an_unsupported_upload_is_refused_before_any_generation(monkeypatch):
    """Rejecting the file type at the door is what keeps a bad upload from costing a
    model call — and the refusal has to survive being wrapped by this route."""
    stub_build(monkeypatch, a_course())
    r = client.post("/course/file", files={"file": ("notes.xyz", b"nope", "application/x-thing")})
    assert r.status_code == 415
