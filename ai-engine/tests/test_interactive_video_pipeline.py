"""Tests for the interactive video assembly (Module C.3).

The model gateway is mocked, so these run offline. What matters here is *coverage
and attribution*: every chapter must get its own knowledge check, and a question
must sit at the end of the chapter its evidence actually came from — a question
about material the learner has not reached is worse than no question.

The mocked gateway returns the shape `_QuestionOut` really parses
(`options` as objects with `is_correct`, `source_section`, `evidence`). An earlier
version of this file returned a shape the pipeline rejected, so every spec under
test held zero questions and the assertions passed over empty collections. Each
test below therefore asserts on concrete, non-empty content.
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
from app.interactive_video.pipeline import VideoBuildOptions, build_interactive_video
from app.interactive_video.schema import VideoSource
from app.main import app
from app.summarization.pipeline import GenerationConfig
from app.summarization.router import get_llm_client
from app.transcription.schema import TranscriptSource

_CONFIG = GenerationConfig(
    base_url="https://llm.test/v1", api_key="k", model="llama-3.1-8b-instant",
    provider="groq", temperature=0.2, max_source_chars=24000,
)

# Each chapter's text carries a distinct sentence, so a question's evidence can
# only ground against the chapter it was really drawn from.
_CH1 = "Evaporation lifts water into the air as vapour."
_CH2 = "At night the land cools faster than the sea and the breeze reverses."
_CH3 = "Condensation forms clouds when the vapour cools around dust."


def _chaptered(count: int = 2) -> ChapteredTranscript:
    texts = [_CH1, _CH2, _CH3][:count]
    return ChapteredTranscript(
        source=TranscriptSource(filename="lecture.mp4", media_seconds=300.0),
        generator="groq",
        model="llama-3.1-8b-instant",
        generated_at=datetime.now(timezone.utc),
        language="en",
        chapters=[
            Chapter(
                index=i + 1,
                start=i * 100.0,
                end=(i + 1) * 100.0,
                title=f"Chapter {i + 1}",
                segment_indexes=[i + 1],
                text=text,
            )
            for i, text in enumerate(texts)
        ],
    )


def _mcq(prompt: str, evidence: str) -> dict:
    """One question in the shape the pipeline actually parses."""
    return {
        "source_section": 1,
        "evidence": evidence,
        "prompt": prompt,
        "options": [
            {"text": "The right one", "is_correct": True},
            {"text": "The wrong one", "is_correct": False},
        ],
    }


def _handler(per_text: dict[str, str] | None = None):
    """A gateway that answers with a question grounded in whatever chapter it sees.

    Because each chapter is now sent as its own single-page document, the handler
    can identify the chapter from the prompt body and echo that chapter's own
    sentence back as the evidence — which is exactly what grounding checks.
    """
    texts = per_text or {_CH1: "What lifts water?", _CH2: "What reverses?", _CH3: "What forms clouds?"}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        questions = [_mcq(prompt, text) for text, prompt in texts.items() if text in body]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"questions": questions})}}]}
        )

    return handler


def _options(**kwargs) -> VideoBuildOptions:
    return VideoBuildOptions(
        content_id=kwargs.get("content_id", "iv-1"),
        title=kwargs.get("title", "Coastal Weather"),
        question_types=kwargs.get("types", ["mcq"]),
        count=kwargs.get("count", 1),
        language=kwargs.get("language", "en"),
    )


def _build(handler, chaptered=None, **kwargs):
    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0)) as client:
            return await build_interactive_video(
                client,
                chaptered if chaptered is not None else _chaptered(),
                VideoSource(url="https://cdn.test/lecture.mp4"),
                _CONFIG,
                _options(**kwargs),
            )

    return asyncio.run(go())


def test_every_chapter_gets_its_own_knowledge_check():
    # The regression this file exists for: `count` bounds questions per type per
    # *document*, so generating once for the whole video put a check on chapter 1
    # and left the rest bare. One call per chapter is what makes count per-chapter.
    spec = _build(_handler(), chaptered=_chaptered(3))
    assert [check.chapter_index for check in spec.checks] == [1, 2, 3]
    assert all(len(check.questions) == 1 for check in spec.checks)


def test_a_question_is_attributed_to_the_chapter_its_evidence_came_from():
    spec = _build(_handler(), chaptered=_chaptered(3))
    placed = {
        check.chapter_index: [q.prompt for q in check.questions] for check in spec.checks
    }
    assert placed == {
        1: ["What lifts water?"],
        2: ["What reverses?"],
        3: ["What forms clouds?"],
    }


def test_question_ids_are_unique_across_the_whole_video():
    # Ids come back per chapter, so without renumbering two interactions would
    # share a subcontent id and collide in the learner's stored state.
    spec = _build(_handler(), chaptered=_chaptered(3))
    ids = [q.id for check in spec.checks for q in check.questions]
    assert ids == sorted(set(ids), key=ids.index)
    assert len(set(ids)) == 3


def test_a_chapter_with_no_question_is_reported_not_hidden():
    # Only chapter 1's evidence is ever returned, so chapters 2 and 3 are bare.
    spec = _build(_handler({_CH1: "What lifts water?"}), chaptered=_chaptered(3))
    assert [check.chapter_index for check in spec.checks] == [1]
    assert any("chapter 2" in w for w in spec.warnings)
    assert any("chapter 3" in w for w in spec.warnings)


def test_an_empty_chapter_is_skipped_with_a_warning():
    chaptered = _chaptered(2)
    chaptered.chapters[1].text = "   "
    spec = _build(_handler(), chaptered=chaptered)
    assert [check.chapter_index for check in spec.checks] == [1]
    assert any("Chapter 2 has no text" in w for w in spec.warnings)


def test_the_chapters_are_carried_through_to_the_spec_unchanged():
    spec = _build(_handler())
    assert [c.title for c in spec.chapters] == ["Chapter 1", "Chapter 2"]
    assert spec.source.filename == "lecture.mp4"


def test_chapter_warnings_are_carried_into_the_video():
    chaptered = _chaptered(2)
    chaptered.warnings.append("No title was produced for chapter 2; used a default.")
    spec = _build(_handler(), chaptered=chaptered)
    assert any("chapter 2" in w for w in spec.warnings)


def test_the_assembled_spec_packages_into_a_real_h5p_with_interactions():
    spec = _build(_handler(), chaptered=_chaptered(3))
    package = emit_interactive_video(spec)
    archive = zipfile.ZipFile(io.BytesIO(package.content))
    content = json.loads(archive.read("content/content.json"))
    assets = content["interactiveVideo"]["assets"]
    assert len(assets["bookmarks"]) == 3
    assert len(assets["interactions"]) == 3  # one per chapter, not one per video
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


def _post(**query) -> httpx.Response:
    params = "&".join(f"{k}={v}" for k, v in query.items())
    return client.post(
        f"/interactive-video?video_url=https://cdn.test/lecture.mp4&{params}",
        json=json.loads(_chaptered().model_dump_json()),
    )


def test_the_endpoint_returns_a_zip_with_the_warning_headers(use_llm):
    use_llm(_handler())
    # Only mcq: the mocked gateway answers in that shape, so nothing is dropped
    # and the warning count is a real zero rather than an artefact of the mock.
    response = _post(title="Coastal", question_types="mcq")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-package-warning-count"] == "0"
    assert zipfile.ZipFile(io.BytesIO(response.content)).namelist() == [
        "h5p.json",
        "content/content.json",
    ]


def test_warnings_are_json_encoded_so_a_non_latin1_one_cannot_500(use_llm):
    # A Hindi lecture's warnings are not latin-1 encodable, and Starlette encodes
    # header values as latin-1. Joining them raw turned a successful build into a
    # 500 after every model call had already been paid for.
    use_llm(_handler({}))  # no questions come back, so warnings are produced
    chaptered = _chaptered()
    chaptered.warnings.append("अध्याय शीर्षक नहीं बना — छोड़ा गया\nदूसरी पंक्ति")
    response = client.post(
        "/interactive-video?video_url=https://cdn.test/lecture.mp4",
        json=json.loads(chaptered.model_dump_json()),
    )
    assert response.status_code == 200
    header = response.headers["x-package-warnings"]
    assert "\n" not in header
    assert "अध्याय" in json.loads(header)[0]


def test_asking_only_for_short_answer_is_refused_rather_than_substituted(use_llm):
    use_llm(_handler())
    response = _post(question_types="short_answer")
    assert response.status_code == 400
    assert "H5P.Essay" in response.json()["detail"]


def test_a_non_http_video_url_is_rejected_by_the_endpoint(use_llm):
    use_llm(_handler())
    response = client.post(
        "/interactive-video?video_url=/local/path.mp4",
        json=json.loads(_chaptered().model_dump_json()),
    )
    assert response.status_code in (400, 422)
