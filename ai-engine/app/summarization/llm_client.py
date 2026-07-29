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
import logging
from asyncio import sleep as _sleep
from typing import Any

import httpx

from .ratelimit import _LOW_TOKENS, governor, parse_duration

logger = logging.getLogger("ai_engine.llm")

# A rate-limited request (HTTP 429) is retried with backoff, and the wait honours
# the gateway's own Retry-After. The cap is deliberately above a minute: a free
# tier's token window is a minute long, so a shorter cap retries *before* the
# budget has refilled, spends every attempt on the same refusal, and reports a
# rate limit the caller could simply have waited out.
_MAX_RETRIES = 4
_BASE_BACKOFF = 1.5  # seconds, doubled each retry
_MAX_RETRY_WAIT = 65.0


class LLMError(Exception):
    """Base class for any failure talking to the model gateway."""


class LLMUnavailable(LLMError):
    """The gateway could not be reached, refused the key, or is rate-limited."""


class LLMTimeout(LLMError):
    """Generation took longer than the configured timeout."""


class LLMBadResponse(LLMError):
    """The gateway replied, but with something we could not use."""


class LLMRequestTooLarge(LLMError):
    """The prompt is bigger than this gateway will accept, at any speed.

    Distinct from a rate limit, because waiting cannot help: the request exceeds
    the ceiling rather than the current allowance. It is worth its own type
    because it is precisely the case a second gateway with a larger budget can
    serve — where a retry is useless, a failover is not.
    """


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
        # Pace against what the gateway last told us was left, so a burst of calls
        # waits out the window instead of provoking a refusal it then has to
        # recover from. No-op on any gateway that does not report a budget.
        await governor.wait_for_room(base_url)
        try:
            response = await client.post(
                f"{base_url}/chat/completions", json=payload, headers=headers
            )
            governor.observe(base_url, response.headers)
            response.raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise LLMUnavailable(f"Could not reach the model gateway at {base_url}: {exc}") from exc
        except httpx.TimeoutException as exc:  # read/write/pool timeout once connected
            raise LLMTimeout(f"Generation timed out after {client.timeout.read}s") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and attempt < max_retries:
                governor.observe(base_url, exc.response.headers)
                wait = _retry_after(exc.response, attempt)
                if _cooldown_exceeds_patience(exc.response):
                    # The gateway is asking for longer than we will wait — a daily
                    # allowance rather than a per-minute window. Sleeping through
                    # four more attempts changes nothing except how long the caller
                    # waits to hear it, and a configured fallback can serve now.
                    logger.warning(
                        "%s wants %ss before the next request; not retrying here",
                        base_url, exc.response.headers.get("retry-after"),
                    )
                    raise _status_error(exc) from exc
                # Log which budget was hit. A rate limit is the one failure whose
                # cause is stated in the reply, and losing that makes it look random.
                logger.warning(
                    "Rate-limited by %s (attempt %d/%d), waiting %.1fs — %s",
                    base_url, attempt + 1, max_retries + 1, wait,
                    "; ".join(
                        f"{k}={v}" for k, v in exc.response.headers.items()
                        if k.startswith("x-ratelimit") or k == "retry-after"
                    ) or "no budget headers",
                )
                await _sleep(wait)
                continue
            raise _status_error(exc) from exc
        except httpx.HTTPError as exc:  # other transport faults: DNS, broken pipe, …
            raise LLMUnavailable(f"Could not reach the model gateway at {base_url}: {exc}") from exc
        else:
            return _extract_content(response)

    raise LLMUnavailable("The model gateway is rate-limited (HTTP 429).")


def _cooldown_exceeds_patience(response: httpx.Response) -> bool:
    """Is the gateway asking us to wait longer than retrying could possibly help?"""
    asked = parse_duration(response.headers.get("retry-after", ""))
    return asked is not None and asked > _MAX_RETRY_WAIT


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """How long to wait before retrying a rate-limited request.

    ``Retry-After`` is authoritative when the gateway sends it — it is the server
    saying how long it needs, and it already accounts for whichever budget ran out.
    Only when it is absent do we infer the wait, and then from the reset header for
    the budget that is actually spent.

    The header for a budget that is *not* spent must be ignored. A daily request
    counter reports the time until midnight-equivalent whether or not requests are
    the problem, and folding that into the wait made every token-limited retry sit
    for the full ceiling — a six-second cool-down became sixty-five.
    """
    headers = response.headers
    asked = parse_duration(headers.get("retry-after", ""))
    if asked is not None:
        return min(asked + 0.5, _MAX_RETRY_WAIT)

    inferred: list[float] = []
    for remaining_key, reset_key, floor in (
        ("x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens", _LOW_TOKENS),
        ("x-ratelimit-remaining-requests", "x-ratelimit-reset-requests", 1),
    ):
        try:
            left = float(headers.get(remaining_key, ""))
        except ValueError:
            continue
        if left >= floor:  # this budget is not the one that refused us
            continue
        window = parse_duration(headers.get(reset_key, ""))
        if window is not None:
            inferred.append(window + 0.5)

    backoff = _BASE_BACKOFF * (2**attempt)
    return min(max(inferred) if inferred else backoff, _MAX_RETRY_WAIT)


def _status_error(exc: httpx.HTTPStatusError) -> LLMError:
    """Map a non-2xx status to the right error type."""
    code = exc.response.status_code
    if code in (401, 403):
        return LLMUnavailable(f"The model gateway rejected the API key (HTTP {code}).")
    body = exc.response.text[:300]
    # Groq reports an over-large prompt as 413, and sometimes as a 429 whose body
    # says "Request too large" — the distinction matters because one is a wait and
    # the other never can be.
    too_large = code == 413 or (code == 429 and "request too large" in body.lower())
    if too_large:
        return LLMRequestTooLarge(
            f"The prompt is larger than this gateway accepts (HTTP {code}): {body[:200]}"
        )
    if code == 429:
        return LLMUnavailable("The model gateway is rate-limited (HTTP 429).")
    return LLMBadResponse(f"The model gateway returned HTTP {code}: {body[:200]}")


def _extract_content(response: httpx.Response) -> dict[str, Any]:
    """Pull the assistant message out of a chat completion and parse it as JSON."""
    try:
        body = response.json()
    except ValueError as exc:  # includes json.JSONDecodeError
        raise LLMBadResponse(f"Gateway response body was not JSON: {exc}") from exc
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices:
        raise LLMBadResponse("Gateway response contained no choices.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not content:
        raise LLMBadResponse("Gateway response contained no message content.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMBadResponse(f"Model output was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMBadResponse("Model output was valid JSON but not an object.")
    return parsed


async def chat_json_for(
    client: httpx.AsyncClient,
    config,
    *,
    system: str,
    user: str,
) -> dict[str, Any]:
    """Run one chat completion for a `GenerationConfig`, failing over if it must.

    The engine speaks one contract to every provider, which is what makes a second
    gateway three settings rather than a second code path. The failover is
    deliberately narrow: it triggers only on `LLMUnavailable` — a gateway that is
    unreachable, refusing the key, or out of budget — and never on a timeout or on
    a reply we simply could not use, because retrying those elsewhere would hide a
    real fault behind a slower one.

    With no fallback configured this is exactly `chat_json`, which is the default.

    When one *is* configured the primary gets a single attempt rather than five.
    Retrying exists to outlast a cool-down when there is nowhere else to go; with a
    second gateway standing by, every retry is a minute of waiting to reach a
    conclusion already available. Measured on an exhausted free tier: the same
    request went from 86 seconds to a handful.
    """
    fallback_url = getattr(config, "fallback_base_url", "")
    fallback_key = getattr(config, "fallback_api_key", "")
    has_fallback = bool(fallback_url and fallback_key)
    try:
        return await chat_json(
            client,
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            system=system,
            user=user,
            temperature=config.temperature,
            max_retries=0 if has_fallback else _MAX_RETRIES,
        )
    except (LLMUnavailable, LLMRequestTooLarge):
        if not has_fallback:
            raise
        logger.warning(
            "Primary gateway unavailable; falling back to %s", fallback_url
        )
        return await chat_json(
            client,
            base_url=fallback_url,
            api_key=fallback_key,
            model=getattr(config, "fallback_model", "") or config.model,
            system=system,
            user=user,
            temperature=config.temperature,
        )
