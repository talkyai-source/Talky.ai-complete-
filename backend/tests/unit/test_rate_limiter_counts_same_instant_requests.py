"""Requests that arrive at the same clock instant are each counted.

Bug hunt 2026-10-12 (found while verifying auth-sessions-4): the sliding window
stored each request as ``f"{now}:{sha256(str(now))[:8]}"``, a member derived
only from the timestamp. Requests with the same time.time() value produced the
same member, the sorted set de-duplicated them, and the count never rose: a
burst inside one clock tick (about 15 ms on Windows, one microsecond on Linux
under concurrency) was never limited.
"""
from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.core.security import api_security
from app.core.security.api_security import APIRateLimiter, RateLimitAction, RateLimitTier


@pytest.mark.asyncio
async def test_a_burst_at_one_instant_is_still_limited(monkeypatch):
    monkeypatch.setattr(api_security.time, "time", lambda: 1_760_000_000.0)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    limiter = APIRateLimiter(redis_client=redis)
    limiter.update_config(RateLimitTier.IP, requests=5, window=60, block_duration=60)
    try:
        actions = [(await limiter.check_rate_limit(RateLimitTier.IP, "203.0.113.9"))[0] for _ in range(6)]
    finally:
        await redis.aclose()
    assert actions[:5].count(RateLimitAction.BLOCK) == 0
    assert actions[5] == RateLimitAction.BLOCK
