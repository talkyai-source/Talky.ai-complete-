"""QA F2-16: production never runs on the in-memory Redis fallback.

With Redis unreachable at start-up the API swapped in an in-memory fallback,
reported healthy, and never reconnected: every service had been handed
``None``. Production now retries for a bounded time and then refuses to
start, so systemd restarts it until Redis answers and every service gets a
real client. Development keeps the fallback.
"""
from __future__ import annotations

import pytest

from app.core import container as container_module


class _FakeRedisClient:
    def __init__(self, attempts: list, up_from_attempt: int | None):
        self._attempts = attempts
        self._up_from = up_from_attempt

    async def ping(self):
        self._attempts.append("ping")
        if self._up_from is None or len(self._attempts) < self._up_from:
            raise ConnectionError("Error 111 connecting to 127.0.0.1:6379. Connection refused.")
        return True

    async def close(self):
        return None


def _redis(monkeypatch, *, up_from_attempt=None):
    attempts: list = []
    clients: list = []

    def from_url(url, **kwargs):
        if kwargs.get("socket_timeout") is not None:  # the request-path client pings
            clients.append(_FakeRedisClient(attempts, up_from_attempt))
            return clients[-1]
        return _FakeRedisClient([], 1)  # the pub/sub client is never pinged here

    monkeypatch.setenv("REDIS_MODE", "single")
    monkeypatch.setattr(container_module.redis, "from_url", from_url)
    # Three retries, no waiting: 4 attempts in all.
    monkeypatch.setattr(container_module, "_REDIS_STARTUP_RETRY_DELAYS_S", (0, 0, 0))
    return attempts


@pytest.mark.asyncio
async def test_production_refuses_to_start_on_the_in_memory_fallback(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    attempts = _redis(monkeypatch, up_from_attempt=None)
    svc = container_module.ServiceContainer()
    with pytest.raises(RuntimeError, match="Redis"):
        await svc._initialize_redis_with_retry()
    assert svc.redis is None
    assert len(attempts) == 4  # it retried before refusing


@pytest.mark.asyncio
async def test_production_connects_when_redis_comes_up_during_start(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    attempts = _redis(monkeypatch, up_from_attempt=3)
    svc = container_module.ServiceContainer()
    await svc._initialize_redis_with_retry()
    assert svc.redis is not None
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_development_keeps_the_in_memory_fallback(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    attempts = _redis(monkeypatch, up_from_attempt=None)
    svc = container_module.ServiceContainer()
    await svc._initialize_redis_with_retry()
    assert svc.redis is None
    assert len(attempts) == 1  # no retry loop outside production


def test_startup_uses_the_retrying_redis_initialiser():
    """Guard, not behaviour: startup() goes through the retrying path."""
    import inspect

    source = inspect.getsource(container_module.ServiceContainer.startup)
    assert "await self._initialize_redis_with_retry()" in source
