"""A thin async client for any OpenAI-compatible speech-to-text endpoint.

This is the audio counterpart of ``summarization.llm_client``. Transcription
speaks the OpenAI ``/audio/transcriptions`` contract — a multipart upload rather
than a JSON chat — which Groq serves with ``whisper-large-v3`` and OpenAI with
``whisper-1``; a local ``faster-whisper`` behind the same shape works too. So the
provider is again just configuration: base URL, key, and model.

The request asks for ``verbose_json`` with per-segment timestamps, so the reply
carries start/end times we can turn into subtitle cues rather than one flat
string. Failures map to the same small typed hierarchy the model client uses, so
the transport layer returns the right HTTP status without re-inspecting httpx.
"""

from __future__ import annotations

from asyncio import sleep as _sleep
from pathlib import Path
from typing import Any

import httpx

# A rate-limited request (HTTP 429) is retried a few times with backoff, capped so
# a request can never hang on a long Retry-After. Mirrors the model client.
_MAX_RETRIES = 2
_BASE_BACKOFF = 1.0  # seconds, doubled each retry
_MAX_RETRY_WAIT = 20.0


class STTError(Exception):
    """Base class for any failure talking to the speech-to-text gateway."""


class STTUnavailable(STTError):
    """The gateway could not be reached, refused the key, or is rate-limited."""


class STTTimeout(STTError):
    """Transcription took longer than the configured timeout."""


class STTBadResponse(STTError):
    """The gateway replied, but with something we could not use."""


async def transcribe_audio(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    model: str,
    audio_path: str,
    language: str | None = None,
    max_retries: int = _MAX_RETRIES,
) -> dict[str, Any]:
    """Transcribe one media file and return the gateway's ``verbose_json`` reply.

    The file is re-opened per attempt so a retried request re-streams cleanly. A
    rate-limit (HTTP 429) is retried with backoff; any other failure maps to an
    `STTError` subclass.
    """
    path = Path(audio_path)
    data: dict[str, str] = {"model": model, "response_format": "verbose_json"}
    if language:
        data["language"] = language
    # Ask for per-segment timestamps explicitly; providers that honour the hint
    # return finer cues, and those that ignore it are unaffected.
    data["timestamp_granularities[]"] = "segment"
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{base_url}/audio/transcriptions"

    for attempt in range(max_retries + 1):
        try:
            with path.open("rb") as handle:
                files = {"file": (path.name, handle, "application/octet-stream")}
                response = await client.post(url, data=data, files=files, headers=headers)
            response.raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise STTUnavailable(f"Could not reach the STT gateway at {base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:  # read/write/pool timeout once connected
            raise STTTimeout(f"Transcription timed out after {client.timeout.read}s") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt < max_retries:
                await _sleep(_retry_after(exc.response, attempt))
                continue
            raise _status_error(exc) from exc
        except httpx.HTTPError as exc:  # other transport faults: DNS, broken pipe, …
            raise STTUnavailable(f"Could not reach the STT gateway at {base_url}: {exc}") from exc
        else:
            return _extract_body(response)

    raise STTUnavailable("The STT gateway is rate-limited (HTTP 429).")


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """How long to wait before retrying a rate-limited request."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(float(header), _MAX_RETRY_WAIT)
        except ValueError:
            pass
    return min(_BASE_BACKOFF * (2**attempt), _MAX_RETRY_WAIT)


def _status_error(exc: httpx.HTTPStatusError) -> STTError:
    """Map a non-2xx status to the right error type."""
    code = exc.response.status_code
    if code in (401, 403):
        return STTUnavailable(f"The STT gateway rejected the API key (HTTP {code}).")
    if code == 429:
        return STTUnavailable("The STT gateway is rate-limited (HTTP 429).")
    return STTBadResponse(f"The STT gateway returned HTTP {code}: {exc.response.text[:200]}")


def _extract_body(response: httpx.Response) -> dict[str, Any]:
    """Parse the transcription reply as a JSON object."""
    try:
        body = response.json()
    except ValueError as exc:  # includes json.JSONDecodeError
        raise STTBadResponse(f"STT response body was not JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise STTBadResponse("STT response was valid JSON but not an object.")
    return body
