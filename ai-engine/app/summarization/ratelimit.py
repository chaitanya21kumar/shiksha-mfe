"""Stay inside a gateway's rate budget instead of discovering it by being refused.

Every OpenAI-compatible gateway worth using reports its own budget on each reply::

    x-ratelimit-remaining-requests: 14399
    x-ratelimit-remaining-tokens:   5241
    x-ratelimit-reset-tokens:       7.59s

Those headers are the difference between a client that *reacts* to HTTP 429 and one
that never provokes it. Reacting is not enough on its own: by the time a 429 comes
back, the request has already been spent, and on a free tier the window that has to
pass before the next one can succeed is often longer than any backoff a caller is
willing to sit through.

So this module keeps, per gateway, whatever the last reply said was left, and makes
the next caller wait out the window when the budget is nearly gone. It is
deliberately small and deliberately conservative:

- **Per base URL.** Two providers have two budgets; a local Ollama has none at all
  and simply never reports these headers, in which case nothing here ever waits.
- **Fail open.** A gateway that reports nothing, or reports something unparseable,
  is treated as unlimited. The 429 retry in the client remains the backstop.
- **Token budget, not request count.** On the free tiers this engine is developed
  against, tokens per minute binds long before requests per day does.

The headers are the de-facto standard OpenAI established and Groq, Together,
Fireworks and OpenRouter all follow; a gateway that does not send them loses
nothing.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

logger = logging.getLogger("ai_engine.ratelimit")

#: Wait for the window to turn over when fewer than this many tokens are left. Set
#: from the size of a single generation call rather than guessed: one assessment
#: request runs to roughly a thousand tokens including its reply.
_LOW_TOKENS = 1200
#: Never sit on a wait longer than this, whatever the gateway claims. A demo that
#: pauses for a minute is bad; one that pauses for ten is broken.
_MAX_WAIT = 65.0
#: Trust a reading for only this long. Past it the window has almost certainly
#: turned over and the stale number would make us wait for nothing.
_STALE_AFTER = 120.0

_DURATION = re.compile(r"(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?$")


def parse_duration(value: str) -> float | None:
    """Parse the ``7.59s`` / ``1m30s`` / ``60`` forms these headers use."""
    text = (value or "").strip()
    if not text:
        return None
    try:  # a bare number of seconds
        return float(text)
    except ValueError:
        pass
    if text.endswith("ms"):
        try:
            return float(text[:-2]) / 1000.0
        except ValueError:
            return None
    match = _DURATION.fullmatch(text)
    if not match or not any(match.groups()):
        return None
    minutes, seconds = match.groups()
    return float(minutes or 0) * 60.0 + float(seconds or 0)


class _Budget:
    """What the gateway last said was left, and when it refills."""

    __slots__ = ("remaining_tokens", "reset_at", "seen_at")

    def __init__(self) -> None:
        self.remaining_tokens: int | None = None
        self.reset_at: float | None = None
        self.seen_at: float = 0.0


class RateLimitGovernor:
    """Holds one budget per gateway and paces callers against it."""

    def __init__(self) -> None:
        self._budgets: dict[str, _Budget] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, gateway: str) -> asyncio.Lock:
        # One lock per gateway, so a wait serialises the callers that share a budget
        # rather than letting all of them wake at once and blow it again.
        if gateway not in self._locks:
            self._locks[gateway] = asyncio.Lock()
        return self._locks[gateway]

    def observe(self, gateway: str, headers) -> None:
        """Record what a reply said about the remaining budget."""
        remaining = headers.get("x-ratelimit-remaining-tokens")
        reset = headers.get("x-ratelimit-reset-tokens")
        if remaining is None:
            return
        try:
            left = int(float(remaining))
        except (TypeError, ValueError):
            return
        budget = self._budgets.setdefault(gateway, _Budget())
        budget.remaining_tokens = left
        budget.seen_at = time.monotonic()
        window = parse_duration(reset or "")
        budget.reset_at = budget.seen_at + window if window is not None else None

    def has_room(self, gateway: str) -> bool:
        """Could the next call go out now, without waiting?

        The non-blocking half of `wait_for_room`, for a caller that has somewhere
        else to send the request. Waiting out a window is the right answer only
        when the alternative is not sending it at all.
        """
        budget = self._budgets.get(gateway)
        if budget is None or budget.remaining_tokens is None:
            return True
        if time.monotonic() - budget.seen_at > _STALE_AFTER:
            return True
        return budget.remaining_tokens >= _LOW_TOKENS

    async def wait_for_room(self, gateway: str) -> float:
        """Sleep, if the last reply said there is not enough budget for another call.

        Returns the seconds actually waited, which is zero in the common case and
        in every case where the gateway reports nothing.
        """
        async with self._lock(gateway):
            budget = self._budgets.get(gateway)
            if budget is None or budget.remaining_tokens is None:
                return 0.0
            now = time.monotonic()
            if now - budget.seen_at > _STALE_AFTER:
                return 0.0
            if budget.remaining_tokens >= _LOW_TOKENS or budget.reset_at is None:
                return 0.0
            wait = min(max(budget.reset_at - now, 0.0), _MAX_WAIT)
            if wait <= 0:
                return 0.0
            logger.info(
                "Pausing %.1fs on %s: %s tokens left in the window",
                wait, gateway, budget.remaining_tokens,
            )
            await asyncio.sleep(wait)
            # The window has turned over; forget the reading rather than making the
            # next caller wait on it a second time.
            budget.remaining_tokens = None
            budget.reset_at = None
            return wait


#: One governor for the process. The budget belongs to the gateway, not to a
#: request, so every router and pipeline shares it.
governor = RateLimitGovernor()
