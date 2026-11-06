from geoassist.api.ratelimit import RateLimiter


def test_seventh_request_in_window_is_rejected():
    now = {"t": 0.0}
    limiter = RateLimiter(limit=6, window_seconds=60, clock=lambda: now["t"])
    assert all(limiter.allow("u") for _ in range(6))
    assert limiter.allow("u") is False
    now["t"] = 61.0
    assert limiter.allow("u") is True


def test_limits_are_per_key():
    limiter = RateLimiter(limit=1, clock=lambda: 0.0)
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("a") is False
