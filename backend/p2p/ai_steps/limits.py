"""Client-side rate limits for model providers, so free tiers are not pushed into errors.

A limiter keeps a sliding window of what was sent in the last minute (requests and tokens) and in the last 24 hours
(requests). Before each call it waits until the call fits. If the wait would be long, or the day's quota is used up,
it raises QuotaExhausted so the gateway moves to the next model instead of waiting. Where a provider tells us what it
has left (Groq sends x-ratelimit headers), the limiter trusts that over its own count, which also covers other
processes using the same key. NVIDIA's free tier publishes no numbers and sends no headers, so its limits are a
conservative guess.
"""

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from p2p.ai_steps.adapters import BadRequest

MINUTE = 60.0
DAY = 86_400.0


class QuotaExhausted(BadRequest):
    """Waiting for this model's limit would take too long (or the daily quota is gone). Use another model."""


@dataclass(frozen=True)
class Limits:
    rpm: int | None = None  # requests per minute
    tpm: int | None = None  # tokens per minute (input plus output)
    rpd: int | None = None  # requests per day


def estimate_tokens(text: str) -> int:
    """A deliberately high guess (3 characters per token) so we under-use the limit rather than hit it."""
    return len(text) // 3 + 1


class RateLimiter:
    def __init__(
        self,
        limits: Limits,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_wait: float = 45.0,
        safety: float = 0.9,
    ) -> None:
        self.limits, self.clock, self.sleep, self.max_wait = limits, clock, sleep, max_wait
        self.safety = safety  # stay at 90% of the published limit
        self._minute: deque[tuple[float, int]] = deque()  # (time, tokens)
        self._day: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def _trim(self, now: float) -> None:
        while self._minute and now - self._minute[0][0] >= MINUTE:
            self._minute.popleft()
        while self._day and now - self._day[0] >= DAY:
            self._day.popleft()

    def _wait_needed(self, now: float, tokens: int) -> float:
        self._trim(now)
        wait = max(0.0, self._blocked_until - now)
        lim = self.limits
        if lim.rpd is not None and len(self._day) >= int(lim.rpd * self.safety):
            return DAY - (now - self._day[0])  # until the oldest call of the last 24 hours drops out
        if lim.rpm is not None and len(self._minute) >= max(1, int(lim.rpm * self.safety)):
            wait = max(wait, MINUTE - (now - self._minute[0][0]))
        if lim.tpm is not None:
            budget = int(lim.tpm * self.safety)
            if tokens > budget:
                raise QuotaExhausted(f"one call needs about {tokens} tokens; the limit is {lim.tpm} a minute")
            used, drop = sum(t for _, t in self._minute), list(self._minute)
            while used + tokens > budget and drop:
                at, t = drop.pop(0)
                used -= t
                wait = max(wait, MINUTE - (now - at))
        return wait

    def acquire(self, tokens: int, max_wait: float | None = None) -> float:
        """Wait until a call of about `tokens` fits. Returns the seconds waited.

        `max_wait` overrides the limiter default: background jobs can be patient, a person waiting at a screen cannot.
        """
        limit = self.max_wait if max_wait is None else max_wait
        waited = 0.0
        while True:
            with self._lock:
                now = self.clock()
                wait = self._wait_needed(now, tokens)
                if wait <= 0:
                    self._minute.append((now, tokens))
                    self._day.append(now)
                    return waited
            if wait > limit:
                raise QuotaExhausted(f"rate limit: would have to wait {wait:.0f}s")
            self.sleep(wait + 0.05)
            waited += wait + 0.05

    def settle(self, estimated: int, actual: int) -> None:
        """Replace the estimate of the last call with what the provider says it used."""
        with self._lock:
            for i in range(len(self._minute) - 1, -1, -1):
                at, t = self._minute[i]
                if t == estimated:
                    self._minute[i] = (at, actual)
                    return

    def penalise(self, seconds: float) -> None:
        """The provider said 429: nobody sends anything for this long."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self.clock() + seconds)

    def observe(self, headers: dict[str, str]) -> None:
        """Use the provider's own numbers (Groq style x-ratelimit headers) when it sends them."""
        remaining_tokens = headers.get("x-ratelimit-remaining-tokens")
        reset_tokens = headers.get("x-ratelimit-reset-tokens")
        if remaining_tokens is not None and self.limits.tpm is not None:
            try:
                if int(remaining_tokens) < self.limits.tpm * (1 - self.safety):
                    self.penalise(_seconds(reset_tokens))
            except ValueError:
                pass
        remaining_requests = headers.get("x-ratelimit-remaining-requests")
        if remaining_requests == "0":
            self.penalise(min(_seconds(headers.get("x-ratelimit-reset-requests")), DAY))


def _seconds(value: str | None) -> float:
    """Parse '1m26.4s', '952ms', '7.66s' or '2h3m' into seconds."""
    if not value:
        return 1.0
    total, number, unit = 0.0, "", ""
    for ch in value:
        if ch.isdigit() or ch == ".":
            if unit:
                total += _unit(number, unit)
                number, unit = "", ""
            number += ch
        else:
            unit += ch
    if number:
        total += _unit(number, unit or "s")
    return total or 1.0


def _unit(number: str, unit: str) -> float:
    return float(number) * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}.get(unit, 1.0)
