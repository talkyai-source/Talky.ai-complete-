"""Voice worker liveness heartbeat.

Mirrors tests/unit/test_dialer_worker_heartbeat.py: the heartbeat loop must
(a) write the Redis heartbeat key the health API watches, and (b) survive a
Redis outage without raising (a dead Redis must not kill the loop that also
pets the systemd watchdog). NOTIFY_SOCKET is left unset so the sd_notify
layer is a silent no-op here.
"""
from __future__ import annotations

import asyncio

import pytest

from app.workers.voice_worker import VoicePipelineWorker


def _bare_worker(redis):
    """A VoicePipelineWorker with only the attrs _heartbeat touches — avoids
    the real constructor's provider/session wiring... except _voice_config,
    which the real constructor loads cheaply (no I/O) and _heartbeat reads
    for the interval, so we build via the real __init__."""
    w = VoicePipelineWorker()
    w.running = True
    w._redis = redis
    return w


class _FakeRedis:
    def __init__(self, *, fail: bool = False):
        self._fail = fail
        self.calls: list[tuple] = []

    async def setex(self, key, ttl, value):
        self.calls.append((key, ttl, value))
        if self._fail:
            raise RuntimeError("redis down")


async def _run_one_tick(worker, monkeypatch):
    # Make the loop exit after its single sleep instead of waiting the full
    # configured interval.
    real_sleep = asyncio.sleep

    async def _fast_sleep(_seconds):
        worker.running = False
        await real_sleep(0)

    monkeypatch.setattr(
        "app.workers.voice_worker.asyncio.sleep", _fast_sleep
    )
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    await asyncio.wait_for(worker._heartbeat(), timeout=2.0)


async def test_heartbeat_writes_setex(monkeypatch):
    redis = _FakeRedis()
    worker = _bare_worker(redis)

    await _run_one_tick(worker, monkeypatch)

    assert len(redis.calls) == 1
    key, ttl, value = redis.calls[0]
    assert key == VoicePipelineWorker.HEARTBEAT_REDIS_KEY == "voice:heartbeat_ts"
    assert ttl == VoicePipelineWorker.HEARTBEAT_TTL == 180
    assert float(value) > 0


async def test_heartbeat_survives_redis_failure(monkeypatch):
    redis = _FakeRedis(fail=True)
    worker = _bare_worker(redis)

    # Must NOT raise even though setex throws.
    await _run_one_tick(worker, monkeypatch)

    assert len(redis.calls) == 1  # it tried


async def test_heartbeat_survives_missing_redis(monkeypatch):
    worker = _bare_worker(redis=None)

    # No redis at all — still no raise.
    await _run_one_tick(worker, monkeypatch)
