"""
Day 6 – API Rate Limiting (Tiered).

Tests cover:
  ✓ RateLimitConfig defaults
  ✓ APIRateLimiter key generation
  ✓ Fail-open when Redis unavailable
  ✓ Rate limit check with mocked Redis (allow, throttle, block)
  ✓ check_all_tiers aggregation
  ✓ Config update
  ✓ Singleton management (get/reset)
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.security.api_security import (
    DEFAULT_LIMITS,
    APIRateLimiter,
    RateLimitAction,
    RateLimitConfig,
    RateLimitTier,
    get_api_rate_limiter,
    reset_api_rate_limiter,
)


# ========================================================================
# Default Configuration
# ========================================================================


class TestDefaultLimits:
    """Verify default rate limit configurations."""

    def test_ip_limit_defaults(self):
        config = DEFAULT_LIMITS[RateLimitTier.IP]
        assert config.requests == 100
        assert config.window == 60

    def test_user_limit_defaults(self):
        config = DEFAULT_LIMITS[RateLimitTier.USER]
        assert config.requests == 1000

    def test_tenant_limit_defaults(self):
        config = DEFAULT_LIMITS[RateLimitTier.TENANT]
        assert config.requests == 10000

    def test_global_limit_defaults(self):
        config = DEFAULT_LIMITS[RateLimitTier.GLOBAL]
        assert config.requests == 100000

    def test_all_tiers_have_config(self):
        for tier in RateLimitTier:
            assert tier in DEFAULT_LIMITS


# ========================================================================
# APIRateLimiter
# ========================================================================


class TestAPIRateLimiter:
    """APIRateLimiter unit tests."""

    def test_key_generation(self):
        limiter = APIRateLimiter()
        key = limiter._make_key(RateLimitTier.IP, "192.168.1.1")
        assert key.startswith("ratelimit:ip:192.168.1.1")

    def test_key_with_endpoint_includes_hash(self):
        limiter = APIRateLimiter()
        key = limiter._make_key(RateLimitTier.IP, "192.168.1.1", endpoint="/api/v1/test")
        assert "ratelimit:ip:192.168.1.1:" in key
        # Endpoint is hashed
        assert len(key.split(":")[-1]) == 16

    def test_block_key_generation(self):
        limiter = APIRateLimiter()
        key = limiter._block_key(RateLimitTier.USER, "user-123")
        assert key == "ratelimit:block:user:user-123"

    @pytest.mark.asyncio
    async def test_fail_open_without_redis(self):
        """When Redis is unavailable, allow all requests."""
        limiter = APIRateLimiter(redis_client=None)
        action, meta = await limiter.check_rate_limit(
            RateLimitTier.IP, "192.168.1.1"
        )
        assert action == RateLimitAction.ALLOW
        assert meta["reason"] == "redis_unavailable"

    @pytest.mark.asyncio
    async def test_allow_under_limit(self, mock_redis):
        """Requests under the limit should be allowed."""
        mock_redis.exists.return_value = 0  # Not blocked
        mock_redis.zcard.return_value = 5  # 5 requests (under 100 limit)
        limiter = APIRateLimiter(redis_client=mock_redis)
        action, meta = await limiter.check_rate_limit(
            RateLimitTier.IP, "192.168.1.1"
        )
        assert action == RateLimitAction.ALLOW
        assert meta["remaining"] > 0

    @pytest.mark.asyncio
    async def test_block_when_over_limit(self, mock_redis):
        """Requests over the limit should be blocked."""
        mock_redis.exists.return_value = 0
        mock_redis.zcard.return_value = 100  # At limit
        limiter = APIRateLimiter(redis_client=mock_redis)
        action, meta = await limiter.check_rate_limit(
            RateLimitTier.IP, "192.168.1.1"
        )
        assert action == RateLimitAction.BLOCK
        assert "retry_after" in meta

    @pytest.mark.asyncio
    async def test_throttle_near_limit(self, mock_redis):
        """Requests within 10% of limit should be throttled."""
        mock_redis.exists.return_value = 0
        mock_redis.zcard.return_value = 91  # 91% of 100
        limiter = APIRateLimiter(redis_client=mock_redis)
        action, meta = await limiter.check_rate_limit(
            RateLimitTier.IP, "192.168.1.1"
        )
        assert action == RateLimitAction.THROTTLE

    @pytest.mark.asyncio
    async def test_blocked_scope_returns_block(self, mock_redis):
        """If scope is already blocked, return BLOCK immediately."""
        mock_redis.exists.return_value = 1  # Blocked
        mock_redis.ttl.return_value = 120
        limiter = APIRateLimiter(redis_client=mock_redis)
        action, meta = await limiter.check_rate_limit(
            RateLimitTier.IP, "192.168.1.1"
        )
        assert action == RateLimitAction.BLOCK
        assert meta["retry_after"] == 120

    def test_update_config(self):
        limiter = APIRateLimiter()
        limiter.update_config(RateLimitTier.IP, requests=200, window=120, block_duration=600)
        assert limiter._configs[RateLimitTier.IP].requests == 200
        assert limiter._configs[RateLimitTier.IP].window == 120


# ========================================================================
# check_all_tiers
# ========================================================================


class TestCheckAllTiers:
    """check_all_tiers() tests."""

    @pytest.mark.asyncio
    async def test_all_allowed(self, mock_redis):
        mock_redis.exists.return_value = 0
        mock_redis.zcard.return_value = 5
        limiter = APIRateLimiter(redis_client=mock_redis)
        allowed, headers, error = await limiter.check_all_tiers(
            ip="192.168.1.1",
            user_id="user-123",
            tenant_id="tenant-456",
        )
        assert allowed is True
        assert error is None
        assert "X-RateLimit-IP-Limit" in headers

    @pytest.mark.asyncio
    async def test_fail_open_without_redis(self):
        limiter = APIRateLimiter(redis_client=None)
        allowed, headers, error = await limiter.check_all_tiers(
            ip="192.168.1.1",
            user_id=None,
            tenant_id=None,
        )
        assert allowed is True


# ========================================================================
# Singleton Management
# ========================================================================


class TestSingleton:
    """get_api_rate_limiter / reset tests."""

    def test_get_creates_singleton(self):
        reset_api_rate_limiter()
        limiter = get_api_rate_limiter()
        assert isinstance(limiter, APIRateLimiter)

    def test_reset_clears_singleton(self):
        reset_api_rate_limiter()
        l1 = get_api_rate_limiter()
        reset_api_rate_limiter()
        l2 = get_api_rate_limiter()
        assert l1 is not l2

    def teardown_method(self):
        reset_api_rate_limiter()
