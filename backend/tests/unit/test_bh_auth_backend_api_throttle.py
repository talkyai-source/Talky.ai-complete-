"""auth-sessions-4: THROTTLE (the last 10% of the budget) must not 429.

check_all_tiers used to turn THROTTLE into (False, ..., "Rate limit exceeded"),
so any path started returning 429 at 90% of the configured limit. Only BLOCK
rejects now; the near-limit state is surfaced as warning headers. Blocked
requests are never added to the sliding window.
"""
import fakeredis.aioredis
import pytest

from app.core.security.api_security import APIRateLimiter, RateLimitTier

LIMIT = 10


@pytest.fixture
async def limiter():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    lim = APIRateLimiter(redis_client=redis)
    lim.update_config(RateLimitTier.IP, requests=LIMIT, window=60, block_duration=60)
    yield lim
    await redis.aclose()


async def _hit(lim):
    return await lim.check_all_tiers(ip="203.0.113.7", user_id=None, tenant_id=None,
                                     endpoint="/api/v1/calls/live")


async def test_full_budget_is_usable_and_only_the_overflow_is_rejected(limiter):
    results = [await _hit(limiter) for _ in range(LIMIT)]
    assert [r[0] for r in results] == [True] * LIMIT, "requests 1..LIMIT must all pass"
    allowed, headers, error = await _hit(limiter)
    assert allowed is False
    assert "Rate limit exceeded" in error
    assert int(headers["Retry-After"]) == 60


async def test_throttle_zone_allows_with_warning_headers(limiter):
    for _ in range(LIMIT - 1):
        await _hit(limiter)
    allowed, headers, error = await _hit(limiter)  # request 10: in the last 10%
    assert allowed is True and error is None
    assert "Retry-After" not in headers
    assert headers.get("X-RateLimit-IP-Warning")


async def test_blocked_requests_are_not_added_to_the_window(limiter):
    for _ in range(LIMIT):
        await _hit(limiter)
    key = limiter._make_key(RateLimitTier.IP, "203.0.113.7", "/api/v1/calls/live")
    before = await limiter._redis.zcard(key)
    for _ in range(5):
        allowed, _, _ = await _hit(limiter)
        assert allowed is False
    assert await limiter._redis.zcard(key) == before == LIMIT
