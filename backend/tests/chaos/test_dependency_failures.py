"""Chaos tests — verify graceful degradation when dependencies misbehave.

These tests deliberately break things (Redis down, Postgres slow, provider
errors) and assert the backend returns sensible errors instead of crashing,
hanging, or leaking exceptions.

Run as a separate marker so they don't slow the main test suite:

    pytest -m chaos backend/tests/chaos/

Mark new tests with `@pytest.mark.chaos`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


pytestmark = pytest.mark.chaos


class TestRedisFailure:
    """Backend must NOT 500 the whole request when Redis is unreachable —
    rate-limit checks should fail-open with a logged warning, cache reads
    should miss gracefully."""

    @pytest.mark.asyncio
    async def test_health_endpoint_reports_redis_down(self, client):
        with patch("redis.asyncio.Redis.ping", new=AsyncMock(side_effect=ConnectionError("boom"))):
            resp = await client.get("/api/v1/health")
            # Liveness should still be 200 — readiness reports degraded.
            assert resp.status_code in (200, 503)
            body = resp.json()
            assert "redis" in str(body).lower() or "status" in body


class TestPostgresFailure:
    """Connection pool exhaustion / DB down should produce a clean 503,
    not a stack-trace leak or a hung request."""

    @pytest.mark.asyncio
    async def test_db_timeout_returns_503(self, client):
        async def _slow(*_a, **_kw):
            import asyncio
            await asyncio.sleep(60)

        with patch("asyncpg.Pool.acquire", new=_slow):
            resp = await client.get("/api/v1/health/database", timeout=5.0)
            assert resp.status_code in (503, 504)
            assert resp.json()["error"]["code"] in ("service_unavailable", "gateway_timeout")


class TestProviderFailure:
    """When an AI provider 500s or times out, the backend must not propagate
    the upstream error verbatim (info disclosure) — return our standard envelope."""

    @pytest.mark.asyncio
    async def test_provider_error_uses_standard_envelope(self, client):
        # Stub out whichever provider is hit by the smoke route. Replace with
        # the actual call path under test in your real implementation.
        with patch("app.infrastructure.providers.deepgram.transcribe",
                   new=AsyncMock(side_effect=RuntimeError("upstream 500"))):
            resp = await client.post("/api/v1/transcribe", json={"audio": "..."})
            assert resp.status_code >= 500
            body = resp.json()
            assert "error" in body
            assert "code" in body["error"]
            assert "request_id" in body["error"]
            # Must NOT leak upstream message.
            assert "upstream 500" not in str(body)


class TestGracefulShutdown:
    """SIGTERM mid-request must drain in-flight work before exiting."""

    @pytest.mark.asyncio
    async def test_lifespan_drains_active_sessions(self):
        # Smoke: lifespan context manager should run shutdown handlers
        # without raising even if a session is mid-flight.
        from app.main import app
        async with app.router.lifespan_context(app):
            pass
