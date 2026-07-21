"""Worker-liveness probe — GET /api/v1/healthz/workers.

Reads each registered worker's Redis heartbeat timestamp and reports 200 only
when every worker is fresh, 503 if any is stale/missing/unreadable. Container +
Redis are mocked so nothing real is touched (mirrors test_health_deep_probe).
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from fastapi import Response

from app.api.v1.endpoints import health


class _FakeRedisKV:
    def __init__(self, store: dict | None = None, *, fail: bool = False):
        self._store = store or {}
        self._fail = fail

    async def get(self, key):
        if self._fail:
            raise RuntimeError("redis down")
        return self._store.get(key)


def _make_container(*, initialized=True, redis=None):
    return SimpleNamespace(is_initialized=initialized, redis=redis)


def _patch_container(monkeypatch, container):
    monkeypatch.setattr("app.core.container.get_container", lambda: container)


async def test_workers_healthy_when_heartbeat_fresh(monkeypatch):
    store = {"dialer:heartbeat_ts": str(time.time())}
    _patch_container(monkeypatch, _make_container(redis=_FakeRedisKV(store)))

    resp = Response()
    result = await health.workers_health_probe(resp)

    assert result["healthy"] is True
    assert resp.status_code != 503
    dialer = next(w for w in result["workers"] if w["name"] == "dialer")
    assert dialer["healthy"] is True
    assert dialer["age_seconds"] is not None and dialer["age_seconds"] < 180


async def test_workers_unhealthy_when_heartbeat_stale(monkeypatch):
    store = {"dialer:heartbeat_ts": str(time.time() - 500)}
    _patch_container(monkeypatch, _make_container(redis=_FakeRedisKV(store)))

    resp = Response()
    result = await health.workers_health_probe(resp)

    assert result["healthy"] is False
    assert resp.status_code == 503
    dialer = next(w for w in result["workers"] if w["name"] == "dialer")
    assert dialer["healthy"] is False


async def test_workers_unhealthy_when_heartbeat_missing(monkeypatch):
    _patch_container(monkeypatch, _make_container(redis=_FakeRedisKV({})))

    resp = Response()
    result = await health.workers_health_probe(resp)

    assert result["healthy"] is False
    assert resp.status_code == 503
    dialer = next(w for w in result["workers"] if w["name"] == "dialer")
    assert dialer["last_beat_epoch"] is None
    assert dialer["age_seconds"] is None


async def test_workers_unhealthy_when_redis_errors(monkeypatch):
    _patch_container(monkeypatch, _make_container(redis=_FakeRedisKV(fail=True)))

    resp = Response()
    result = await health.workers_health_probe(resp)

    assert result["healthy"] is False
    assert resp.status_code == 503


async def test_workers_accepts_bytes_heartbeat_value(monkeypatch):
    # A Redis client without decode_responses returns bytes — must still parse.
    store = {"dialer:heartbeat_ts": str(time.time()).encode()}
    _patch_container(monkeypatch, _make_container(redis=_FakeRedisKV(store)))

    resp = Response()
    result = await health.workers_health_probe(resp)

    assert result["healthy"] is True


async def test_workers_unhealthy_when_container_uninitialized(monkeypatch):
    _patch_container(monkeypatch, _make_container(initialized=False, redis=None))

    resp = Response()
    result = await health.workers_health_probe(resp)

    assert result["healthy"] is False
    assert resp.status_code == 503
