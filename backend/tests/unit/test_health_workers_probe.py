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
    # The registry now covers all three workers (dialer/voice/reminder), so
    # "healthy" requires a fresh heartbeat from each — see
    # test_all_three_workers_healthy_returns_200 below for the dedicated
    # multi-worker case.
    now = str(time.time())
    store = {
        "dialer:heartbeat_ts": now,
        "voice:heartbeat_ts": now,
        "reminder:heartbeat_ts": now,
    }
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
    # All three workers need a fresh heartbeat for the overall probe to be
    # healthy (see note in test_workers_healthy_when_heartbeat_fresh).
    raw = str(time.time()).encode()
    store = {
        "dialer:heartbeat_ts": raw,
        "voice:heartbeat_ts": raw,
        "reminder:heartbeat_ts": raw,
    }
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


# ---------------------------------------------------------------------------
# Three-worker registry (dialer + voice + reminder)
# ---------------------------------------------------------------------------

def test_registry_includes_voice_and_reminder():
    """The registry health.py exposes must list all three workers with the
    exact heartbeat keys each worker writes (see DialerWorker/
    VoicePipelineWorker/ReminderWorker HEARTBEAT_REDIS_KEY)."""
    assert health._WORKER_HEARTBEAT_KEYS == {
        "dialer": "dialer:heartbeat_ts",
        "voice": "voice:heartbeat_ts",
        "reminder": "reminder:heartbeat_ts",
    }


async def test_all_three_workers_healthy_returns_200(monkeypatch):
    now = time.time()
    store = {
        "dialer:heartbeat_ts": str(now),
        "voice:heartbeat_ts": str(now),
        "reminder:heartbeat_ts": str(now),
    }
    _patch_container(monkeypatch, _make_container(redis=_FakeRedisKV(store)))

    resp = Response()
    result = await health.workers_health_probe(resp)

    assert result["healthy"] is True
    assert resp.status_code != 503
    names = {w["name"] for w in result["workers"]}
    assert names == {"dialer", "voice", "reminder"}
    assert all(w["healthy"] for w in result["workers"])


async def test_one_stale_worker_flags_503_and_names_it(monkeypatch):
    now = time.time()
    store = {
        "dialer:heartbeat_ts": str(now),
        "voice:heartbeat_ts": str(now - 500),  # stale
        "reminder:heartbeat_ts": str(now),
    }
    _patch_container(monkeypatch, _make_container(redis=_FakeRedisKV(store)))

    resp = Response()
    result = await health.workers_health_probe(resp)

    assert result["healthy"] is False
    assert resp.status_code == 503

    by_name = {w["name"]: w for w in result["workers"]}
    assert by_name["voice"]["healthy"] is False
    assert by_name["dialer"]["healthy"] is True
    assert by_name["reminder"]["healthy"] is True
