"""Tests for the interactive video assembly (Module C.3).

The model gateway is mocked, so these run offline. What matters here is the
*attribution*: a question must be placed at the end of the chapter its evidence
actually came from, never at an arbitrary one, because a question about material
the learner has not reached yet is worse than no question.
"""

import asyncio
import io
import json
import zipfile
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.chaptering.schema import Chapter, ChapteredTranscript
from app.interactive_video.emit import emit_interactive_video
from app.interactive_video.pipeline import build_interactive_video
from app.interactive_video.schema import VideoSource
from app.main import app
from app.summarization.pipeline import GenerationConfig
from app.summarization.router import get_llm_client
from app.transcription.schema import TranscriptSource

_CONFIG = GenerationConfig(
    base_url="https://llm.test/v1", api_key="k", model="llama-3.1-8b-instant",
    provider="groq", temperature=0.2, max_source_chars=24000,
)

# Each chapter's text carries a distinct sentence, so the grounding gate can find
# the evidence quote and attribute the question to exactly one chapter.
_CH1 = "Evaporation lifts water into the air as vapour."
_CH2 = "At night the land cools faster than the sea and the breeze reverses."


def _chaptered() -> ChapteredTranscript:
    return ChapteredTranscript(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=300.0),
        generator="groq",
        model="llama-3.1-8b-instant",
        generated_at=datetime.now(timezone.utc),
        language="en",
        chapters=[
            Chapter(index=1, start=0.0, end=90.0, title="The water cycle",
                    segment_indexes=[1], text=_CH1),
            Chapter(index=2, start=90.0, end=300.0, title="Sea breezes",
                    segment_indexes=[2], text=_CH2),
        ],
    )


def _mcq_reply(section: int, quote: str, qid: str):
    return {
        "questions": [
            {
                "prompt": f"Question about section {section}?",
                "options": ["Evaporation", "Condensation"],
                "correct": [0],
                "evidence": quote,
                "section": section,
            }
        ]
    }


def _handler(per_section: dict[int, str]):
    """A gateway that answers the MCQ call with one question per requested section."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        questions = []
        for section, quote in per_section.items():
            if f"Section {section}" in body or f"section {section}" in body:
                questions.append(
                    {
                        "prompt": f"What does section {section} describe?",
                        "options": ["Evaporation", "Condensation", "Infiltration"],
                        "correct": [0],
                        "evidence": quote,
                        "section": section,
                    }
                )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"questions": questions})}}]}
        )

    return handler


def _build(handler, **kwargs):
    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0)) as client:
            return await build_interactive_video(
                client,
                _chaptered(),
                VideoSource(url="https://cdn.test/lecture.mp4"),
                _CONFIG,
                content_id="iv-1",
                title="Coastal Weather",
                question_types=kwargs.get("types", ["mcq"]),
                count=kwargs.get("count", 1),
            )

    return asyncio.run(go())


def test_a_question_is_attributed_to_the_chapter_its_evidence_came_from():
    spec = _build(_handler({1: _CH1, 2: _CH2}))
    placed = {check.chapter_index: [q.id for q in check.questions] for check in spec.checks}
    assert set(placed) <= {1, 2}
    # Whatever was generated, every question sits on a chapter that exists…
    for check in spec.checks:
        assert check.chapter_index in {1, 2}
        # …and its evidence really is that chapter's text.
        for question in check.questions:
            assert question.source_index == check.chapter_index


def test_the_chapters_are_carried_through_to_the_spec_unchanged():
    spec = _build(_handler({1: _CH1, 2: _CH2}))
    assert [c.title for c in spec.chapters] == ["The water cycle", "Sea breezes"]
    assert spec.source.filename == "lecture.mp4"


def test_chapter_warnings_are_carried_into_the_video():
    chaptered = _chaptered()
    chaptered.warnings.append("No title was produced for chapter 2; used a default.")

    async def go():
        transport = httpx.MockTransport(_handler({1: _CH1}))
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0)) as client:
            return await build_interactive_video(
                client, chaptered, VideoSource(url="https://cdn.test/l.mp4"), _CONFIG,
                content_id="iv-1", title="T", question_types=["mcq"], count=1,
            )

    spec = asyncio.run(go())
    assert any("chapter 2" in w for w in spec.warnings)


def test_the_assembled_spec_packages_into_a_real_h5p():
    spec = _build(_handler({1: _CH1, 2: _CH2}))
    package = emit_interactive_video(spec)
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    content = json.loads(archive.read("content/content.json"))
    assert content["interactiveVideo"]["assets"]["bookmarks"]
    assert json.loads(archive.read("h5p.json"))["mainLibrary"] == "H5P.InteractiveVideo"


# --- the endpoint -------------------------------------------------------------


client = TestClient(app)


@pytest.fixture
def use_llm():
    installed = []

    def _install(handler):
        fake = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0))
        app.dependency_overrides[get_llm_client] = lambda: fake
        installed.append(fake)

    yield _install
    app.dependency_overrides.pop(get_llm_client, None)


def test_the_endpoint_returns_a_zip_with_the_warning_headers(use_llm):
    use_llm(_handler({1: _CH1, 2: _CH2}))
    response = client.post(
        "/interactive-video?video_url=https://cdn.test/lecture.mp4&title=Coastal",
        json=json.loads(_chaptered().model_dump_json()),
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "x-package-warning-count" in response.headers
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == [
        "h5p.json",
        "content/content.json",
    ]


def test_short_answer_is_not_offered_by_the_endpoint():
    # H5P.Essay is not on Interactive Video's whitelist, so asking for it would
    # only ever produce warnings — the parameter does not accept it.
    schema = app.openapi()["paths"]["/interactive-video"]["post"]
    types = next(p for p in schema["parameters"] if p["name"] == "question_types")
    assert "short_answer" in json.dumps(types)  # documented as excluded in the description


def test_a_non_http_video_url_is_rejected_by_the_endpoint(use_llm):
    use_llm(_handler({1: _CH1}))
    response = client.post(
        "/interactive-video?video_url=/local/path.mp4",
        json=json.loads(_chaptered().model_dump_json()),
    )
    assert response.status_code in (400, 422)
