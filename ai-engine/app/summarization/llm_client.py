"""A thin async client for any OpenAI-compatible chat endpoint.

The engine talks to models through the OpenAI chat-completions contract, which
is spoken by hosted providers (Groq, OpenAI, OpenRouter, …) and by a local
Ollama instance alike. Switching providers is therefore just configuration —
base URL, API key, and model — with no change to the calling code. This is the
abstraction that lets development run on a hosted model now and move to a
self-hosted one for production later.

Every call asks for a JSON-object response (validated downstream), and failures
map to a small typed hierarchy so the transport layer can return the right HTTP
status without re-inspecting raw httpx errors.
"""

from __future__ import annotations

import json
from asyncio import sleep as _sleep
from typing import Any

import httpx

# A rate-limited request (HTTP 429) is retried a few times with backoff. The wait
# honours the gateway's Retry-After when present, but is capped so a request can
# never hang on a long one.
_MAX_RETRIES = 2
_BASE_BACKOFF = 1.0  # seconds, doubled each retry
_MAX_RETRY_WAIT = 20.0


class LLMError(Exception):
    """Base class for any failure talking to the model gateway."""


class LLMUnavailable(LLMError):
    """The gateway could not be reached, refused the key, or is rate-limited."""


class LLMTimeout(LLMError):
    """Generation took longer than the configured timeout."""


class LLMBadResponse(LLMError):
    """The gateway replied, but with something we could not use."""


async def chat_json(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    temperature: float,
    max_retries: int = _MAX_RETRIES,
) -> dict[str, Any]:
    """Run one chat completion and return the model's reply parsed as a dict.

    Asks for a JSON object via ``response_format`` so the assistant message is
    machine-readable. A rate-limit (HTTP 429) is retried with backoff; any other
    failure maps to an `LLMError` subclass.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    for attempt in range(max_retries + 1):
        try:
            response = await client.post(
                f"{base_url}/chat/completions", json=payload, headers=headers
            )
            response.raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailable(f"Could not reach the model gateway at {base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:  # read/write/pool timeout once connected
            raise LLMTimeout(f"Generation timed out after {client.timeout.read}s") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt < max_retries:
                await _sleep(_retry_after(exc.response, attempt))
                continue
            raise _status_error(exc) from exc
        except httpx.HTTPError as exc:  # other transport faults: DNS, broken pipe, …
            raise LLMUnavailable(f"Could not reach the model gateway at {base_url}: {exc}") from exc
        else:
            return _extract_content(response)

    raise LLMUnavailable("The model gateway is rate-limited (HTTP 429).")


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """How long to wait before retrying a rate-limited request."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(float(header), _MAX_RETRY_WAIT)
        except ValueError:
            pass
    return min(_BASE_BACKOFF * (2**attempt), _MAX_RETRY_WAIT)


def _status_error(exc: httpx.HTTPStatusError) -> LLMError:
    """Map a non-2xx status to the right error type."""
    code = exc.response.status_code
    if code in (401, 403):
        return LLMUnavailable(f"The model gateway rejected the API key (HTTP {code}).")
    if code == 429:
        return LLMUnavailable("The model gateway is rate-limited (HTTP 429).")
    return LLMBadResponse(f"The model gateway returned HTTP {code}: {exc.response.text[:200]}")


def _extract_content(response: httpx.Response) -> dict[str, Any]:
    """Pull the assistant message out of a chat completion and parse it as JSON."""
    try:
        body = response.json()
    except ValueError as exc:  # includes json.JSONDecodeError
        raise LLMBadResponse(f"Gateway response body was not JSON: {exc}") from exc
    choices = body.get("choices") if isinstance(body, dict) else None
    if not choices:
        raise LLMBadResponse("Gateway response contained no choices.")
    content = (choices[0].get("message") or {}).get("content")
    if not content:
        raise LLMBadResponse("Gateway response contained no message content.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMBadResponse(f"Model output was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMBadResponse("Model output was valid JSON but not an object.")
    return parsed
