"""Per-endpoint token-bucket rate limiter with exponential backoff + jitter.

A single shared instance owns the limiter across the process, per the ground
rule that quote/historical/order endpoints have independent limits and a
single global limiter would be wrong.
"""
from __future__ import annotations

import logging
import random
import threading
import time

from config.smartapi_limits import ENDPOINT_LIMITS, EndpointLimit

logger = logging.getLogger(__name__)


class TokenBucket:
    def __init__(self, limit: EndpointLimit):
        self._rate = limit.requests_per_second
        self._capacity = limit.burst
        self._tokens = float(limit.burst)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def acquire(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
            if time.monotonic() >= deadline:
                raise TimeoutError("Rate limiter wait exceeded timeout")
            time.sleep(0.05)


class RateLimiter:
    """Owns one TokenBucket per endpoint class. Not a singleton by force —
    the caller (angelone_client) is responsible for owning exactly one
    instance and sharing it, per the ground rule that "a single shared
    client owns the limiter."
    """

    def __init__(self) -> None:
        self._buckets = {name: TokenBucket(limit) for name, limit in ENDPOINT_LIMITS.items()}

    def acquire(self, endpoint: str) -> None:
        if endpoint not in self._buckets:
            raise KeyError(
                f"Unknown endpoint class '{endpoint}' — add it to config/smartapi_limits.py "
                "ENDPOINT_LIMITS before using it, do not silently fall back to a default."
            )
        self._buckets[endpoint].acquire()


def backoff_with_jitter(attempt: int, base: float = 0.5, cap: float = 30.0) -> float:
    """Exponential backoff with full jitter, for 429/throttle responses."""
    exp = min(cap, base * (2**attempt))
    return random.uniform(0, exp)


def call_with_retry(fn, endpoint: str, limiter: RateLimiter, max_attempts: int = 5):
    """Run fn() honoring the rate limiter, retrying on throttle-shaped exceptions.

    fn must raise a RateLimitedError (defined by the caller's client) on 429/throttle
    responses; any other exception propagates immediately without retry, since silent
    retry-forever on non-throttle failures is exactly what the auth ground rules forbid.
    """
    from broker.exceptions import RateLimitedError

    for attempt in range(max_attempts):
        limiter.acquire(endpoint)
        try:
            return fn()
        except RateLimitedError:
            if attempt == max_attempts - 1:
                raise
            delay = backoff_with_jitter(attempt)
            logger.warning("Throttled on endpoint=%s, retrying in %.2fs (attempt %d)", endpoint, delay, attempt + 1)
            time.sleep(delay)
    raise RuntimeError("unreachable")
