"""What happens when a provider retires the model we are configured to use.

This is not hypothetical. Groq withdrew `llama-3.1-8b-instant` on 16 August 2026,
and from that morning every generation in this engine failed outright: the gateway
answered 404, the client filed that under "a reply we could not use", and the
configured fallback — which was healthy the whole time — was never consulted.

A retired model is a fact about the *gateway*, like a rejected key: no retry helps,
no wait helps, and the fallback can serve immediately. So it must reach the failover.
The matching is on what the provider *says* rather than on the status code alone,
because a bare 404 is far more often a mistyped base URL, and failing over on that
would hide a typo behind a working fallback.
"""

from __future__ import annotations

import httpx
import pytest

from app.summarization.llm_client import (
    LLMBadResponse,
    LLMUnavailable,
    _is_model_missing,
    _status_error,
)

GROQ_RETIRED = (
    '{"error":{"message":"The model `llama-3.1-8b-instant` does not exist or you do '
    'not have access to it.","type":"invalid_request_error","code":"model_not_found"}}'
)


def _error(status: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


# --- the case that actually happened -------------------------------------------


def test_a_retired_model_is_treated_as_the_gateway_being_unavailable():
    """The whole point: this must be the type the failover catches."""
    assert isinstance(_status_error(_error(404, GROQ_RETIRED)), LLMUnavailable)


def test_the_message_names_the_retirement_and_the_model():
    """Whoever reads the log has to learn that a version needs re-pinning, not that
    something was flaky. Losing the provider's own sentence loses the instruction.

    Asserting the model name alone was not enough: the generic "returned HTTP 404"
    message quotes the body too, so that assertion still passed with the whole
    retirement branch removed. It has to pin the wording that only this branch
    produces.
    """
    message = str(_status_error(_error(404, GROQ_RETIRED)))
    assert "no longer serves the configured model" in message
    assert "llama-3.1-8b-instant" in message


# --- the thing this must not become --------------------------------------------


def test_a_mistyped_base_url_still_fails_loudly():
    """A 404 from a wrong path says nothing about a model. Failing over here would
    hide a configuration typo behind a fallback that quietly works."""
    body = "<html><title>404 Not Found</title></html>"
    assert isinstance(_status_error(_error(404, body)), LLMBadResponse)


def test_an_ordinary_server_error_is_not_a_missing_model():
    assert isinstance(_status_error(_error(500, "upstream exploded")), LLMBadResponse)


# --- the phrase matching, across the wordings providers actually use -------------


@pytest.mark.parametrize(
    "body",
    [
        GROQ_RETIRED,
        '{"error":{"message":"model not found","code":"model_not_found"}}',
        '{"error":{"message":"The model `x` is not a valid model ID"}}',
        '{"error":{"message":"unknown model: foo"}}',
        '{"error":{"message":"This model has been decommissioned."}}',
    ],
)
def test_the_wordings_providers_use_are_all_recognised(body):
    assert _is_model_missing(404, body) or _is_model_missing(400, body)


@pytest.mark.parametrize("status", [200, 401, 403, 413, 429, 500, 502, 503])
def test_only_a_client_error_can_mean_a_missing_model(status):
    """A 429 saying "does not exist" is a rate limit that happens to mention it, and
    a 503 is an outage. Widening past 400 and 404 would swallow both."""
    assert not _is_model_missing(status, GROQ_RETIRED)


def test_the_check_is_case_insensitive():
    assert _is_model_missing(404, "The Model Does Not Exist")


def test_a_body_that_says_nothing_useful_is_not_a_missing_model():
    assert not _is_model_missing(404, "")
