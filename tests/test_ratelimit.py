import time

import pytest

from broker.exceptions import RateLimitedError
from broker.ratelimit import RateLimiter, call_with_retry, backoff_with_jitter


def test_unknown_endpoint_raises_key_error():
    limiter = RateLimiter()
    with pytest.raises(KeyError):
        limiter.acquire("not_a_real_endpoint")


def test_token_bucket_throttles_burst():
    limiter = RateLimiter()
    # "order" endpoint: burst=1, rate=1/s -> second immediate acquire must wait.
    start = time.monotonic()
    limiter.acquire("order")
    limiter.acquire("order")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.9  # allow small scheduling slack under 1 req/s


def test_call_with_retry_succeeds_after_throttle():
    limiter = RateLimiter()
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RateLimitedError("throttled")
        return "ok"

    result = call_with_retry(flaky, "quote", limiter, max_attempts=5)
    assert result == "ok"
    assert attempts["n"] == 3


def test_call_with_retry_gives_up_after_max_attempts():
    limiter = RateLimiter()

    def always_throttled():
        raise RateLimitedError("throttled")

    with pytest.raises(RateLimitedError):
        call_with_retry(always_throttled, "quote", limiter, max_attempts=2)


def test_non_throttle_exception_propagates_without_retry():
    limiter = RateLimiter()
    attempts = {"n": 0}

    def broken():
        attempts["n"] += 1
        raise ValueError("not a throttle error")

    with pytest.raises(ValueError):
        call_with_retry(broken, "quote", limiter, max_attempts=5)
    assert attempts["n"] == 1  # must not retry non-throttle failures


def test_backoff_with_jitter_bounded():
    for attempt in range(10):
        delay = backoff_with_jitter(attempt, base=0.5, cap=30.0)
        assert 0 <= delay <= 30.0
