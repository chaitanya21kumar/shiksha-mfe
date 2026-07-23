"""Tests for the speech-to-text client (Module C.1).

The STT gateway is mocked with an httpx ``MockTransport``, so these run offline
and deterministically — no audio model, no key. The client streams a real (tiny)
temp file, so the multipart path is exercised for real.
"""

import asyncio
from pathlib import Path

import httpx
import pytest

from app.transcription.stt_client import (
    STTBadResponse,
    STTTimeout,
    STTUnavailable,
    transcribe_audio,
)

_VERBOSE_JSON = {
    "task": "transcribe",
    "language": "english",
    "duration": 4.0,
    "text": "Hello world. This is a test.",
    "segments": [
        {"id": 0, "start": 0.0, "end": 2.0, "text": " Hello world."},
        {"id": 1, "start": 2.0, "end": 4.0, "text": " This is a test."},
    ],
}


@pytest.fixture
def audio_file(tmp_path) -> str:
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"ID3 fake-audio-bytes")
    return str(path)


def _run(handler, audio_path, **kwargs):
    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0)) as client:
            return await transcribe_audio(
                client,
                base_url="https://stt.test/v1",
                api_key="k",
                model="whisper-large-v3",
                audio_path=audio_path,
                **kwargs,
            )

    return asyncio.run(go())


def test_a_successful_transcription_returns_the_parsed_body(audio_file):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/audio/transcriptions")
        assert b"whisper-large-v3" in request.content  # multipart carries the model field
        return httpx.Response(200, json=_VERBOSE_JSON)

    body = _run(handler, audio_file)
    assert body["language"] == "english"
    assert len(body["segments"]) == 2


def test_the_language_hint_is_forwarded_when_given(audio_file):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["had_language"] = b'name="language"' in request.content
        return httpx.Response(200, json=_VERBOSE_JSON)

    _run(handler, audio_file, language="en")
    assert seen["had_language"] is True


def test_an_unreachable_gateway_maps_to_unavailable(audio_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(STTUnavailable):
        _run(handler, audio_file)


def test_a_rejected_key_maps_to_unavailable(audio_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    with pytest.raises(STTUnavailable):
        _run(handler, audio_file)


def test_a_timeout_maps_to_stt_timeout(audio_file):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(STTTimeout):
        _run(handler, audio_file)


def test_a_rate_limit_is_retried_then_succeeds(audio_file):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"})
        return httpx.Response(200, json=_VERBOSE_JSON)

    body = _run(handler, audio_file)
    assert calls["n"] == 2
    assert body["language"] == "english"


def test_a_persistent_rate_limit_gives_up_as_unavailable(audio_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"})

    with pytest.raises(STTUnavailable):
        _run(handler, audio_file)


def test_a_non_json_body_maps_to_bad_response(audio_file):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(STTBadResponse):
        _run(handler, audio_file)


def test_the_audio_bytes_are_resent_on_retry(audio_file):
    # The file is read once and its bytes reused, so a retried request still
    # carries the full audio rather than a consumed, empty handle.
    seen_sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_sizes.append(len(request.content))
        if len(seen_sizes) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={})
        return httpx.Response(200, json=_VERBOSE_JSON)

    _run(handler, audio_file)
    assert seen_sizes[0] == seen_sizes[1] and seen_sizes[0] > 0
