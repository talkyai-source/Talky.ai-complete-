"""Password recovery remains bounded and can recover from legacy TTL loss."""
import asyncio
from unittest.mock import AsyncMock

import fakeredis
import pytest

from app.api.v1.endpoints.auth import password_reset as reset

EMAIL = "owner@example.com"


@pytest.fixture
async def redis():
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


async def test_legacy_counter_without_expiry_cannot_suppress_recovery_forever(redis):
    key = reset._reset_rate_limit_key(EMAIL)
    # The previous INCR/EXPIRE pair could be interrupted after creating this.
    await redis.set(key, reset._RESET_RATE_LIMIT_MAX)
    assert await redis.ttl(key) == -1
    assert not await reset._check_reset_rate_limit(redis, EMAIL)
    assert 0 < await redis.ttl(key) <= reset._RESET_RATE_LIMIT_WINDOW_SECONDS


async def test_parallel_requests_share_one_bounded_normalized_email_window(redis):
    results = await asyncio.gather(*[
        reset._check_reset_rate_limit(redis, " OWNER@example.com " if i % 2 else EMAIL)
        for i in range(20)
    ])
    assert sum(results) == reset._RESET_RATE_LIMIT_MAX
    assert 0 < await redis.ttl(reset._reset_rate_limit_key(EMAIL)) <= reset._RESET_RATE_LIMIT_WINDOW_SECONDS


async def test_denied_retry_does_not_extend_an_existing_recovery_window(redis):
    key = reset._reset_rate_limit_key(EMAIL)
    await redis.set(key, reset._RESET_RATE_LIMIT_MAX, ex=120)
    before = await redis.pttl(key)
    assert not await reset._check_reset_rate_limit(redis, EMAIL)
    assert 0 < await redis.pttl(key) <= before


async def test_unconfirmed_rate_claim_does_not_authorize_an_email(redis, monkeypatch):
    monkeypatch.setattr(redis, "eval", AsyncMock(side_effect=ConnectionError("synthetic lost claim")))
    monkeypatch.setattr(redis, "incr", AsyncMock(side_effect=ConnectionError("synthetic lost claim")))
    assert not await reset._check_reset_rate_limit(redis, EMAIL)
