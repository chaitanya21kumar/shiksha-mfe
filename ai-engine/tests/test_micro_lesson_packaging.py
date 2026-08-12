"""The six packaging endpoints, exercised over HTTP.

The emitter suites already open each artefact and check its contents. What is
tested here is the layer above them, which is where Module B's teacher controls
were found unreachable: a route can accept a request, return 200, and still hand
back the wrong thing or lose a setting on the way through. So these assert on what
comes *out of the response* — the bytes, the media type, the download filename —
rather than on anything the emitter already proved.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import datetime, timezone

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.microlesson.schema import LessonStep, MicroLesson
from app.summarization.router import get_llm_client

client = TestClient(app)


def make_lesson(**kw) -> MicroLesson:
    defaults = dict(
        lesson_id="lesson-1",
        source={"kind": "text"},
        title="The Water Cycle",
        generator="test",
        model="m",
        generated_at=datetime.now(timezone.utc),
        objectives=["Explain the cycle"],
        steps=[
            LessonStep(index=1, title="Evaporation", bullets=["The sun heats the ocean"],
                       notes="The sun drives it.", source_index=1),
            LessonStep(index=2, title="Condensation", bullets=["Vapour cools"], notes="", source_index=2),
        ],
    )
    defaults.update(kw)
    return MicroLesson(**defaults)


def body(lesson: MicroLesson) -> dict:
    return lesson.model_dump(mode="json")


def _pdf_bytes() -> bytes:
    doc = pymupdf.open()
    doc.set_metadata({"title": "The Water Cycle"})
    page = doc.new_page()
    # Short lines: pymupdf's insert_text does not wrap, and a clipped line loses
    # text the lesson would otherwise have been built from.
    page.insert_text((72, 72), "Evaporation", fontsize=20)
    page.insert_text((72, 110), "The sun heats the ocean surface.", fontsize=11)
    page.insert_text((72, 130), "Water escapes into the air as vapour.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _lesson_reply(n: int = 1) -> httpx.Response:
    content = json.dumps({
        "objectives": ["Explain the cycle"],
        "steps": [{"index": i, "title": f"Step {i}", "bullets": [f"Point {i}"], "notes": f"Notes {i}"}
                  for i in range(1, n + 1)],
    })
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


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


PACKAGING_ROUTES = ["/micro-lesson/h5p", "/micro-lesson/html5", "/micro-lesson/scorm"]
FILE_ROUTES = ["/micro-lesson/h5p/file", "/micro-lesson/html5/file", "/micro-lesson/scorm/file"]


# --- every route returns the artefact it promises ---------------------------------


@pytest.mark.parametrize("route", PACKAGING_ROUTES)
def test_each_route_returns_a_downloadable_package(route):
    resp = client.post(route, json=body(make_lesson()))
    assert resp.status_code == 200, resp.text
    assert resp.content
    assert "attachment" in resp.headers["content-disposition"]


def test_the_h5p_route_returns_a_real_h5p():
    resp = client.post("/micro-lesson/h5p", json=body(make_lesson()))
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    assert "h5p.json" in archive.namelist()
    assert resp.headers["content-type"] == "application/zip"
    assert 'filename="The-Water-Cycle.h5p"' in resp.headers["content-disposition"]


def test_the_html5_route_returns_html_not_a_zip():
    """A single file is the whole point of this format. Wrapping it in an archive
    would make a teacher unzip something that did not need zipping."""
    resp = client.post("/micro-lesson/html5", json=body(make_lesson()))
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.content.startswith(b"<!DOCTYPE html>")
    assert 'filename="The-Water-Cycle.html"' in resp.headers["content-disposition"]


def test_the_scorm_route_returns_a_manifested_course():
    resp = client.post("/micro-lesson/scorm", json=body(make_lesson()))
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    assert "imsmanifest.xml" in archive.namelist()
    assert 'filename="The-Water-Cycle-scorm.zip"' in resp.headers["content-disposition"]


def test_the_three_formats_are_genuinely_different_artefacts():
    """Cheap, and it would have caught a copy-paste that wired two routes to one
    emitter — which returns 200 on all three and looks perfectly healthy."""
    payload = body(make_lesson())
    outputs = [client.post(r, json=payload).content for r in PACKAGING_ROUTES]
    assert len(set(outputs)) == 3


# --- a lesson can be reviewed, edited, then packaged ------------------------------


def test_a_lesson_edited_between_generation_and_packaging_is_honoured():
    """The reason the plain routes exist alongside the `/file` ones: a teacher fixes
    a heading, and the package must contain the fix, not the original."""
    lesson = make_lesson()
    payload = body(lesson)
    payload["steps"][0]["title"] = "Evaporation and transpiration"
    resp = client.post("/micro-lesson/html5", json=payload)
    assert "Evaporation and transpiration" in resp.content.decode()


def test_the_generated_lesson_can_be_posted_straight_back(use_model):
    """The round-trip `MicroLesson` deliberately does not forbid extra fields, so
    the output of one endpoint is valid input to the next. This is the test that
    holds that promise at the HTTP layer rather than in the contract alone."""
    use_model(lambda request: _lesson_reply(1))
    generated = client.post(
        "/micro-lesson/file", files={"file": ("l.pdf", _pdf_bytes(), "application/pdf")}
    )
    assert generated.status_code == 200
    packaged = client.post("/micro-lesson/scorm", json=generated.json())
    assert packaged.status_code == 200, packaged.text


# --- the one-call routes ----------------------------------------------------------


@pytest.mark.parametrize("route", FILE_ROUTES)
def test_each_file_route_goes_from_upload_to_package(use_model, route):
    use_model(lambda request: _lesson_reply(1))
    resp = client.post(route, files={"file": ("lesson.pdf", _pdf_bytes(), "application/pdf")})
    assert resp.status_code == 200, resp.text
    assert resp.content


@pytest.mark.parametrize("route", FILE_ROUTES)
def test_each_file_route_refuses_a_type_it_cannot_parse(use_model, route):
    use_model(lambda request: _lesson_reply(1))
    resp = client.post(route, files={"file": ("a.zip", b"PK\x03\x04", "application/zip")})
    assert resp.status_code == 415


def test_a_title_given_to_a_file_route_reaches_the_download_name(use_model):
    """It travels through the lesson and out into the Content-Disposition, which is
    three layers — and the only place a caller can see it went wrong."""
    use_model(lambda request: _lesson_reply(1))
    resp = client.post(
        "/micro-lesson/html5/file",
        params={"title": "My Own Lesson"},
        files={"file": ("lesson.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert 'filename="My-Own-Lesson.html"' in resp.headers["content-disposition"]


# --- refusals and warnings --------------------------------------------------------


@pytest.mark.parametrize("route", PACKAGING_ROUTES)
def test_a_lesson_with_nothing_to_show_is_refused_with_400(route):
    """Not a 500, and not a corrupt download that fails inside the LMS instead."""
    lesson = make_lesson(objectives=[], steps=[LessonStep(index=1, title="  ", bullets=[""], notes="")])
    resp = client.post(route, json=body(lesson))
    assert resp.status_code == 400
    assert "detail" in resp.json()


@pytest.mark.parametrize("route", PACKAGING_ROUTES)
def test_the_lessons_warnings_travel_in_the_headers(route):
    """A package is bytes, so there is nowhere else for them to go — and a caller
    who never learns a step fell back to its source text has been misled."""
    lesson = make_lesson(warnings=["The model returned nothing usable for step 2"])
    resp = client.post(route, json=body(lesson))
    assert resp.headers["x-package-warning-count"] == "1"
    assert "step 2" in resp.headers["x-package-warnings"]


@pytest.mark.parametrize("route", PACKAGING_ROUTES)
def test_a_clean_lesson_carries_no_warning_header(route):
    resp = client.post(route, json=body(make_lesson()))
    assert resp.headers["x-package-warning-count"] == "0"
    assert "x-package-warnings" not in resp.headers
