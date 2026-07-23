"""Tests for the transcription endpoint (Module C.1).

The STT gateway is mocked with an httpx ``MockTransport`` injected through the
``get_stt_client`` dependency, so these run offline — no audio model, no key. The
upload path (streaming, extension and size guards) and the three output formats
are exercised end to end through the real FastAPI app.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.transcription.router import get_stt_client

client = TestClient(app)

_VERBOSE_JSON = {
    "language": "english",
    "duration": 4.0,
    "text": "Hello world. This is a test.",
    "segments": [
        {"start": 0.0, "end": 2.0, "text": " Hello world."},
        {"start": 2.0, "end": 4.0, "text": " This is a test."},
    ],
}


@pytest.fixture
def use_stt():
    """Install a fake STT gateway backed by the given request handler."""
    installed = []

    def _install(handler):
        fake = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0))
        app.dependency_overrides[get_stt_client] = lambda: fake
        installed.append(fake)

    yield _install
    app.dependency_overrides.pop(get_stt_client, None)


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_VERBOSE_JSON)


def _upload(name="clip.mp3", data=b"ID3 fake-audio"):
    return {"file": (name, data, "audio/mpeg")}


def test_json_is_the_default_and_carries_timed_segments(use_stt):
    use_stt(_ok_handler)
    resp = client.post("/transcribe", files=_upload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["filename"] == "clip.mp3"
    assert [s["text"] for s in body["segments"]] == ["Hello world.", "This is a test."]
    assert body["language"] == "en"  # normalised from Whisper's "english"


def test_vtt_format_returns_a_webvtt_file(use_stt):
    use_stt(_ok_handler)
    resp = client.post("/transcribe?format=vtt", files=_upload())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/vtt")
    assert resp.text.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:02.000" in resp.text


def test_srt_format_returns_numbered_cues(use_stt):
    use_stt(_ok_handler)
    resp = client.post("/transcribe?format=srt", files=_upload())
    assert resp.status_code == 200
    assert resp.text.startswith("1\n")
    assert "00:00:02,000 --> 00:00:04,000" in resp.text


def test_an_unsupported_extension_is_415(use_stt):
    use_stt(_ok_handler)
    resp = client.post("/transcribe", files=_upload(name="notes.pdf", data=b"%PDF-1.4"))
    assert resp.status_code == 415


def test_a_video_container_is_accepted(use_stt):
    use_stt(_ok_handler)
    resp = client.post("/transcribe", files=_upload(name="lecture.mp4", data=b"\x00\x00\x00 ftyp"))
    assert resp.status_code == 200


def test_an_oversized_upload_is_413(use_stt, monkeypatch):
    use_stt(_ok_handler)
    monkeypatch.setattr(settings, "max_audio_bytes", 8)
    resp = client.post("/transcribe", files=_upload(data=b"way past eight bytes"))
    assert resp.status_code == 413


def test_an_oversized_upload_is_rejected_up_front_by_declared_size(use_stt, monkeypatch):
    # A declared Content-Length over the ceiling is refused before the body is
    # read, so a large upload never lands on disk at all.
    reached_gateway = {"hit": False}

    def handler(request: httpx.Request) -> httpx.Response:
        reached_gateway["hit"] = True
        return httpx.Response(200, json=_VERBOSE_JSON)

    use_stt(handler)
    monkeypatch.setattr(settings, "max_audio_bytes", 8)
    resp = client.post("/transcribe", files=_upload(data=b"way past eight bytes"))
    assert resp.status_code == 413
    assert reached_gateway["hit"] is False  # never spooled, never transcribed


def test_an_unreachable_gateway_is_503(use_stt):
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    use_stt(unreachable)
    resp = client.post("/transcribe", files=_upload())
    assert resp.status_code == 503


def test_a_gateway_timeout_is_504(use_stt):
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    use_stt(slow)
    resp = client.post("/transcribe", files=_upload())
    assert resp.status_code == 504
