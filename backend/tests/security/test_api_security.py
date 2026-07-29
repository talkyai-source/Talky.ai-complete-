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
        # Raised from 100 on 2026-07-28: a single dashboard user measured
        # 76-225 req/min in production (it polls /calls/live every ~2s), so the
        # old cap rejected a THIRD of one user's traffic and the frontend
        # rendered it as "service down".
        assert config.requests == 600
        assert config.window == 60
        # Short block by design — the sliding window already throttles. The
        # previous 300s block turned a one-request overshoot into a five-minute
        # blackout for everyone sharing that IP.
        assert config.block_duration == 60

    def test_ip_limits_are_env_tunable(self, monkeypatch):
        """Operators must be able to retune this without a code deploy — the
        previous hardcoded value caused a user-visible outage.

        Calls the builder directly rather than reloading the module: a reload
        would rebuild the limiter singleton and break TestSingleton.
        """
        from app.core.security.api_security import build_ip_limit_config

        monkeypatch.setenv("RATE_LIMIT_IP_REQUESTS", "1234")
        monkeypatch.setenv("RATE_LIMIT_IP_BLOCK_SECONDS", "7")
        cfg = build_ip_limit_config()
        assert cfg.requests == 1234
        assert cfg.block_duration == 7

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
        mock_redis.zcard.return_value = 5  # well under the IP limit
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
        # Derived from config, not hardcoded: this assertion is about
        # "at the limit", not about any particular number.
        mock_redis.zcard.return_value = DEFAULT_LIMITS[RateLimitTier.IP].requests
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
        mock_redis.zcard.return_value = int(
            DEFAULT_LIMITS[RateLimitTier.IP].requests * 0.91
        )  # 91% of the limit
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


class TestMonitoringExemption:
    """Monitoring probes must never be rate limited; app routes must be.

    Regression cover for the 2026-07-28 incident: /api/v1/health was 429'd 51
    times in two hours while healthy, which makes monitoring and the LB report
    a false outage. The first fix used `endswith("/health") or
    endswith("/ready")`, which still missed every other real probe route.
    """

    # Every monitoring route that actually exists, with the file it is
    # defined in. Keep this in sync with the routers, not with the middleware.
    REAL_PROBE_PATHS = [
        "/health",                     # app/api/operational.py:23 (root, Docker)
        "/api/v1/health",              # endpoints/health.py:188
        "/api/v1/health/detailed",     # endpoints/health.py:202
        "/api/v1/healthz/ready",       # endpoints/health.py:24
        "/api/v1/healthz/live",        # endpoints/health.py:44
        "/api/v1/healthz/deep",        # endpoints/health.py:50
        "/api/v1/healthz/workers",     # endpoints/health.py:127
    ]

    # Real application routes that must KEEP their rate limiting.
    REAL_LIMITED_PATHS = [
        "/api/v1/calls/live",                    # endpoints/calls.py:181
        "/api/v1/admin/calls/live",              # endpoints/admin/calls.py:93
        "/api/v1/campaigns/minutes/status",      # endpoints/campaigns.py:866
        "/api/v1/campaigns/abc-123",
        "/api/v1/connectors/status",             # endpoints/connectors.py:330
        "/api/v1/auth/mfa/status",               # endpoints/mfa/status.py:27
        "/api/v1/telephony/sip/quotas/status",   # endpoints/telephony_sip/quotas.py:19
        "/api/v1/sessions/security-status",      # endpoints/sessions.py:298
        # Admin dashboard health reads: authenticated + expensive (DB latency
        # probes, provider fan-out). Not what a load balancer polls.
        "/api/v1/admin/health/workers",          # endpoints/admin/health/workers.py:18
        "/api/v1/admin/health/queues",           # endpoints/admin/health/queues.py:16
        "/api/v1/admin/health/database",         # endpoints/admin/health/database.py:16
        "/api/v1/admin/health/detailed",         # endpoints/admin/health/system.py:20
        "/api/v1/admin/health/incidents",        # endpoints/admin/health/incidents.py:23
        "/api/v1/admin/system-health",           # endpoints/admin/base.py:120
        # Prometheus scrape is token-gated (fails closed) and scraped ~4/min;
        # it needs no namespace exemption.
        "/api/v1/telephony/sip/runtime/metrics/activation",  # telephony_runtime/metrics.py:18
    ]

    @pytest.mark.parametrize("path", REAL_PROBE_PATHS)
    def test_real_probe_routes_are_exempt(self, path):
        from app.core.api_security_middleware import is_monitoring_path

        assert is_monitoring_path(path) is True

    @pytest.mark.parametrize("path", REAL_LIMITED_PATHS)
    def test_application_routes_are_not_exempt(self, path):
        from app.core.api_security_middleware import is_monitoring_path

        assert is_monitoring_path(path) is False

    def test_fixtures_are_disjoint_and_non_empty(self):
        """Guards against a vacuous suite (e.g. an empty parametrize list)."""
        assert len(self.REAL_PROBE_PATHS) >= 7
        assert len(self.REAL_LIMITED_PATHS) >= 10
        assert not set(self.REAL_PROBE_PATHS) & set(self.REAL_LIMITED_PATHS)

    def test_old_suffix_rule_would_have_failed(self):
        """Proves these assertions are not vacuous.

        Under the previous `endswith("/health") or endswith("/ready")` rule
        these four probes were still rate limited. If someone reverts to a
        suffix test, the parametrized cases above go red — this test documents
        exactly which ones and why.
        """
        from app.core.api_security_middleware import is_monitoring_path

        gapped = [
            "/api/v1/healthz/live",
            "/api/v1/healthz/deep",
            "/api/v1/healthz/workers",
            "/api/v1/health/detailed",
        ]
        for path in gapped:
            old_rule = path.endswith("/health") or path.endswith("/ready")
            assert old_rule is False, f"{path} was already covered; bad fixture"
            assert is_monitoring_path(path) is True

    def test_new_health_subroute_is_covered_automatically(self):
        """The whole probe subtree is exempt, so a future sub-route needs no
        middleware change — that is the point of the root-segment rule."""
        from app.core.api_security_middleware import is_monitoring_path

        assert is_monitoring_path("/api/v1/healthz/some-future-probe") is True
        assert is_monitoring_path("/api/v1/health/some/deep/future/probe") is True
        assert is_monitoring_path("/api/v2/healthz/workers") is True

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/healthzz/workers",   # near-miss namespace
            "/api/v1/unhealthy",
            "/api/v1/tenants/health-check-settings",
            "/api/v1/campaigns/health",   # suffix-shaped bait
            "/api/v1",
            "/",
            "",
        ],
    )
    def test_lookalike_paths_are_not_exempt(self, path):
        from app.core.api_security_middleware import is_monitoring_path

        assert is_monitoring_path(path) is False

    def test_trailing_slash_and_case(self):
        from app.core.api_security_middleware import is_monitoring_path

        assert is_monitoring_path("/api/v1/healthz/workers/") is True
        assert is_monitoring_path("/api/v1/HEALTHZ/workers") is True


class TestMonitoringExemptionWiring:
    """End-to-end: the middleware must actually skip the limiter for probes.

    The helper being correct is worthless if dispatch() does not consult it, so
    these drive APISecurityMiddleware.dispatch() with a limiter that denies
    EVERYTHING. A probe must still get 200; an app route must get 429.
    """

    @staticmethod
    def _request(path: str, method: str = "GET"):
        from starlette.requests import Request

        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "root_path": "",
                "query_string": b"",
                "headers": [],
                # Deliberately NOT loopback: loopback and internal-token
                # traffic have their own exemptions, which would mask the
                # health-path rule under test.
                "client": ("203.0.113.9", 51234),
                "server": ("testserver", 80),
            }
        )

    @staticmethod
    def _install_denying_limiter(monkeypatch):
        """Patch the container + limiter so every check_all_tiers() denies."""
        import app.core.container as container_mod
        import app.core.security.api_security as api_security_mod

        class _DenyLimiter:
            def __init__(self):
                self.calls = []

            async def check_all_tiers(self, **kwargs):
                self.calls.append(kwargs)
                return False, {"X-RateLimit-IP-Limit": "600"}, "rate limit exceeded"

        class _Container:
            is_initialized = True
            redis_enabled = True
            redis = object()

        limiter = _DenyLimiter()
        monkeypatch.setattr(container_mod, "get_container", lambda: _Container())
        monkeypatch.setattr(
            api_security_mod, "get_api_rate_limiter", lambda *a, **k: limiter
        )
        return limiter

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", TestMonitoringExemption.REAL_PROBE_PATHS)
    async def test_probe_bypasses_a_denying_limiter(self, path, monkeypatch):
        from starlette.responses import PlainTextResponse

        from app.core.api_security_middleware import APISecurityMiddleware

        limiter = self._install_denying_limiter(monkeypatch)
        mw = APISecurityMiddleware(app=None)

        async def call_next(_request):
            return PlainTextResponse("ok")

        response = await mw.dispatch(self._request(path), call_next)
        assert response.status_code == 200, f"{path} was rate limited"
        assert limiter.calls == [], f"limiter was consulted for {path}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/calls/live",
            "/api/v1/campaigns/minutes/status",
            "/api/v1/admin/health/workers",
            "/api/v1/admin/system-health",
        ],
    )
    async def test_application_route_is_still_limited(self, path, monkeypatch):
        """Non-vacuity proof for the test above: with the SAME denying limiter,
        these paths get 429. So the 200s above come from the exemption, not
        from a limiter that never denies."""
        from starlette.responses import PlainTextResponse

        from app.core.api_security_middleware import APISecurityMiddleware

        limiter = self._install_denying_limiter(monkeypatch)
        mw = APISecurityMiddleware(app=None)

        async def call_next(_request):
            return PlainTextResponse("ok")

        response = await mw.dispatch(self._request(path), call_next)
        assert response.status_code == 429, f"{path} escaped rate limiting"
        assert len(limiter.calls) == 1

    @pytest.mark.asyncio
    async def test_write_method_on_a_probe_path_is_still_limited(self, monkeypatch):
        """Every probe is a GET. A POST to a health path can only ever earn a
        405, so it must not buy a free pass through the limiter."""
        from starlette.responses import PlainTextResponse

        from app.core.api_security_middleware import APISecurityMiddleware

        self._install_denying_limiter(monkeypatch)
        mw = APISecurityMiddleware(app=None)

        async def call_next(_request):
            return PlainTextResponse("ok")

        response = await mw.dispatch(
            self._request("/api/v1/healthz/workers", method="POST"), call_next
        )
        assert response.status_code == 429


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
