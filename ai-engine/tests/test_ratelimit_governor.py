"""Tests for the rate-limit governor.

The point of the governor is to stay inside a budget the gateway *tells* us about,
rather than discovering it by being refused. These pin the two things that make it
safe to have in the request path at all: it never waits on a gateway that reports
nothing, and it never waits longer than its ceiling.
"""

import asyncio
import time

import httpx
import pytest

from app.summarization.llm_client import chat_json
from app.summarization.ratelimit import RateLimitGovernor, parse_duration


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7.59s", 7.59), ("60", 60.0), ("1m30s", 90.0), ("2m", 120.0),
        ("500ms", 0.5), ("", None), ("soon", None), ("  12s ", 12.0),
    ],
)
def test_the_duration_forms_these_headers_really_use(value, expected):
    assert parse_duration(value) == expected


def _headers(remaining=None, reset=None) -> httpx.Headers:
    h = {}
    if remaining is not None:
        h["x-ratelimit-remaining-tokens"] = str(remaining)
    if reset is not None:
        h["x-ratelimit-reset-tokens"] = reset
    return httpx.Headers(h)


def test_a_gateway_that_reports_nothing_is_treated_as_unlimited():
    # A local Ollama sends none of these headers. It must never be made to wait.
    g = RateLimitGovernor()
    g.observe("http://localhost:11434/v1", _headers())
    assert asyncio.run(g.wait_for_room("http://localhost:11434/v1")) == 0.0


def test_plenty_of_budget_means_no_wait():
    g = RateLimitGovernor()
    g.observe("https://api.test/v1", _headers(remaining=5000, reset="8s"))
    assert asyncio.run(g.wait_for_room("https://api.test/v1")) == 0.0


def test_a_nearly_spent_budget_waits_for_the_window_to_turn_over():
    g = RateLimitGovernor()
    g.observe("https://api.test/v1", _headers(remaining=40, reset="0.4s"))
    started = time.monotonic()
    waited = asyncio.run(g.wait_for_room("https://api.test/v1"))
    assert 0.2 <= waited <= 1.0
    assert time.monotonic() - started >= 0.2


def test_the_wait_is_dropped_once_it_has_been_served():
    # Otherwise every later caller pays for the same exhausted window again.
    g = RateLimitGovernor()
    g.observe("https://api.test/v1", _headers(remaining=10, reset="0.3s"))
    asyncio.run(g.wait_for_room("https://api.test/v1"))
    assert asyncio.run(g.wait_for_room("https://api.test/v1")) == 0.0


def test_an_unparseable_budget_is_ignored_rather_than_guessed():
    g = RateLimitGovernor()
    g.observe("https://api.test/v1", httpx.Headers({"x-ratelimit-remaining-tokens": "lots"}))
    assert asyncio.run(g.wait_for_room("https://api.test/v1")) == 0.0


def test_two_gateways_keep_separate_budgets():
    g = RateLimitGovernor()
    g.observe("https://a.test/v1", _headers(remaining=10, reset="30s"))
    g.observe("https://b.test/v1", _headers(remaining=9000, reset="30s"))
    assert asyncio.run(g.wait_for_room("https://b.test/v1")) == 0.0


# --- the client's retry policy ------------------------------------------------


def test_a_rate_limited_call_retries_and_then_succeeds():
    # The regression this exists for: a token-per-minute window is a minute long,
    # so a cap below that retried before the budget refilled and reported a rate
    # limit the caller could simply have waited out.
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0.4"}, json={"error": "slow down"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0)) as client:
            return await chat_json(
                client, base_url="https://retry.test/v1", api_key="k", model="m",
                system="s", user="u", temperature=0.2,
            )

    assert asyncio.run(go()) == {"ok": True}
    assert len(calls) == 2


def test_the_reset_header_is_honoured_when_there_is_no_retry_after():
    # Groq sends x-ratelimit-reset-tokens rather than Retry-After on a token limit.
    # The reset here is deliberately LONGER than the first backoff step, so the
    # measured wait can only be explained by the header having been read — an
    # earlier version of this test passed whether or not it was.
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) == 1:
            # A real token-limit 429 always states what is left as well as when
            # it refills; the reset alone is not enough to know it is the culprit.
            return httpx.Response(
                429,
                headers={"x-ratelimit-remaining-tokens": "12", "x-ratelimit-reset-tokens": "3s"},
                json={"error": "tpm"},
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0)) as client:
            return await chat_json(
                client, base_url="https://tpm.test/v1", api_key="k", model="m",
                system="s", user="u", temperature=0.2,
            )

    started = time.monotonic()
    assert asyncio.run(go()) == {}
    elapsed = time.monotonic() - started
    assert len(seen) == 2
    assert elapsed >= 3.0, f"waited {elapsed:.2f}s — the reset header was not honoured"


# --- the second gateway -------------------------------------------------------


class _Cfg:
    """The shape chat_json_for reads off a GenerationConfig."""

    def __init__(self, **kw):
        self.base_url = kw.get("base_url", "https://primary.test/v1")
        self.api_key = "k"
        self.model = "primary-model"
        self.temperature = 0.2
        self.fallback_base_url = kw.get("fallback_base_url", "")
        self.fallback_api_key = kw.get("fallback_api_key", "")
        self.fallback_model = kw.get("fallback_model", "")


def _run(config, handler):
    from app.summarization.llm_client import chat_json_for

    async def go():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0)) as client:
            return await chat_json_for(client, config, system="s", user="u")

    return asyncio.run(go())


def test_an_exhausted_primary_falls_over_to_the_second_gateway():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "primary" in str(request.url):
            return httpx.Response(429, headers={"retry-after": "0.1"}, json={"error": "tpm"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"from": "fallback"}'}}]})

    config = _Cfg(fallback_base_url="https://second.test/v1", fallback_api_key="k2",
                  fallback_model="second-model")
    assert _run(config, handler) == {"from": "fallback"}
    assert any("second.test" in u for u in seen), "the second gateway was never tried"


def test_with_no_fallback_configured_the_original_failure_is_raised():
    from app.summarization.llm_client import LLMUnavailable

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "0.1"}, json={"error": "tpm"})

    with pytest.raises(LLMUnavailable):
        _run(_Cfg(), handler)


def test_a_bad_reply_does_not_fail_over():
    # Failing over on an unusable reply would hide a real fault behind a slower
    # gateway giving the same unusable reply.
    from app.summarization.llm_client import LLMBadResponse

    tried = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    config = _Cfg(fallback_base_url="https://second.test/v1", fallback_api_key="k2")
    with pytest.raises(LLMBadResponse):
        _run(config, handler)
    assert not any("second.test" in u for u in tried)


# --- how long to wait, which is easy to get wrong in a way nothing notices ------


def _resp(**headers) -> httpx.Response:
    return httpx.Response(429, headers=httpx.Headers(headers))


def test_retry_after_is_authoritative_when_the_gateway_sends_it():
    from app.summarization.llm_client import _retry_after

    # The regression: taking the longest of every reset header folded in
    # x-ratelimit-reset-requests — the DAILY counter — which reports its window
    # whether or not requests are what ran out. A six-second cool-down became
    # sixty-five, on every retry, and the only symptom was a slow demo.
    wait = _retry_after(
        _resp(**{
            "retry-after": "6",
            "x-ratelimit-remaining-tokens": "3523", "x-ratelimit-reset-tokens": "24.77s",
            "x-ratelimit-remaining-requests": "14240", "x-ratelimit-reset-requests": "16m0s",
        }),
        0,
    )
    assert 6.0 <= wait <= 7.0, f"waited {wait}s for a 6-second cool-down"


def test_the_window_of_a_budget_that_is_not_spent_is_ignored():
    from app.summarization.llm_client import _retry_after

    # Tokens are gone, requests are plentiful: wait the token window, not the
    # sixteen minutes until the daily request counter rolls over.
    wait = _retry_after(
        _resp(**{
            "x-ratelimit-remaining-tokens": "40", "x-ratelimit-reset-tokens": "12s",
            "x-ratelimit-remaining-requests": "9000", "x-ratelimit-reset-requests": "16m0s",
        }),
        0,
    )
    assert 12.0 <= wait <= 13.0


def test_with_no_useful_headers_it_falls_back_to_backoff():
    from app.summarization.llm_client import _retry_after

    assert _retry_after(_resp(), 0) < 5.0
    assert _retry_after(_resp(), 3) > _retry_after(_resp(), 0)


def test_the_primary_is_not_retried_when_a_fallback_can_serve_now():
    # Retrying exists to outlast a cool-down when there is nowhere else to go. With
    # a second gateway configured, each retry is a minute spent reaching a
    # conclusion already available — measured at 86s versus 7s on a spent free tier.
    attempts = {"primary": 0, "fallback": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "primary" in str(request.url):
            attempts["primary"] += 1
            return httpx.Response(429, headers={"retry-after": "30"}, json={"error": "tpd"})
        attempts["fallback"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": 1}'}}]})

    config = _Cfg(fallback_base_url="https://second.test/v1", fallback_api_key="k2")
    assert _run(config, handler) == {"ok": 1}
    assert attempts["primary"] == 1, f"primary tried {attempts['primary']} times, should be once"
    assert attempts["fallback"] == 1


def test_without_a_fallback_the_primary_is_still_retried():
    # The retry budget is what keeps a single-gateway deployment working through a
    # short cool-down; removing it everywhere would trade one problem for another.
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, headers={"retry-after": "0.1"}, json={"error": "tpm"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    assert _run(_Cfg(), handler) == {}
    assert len(attempts) == 3
