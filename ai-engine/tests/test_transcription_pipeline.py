"""Tests for the transcription pipeline (Module C.1) — the provider-reply → Transcript mapping.

The STT client is mocked with an httpx ``MockTransport``, so these run offline.
The focus is the mapping and its edge cases: timing, warnings, provenance,
language, and malformed segments.
"""

import asyncio

import httpx
import pytest

from app.transcription.pipeline import TranscriptionConfig, transcribe

_CONFIG = TranscriptionConfig(
    base_url="https://stt.test/v1",
    api_key="k",
    model="whisper-large-v3",
    provider="groq",
)


@pytest.fixture
def audio_file(tmp_path) -> str:
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"ID3 fake-audio-bytes")
    return str(path)


def _transcribe(handler, audio_path, config=_CONFIG):
    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0)) as client:
            return await transcribe(client, audio_path, "clip.mp3", config)

    return asyncio.run(go())


def _reply(body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return handler


def test_segments_and_text_are_mapped_with_provenance(audio_file):
    body = {
        "language": "english",
        "duration": 4.0,
        "text": "Hello world. This is a test.",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": " Hello world."},
            {"start": 2.0, "end": 4.0, "text": " This is a test."},
        ],
    }
    transcript = _transcribe(_reply(body), audio_file)
    assert [s.text for s in transcript.segments] == ["Hello world.", "This is a test."]
    assert [s.index for s in transcript.segments] == [1, 2]
    assert transcript.full_text == "Hello world. This is a test."
    assert transcript.language == "en"  # normalised from Whisper's "english"
    assert transcript.source.media_seconds == 4.0
    assert transcript.generator == "groq"
    assert transcript.model == "whisper-large-v3"
    assert transcript.generated_at is not None


def test_no_speech_yields_an_empty_transcript_with_a_warning(audio_file):
    transcript = _transcribe(_reply({"text": "", "segments": []}), audio_file)
    assert transcript.segments == []
    assert transcript.full_text == ""
    assert any("no speech" in w.lower() for w in transcript.warnings)


def test_full_text_falls_back_to_joined_segments_when_absent(audio_file):
    body = {"segments": [{"start": 0.0, "end": 1.0, "text": "one"}, {"start": 1.0, "end": 2.0, "text": "two"}]}
    transcript = _transcribe(_reply(body), audio_file)
    assert transcript.full_text == "one two"


def test_a_segment_without_usable_timing_is_skipped_with_a_warning(audio_file):
    body = {
        "text": "kept.",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "kept."},
            {"start": None, "end": 2.0, "text": "no start"},
            {"start": 2.0, "end": 3.0, "text": "   "},  # blank after trim
        ],
    }
    transcript = _transcribe(_reply(body), audio_file)
    assert [s.text for s in transcript.segments] == ["kept."]


def test_an_end_before_start_is_clamped_not_dropped(audio_file):
    body = {"segments": [{"start": 3.0, "end": 1.0, "text": "reversed"}]}
    transcript = _transcribe(_reply(body), audio_file)
    assert len(transcript.segments) == 1
    assert transcript.segments[0].end == transcript.segments[0].start == 3.0


def test_all_segments_unusable_records_a_warning(audio_file):
    body = {"text": "flat text only", "segments": [{"text": "no timing"}]}
    transcript = _transcribe(_reply(body), audio_file)
    assert transcript.segments == []
    assert any("no usable timed segments" in w.lower() for w in transcript.warnings)
    # full_text still comes through, so the result is not empty.
    assert transcript.full_text == "flat text only"


def test_detected_language_is_normalised_to_iso639_1(audio_file):
    # Whisper reports the full name; the contract promises the code.
    with_lang = _transcribe(_reply({"language": "hindi", "segments": []}), audio_file)
    assert with_lang.language == "hi"

    config = TranscriptionConfig(
        base_url="https://stt.test/v1", api_key="k", model="whisper-large-v3", provider="groq", language="en"
    )
    without_lang = _transcribe(_reply({"segments": []}), audio_file, config=config)
    assert without_lang.language == "en"


def test_a_null_text_segment_is_skipped_not_stringified(audio_file):
    # A present-but-null text must not become the literal cue "None".
    body = {
        "text": "kept.",
        "segments": [
            {"start": 0.0, "end": 1.0, "text": "kept."},
            {"start": 1.0, "end": 2.0, "text": None},
        ],
    }
    transcript = _transcribe(_reply(body), audio_file)
    assert [s.text for s in transcript.segments] == ["kept."]
    assert all(s.text != "None" for s in transcript.segments)


def test_a_non_finite_timestamp_is_dropped_not_crashed(audio_file):
    # A provider that emits the non-standard JSON literals Infinity/NaN (Python's
    # json parses them) must have those segments skipped, not crash rendering. The
    # raw body is used because json.dumps refuses to serialise inf/nan.
    raw_body = (
        b'{"text": "kept.", "segments": ['
        b'{"start": 0.0, "end": 1.0, "text": "kept."}, '
        b'{"start": 0.0, "end": Infinity, "text": "infinite"}, '
        b'{"start": NaN, "end": 2.0, "text": "not a number"}]}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw_body, headers={"content-type": "application/json"})

    transcript = _transcribe(handler, audio_file)
    assert [s.text for s in transcript.segments] == ["kept."]

    # And the surviving transcript renders to subtitles without raising.
    from app.transcription.emit import to_srt, to_webvtt

    assert to_webvtt(transcript).count("-->") == 1
    assert to_srt(transcript).count("-->") == 1


def test_gateway_failures_propagate(audio_file):
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    from app.transcription.stt_client import STTUnavailable

    with pytest.raises(STTUnavailable):
        _transcribe(unreachable, audio_file)
