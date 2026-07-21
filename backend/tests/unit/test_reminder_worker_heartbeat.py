"""Reminder worker liveness heartbeat.

Mirrors tests/unit/test_dialer_worker_heartbeat.py: the heartbeat loop must
(a) write the Redis heartbeat key the health API watches, and (b) survive a
Redis outage without raising. The reminder worker is otherwise DB-only — its
Redis client exists purely for this heartbeat, wired in initialize() with a
best-effort connect (self._redis stays None on failure). NOTIFY_SOCKET is
left unset so the sd_notify layer is a silent no-op here.
"""
from __future__ import annotations

import asyncio

import pytest

from app.workers.reminder_worker import ReminderWorker


def _bare_worker(redis):
    """A ReminderWorker with only the attrs _heartbeat touches — avoids the
    real constructor's DB/service wiring."""
    w = ReminderWorker.__new__(ReminderWorker)
    w.running = True
    w._reminders_sent = 0
    w._reminders_failed = 0
    w._emails_sent = 0
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
    real_sleep = asyncio.sleep

    async def _fast_sleep(_seconds):
        worker.running = False
        await real_sleep(0)

    monkeypatch.setattr(
        "app.workers.reminder_worker.asyncio.sleep", _fast_sleep
    )
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    await asyncio.wait_for(worker._heartbeat(), timeout=2.0)


async def test_heartbeat_writes_setex(monkeypatch):
    redis = _FakeRedis()
    worker = _bare_worker(redis)

    await _run_one_tick(worker, monkeypatch)

    assert len(redis.calls) == 1
    key, ttl, value = redis.calls[0]
    assert key == ReminderWorker.HEARTBEAT_REDIS_KEY == "reminder:heartbeat_ts"
    assert ttl == ReminderWorker.HEARTBEAT_TTL == 180
    assert float(value) > 0


async def test_heartbeat_survives_redis_failure(monkeypatch):
    redis = _FakeRedis(fail=True)
    worker = _bare_worker(redis)

    await _run_one_tick(worker, monkeypatch)

    assert len(redis.calls) == 1  # it tried


async def test_heartbeat_survives_missing_redis(monkeypatch):
    # Mirrors the real worst case: initialize()'s best-effort Redis connect
    # failed, so self._redis is None for the whole process lifetime.
    worker = _bare_worker(redis=None)

    await _run_one_tick(worker, monkeypatch)
