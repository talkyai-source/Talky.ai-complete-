"""The dialler's health signal must be true.

A failing active-accounts read must not look like "no campaign running", one
failing reaper must not stop the others, and the Redis heartbeat key must stop
being written once the worker has failed to read its work five times running.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from app.workers.dialer_worker import DialerWorker

LOGGER = "app.workers.dialer_worker"
_REAL_SLEEP = asyncio.sleep


class _FakeRedis:
    def __init__(self):
        self.calls: list[tuple] = []

    async def setex(self, key, ttl, value):
        self.calls.append((key, ttl, value))


class _Conn:
    def __init__(self, fail=False):
        self.fail = fail

    async def fetch(self, *_a, **_k):
        if self.fail:
            raise RuntimeError("permission denied")
        return [{"tenant_id": "t1"}]


def _worker(redis=None, fail_db=False):
    w = DialerWorker.__new__(DialerWorker)
    w.running = True
    w._jobs_processed = 0
    w._jobs_failed = 0
    w._redis = redis

    @contextlib.asynccontextmanager
    async def _acq():
        yield _Conn(fail=w._fail_db)

    w._fail_db = fail_db
    w._acquire_db = _acq
    return w


async def _tick(worker, monkeypatch, notify_calls=None):
    async def _fast_sleep(_s):
        worker.running = False
        await _REAL_SLEEP(0)

    monkeypatch.setattr("app.workers.dialer_worker.asyncio.sleep", _fast_sleep)
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    if notify_calls is not None:
        from app.core import sd_notify

        monkeypatch.setattr(
            sd_notify.SystemdNotifier,
            "notify_watchdog",
            lambda self: notify_calls.append("WATCHDOG"),
        )
    worker.running = True
    await asyncio.wait_for(worker._heartbeat(), timeout=2.0)


def _msgs(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


async def test_failing_reaper_does_not_stop_the_others(monkeypatch, caplog):
    from app.domain.services.dialer import stuck_job_reaper as sjr

    ran = []

    async def boom(_c):
        ran.append("jobs")
        raise ValueError("bad row")

    async def calls(_c):
        ran.append("calls")

    async def sched(_c):
        ran.append("sched")

    monkeypatch.setattr(sjr, "reap_stuck_jobs", boom)
    monkeypatch.setattr(sjr, "reap_stuck_calls", calls)
    monkeypatch.setattr(sjr, "reap_orphaned_scheduled_jobs", sched)
    w = _worker()

    class _Q:
        async def reap_stale_processing(self):
            ran.append("zset")

    w.queue_service = _Q()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await w._reap_stuck_jobs_tick()
    assert ran == ["jobs", "calls", "sched", "zset"]
    assert "reaper_failed name=reap_stuck_jobs error=ValueError" in _msgs(caplog)


async def test_five_failed_reads_withhold_the_key(monkeypatch, caplog):
    redis = _FakeRedis()
    w = _worker(redis, fail_db=True)
    notes: list[str] = []
    for _ in range(5):
        assert await w._get_active_tenant_ids() == []
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await _tick(w, monkeypatch, notes)
    assert redis.calls == []
    assert "dialer_heartbeat_withheld consecutive_failures=5" in _msgs(caplog)
    assert notes == ["WATCHDOG"]  # the systemd watchdog is still petted


async def test_four_failed_reads_still_write(monkeypatch, caplog):
    redis = _FakeRedis()
    w = _worker(redis, fail_db=True)
    for _ in range(4):
        await w._get_active_tenant_ids()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await _tick(w, monkeypatch)
    assert len(redis.calls) == 1
    assert not any("withheld" in m for m in _msgs(caplog))


async def test_failure_log_line_carries_count_and_type(caplog):
    w = _worker(_FakeRedis(), fail_db=True)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await w._get_active_tenant_ids()
        await w._get_active_tenant_ids()
    m = _msgs(caplog)
    assert "dialer_active_tenants_read_failed consecutive=1 error=RuntimeError" in m
    assert "dialer_active_tenants_read_failed consecutive=2 error=RuntimeError" in m


async def test_success_resumes_the_heartbeat_once(monkeypatch, caplog):
    redis = _FakeRedis()
    w = _worker(redis, fail_db=True)
    for _ in range(5):
        await w._get_active_tenant_ids()
    await _tick(w, monkeypatch)
    assert redis.calls == []
    w._fail_db = False
    assert await w._get_active_tenant_ids() == ["t1"]
    assert w._active_tenants_failures == 0
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await _tick(w, monkeypatch)
        await _tick(w, monkeypatch)
    assert len(redis.calls) == 2
    assert _msgs(caplog).count("dialer_heartbeat_resumed") == 1


async def test_healthy_loop_writes_same_key_and_expiry(monkeypatch):
    redis = _FakeRedis()
    w = _worker(redis)
    await w._get_active_tenant_ids()
    await _tick(w, monkeypatch)
    assert len(redis.calls) == 1
    key, ttl, value = redis.calls[0]
    assert key == "dialer:heartbeat_ts"
    assert ttl == 180
    assert float(value) > 0


async def test_a_good_read_with_no_running_campaign_resets_the_count(monkeypatch):
    """No campaign running is a GOOD read. It must not look like a failure, or the
    health check would go red on every quiet day."""
    redis = _FakeRedis()
    w = _worker(redis, fail_db=True)
    for _ in range(5):
        await w._get_active_tenant_ids()
    assert w._active_tenants_failures == 5

    class _NoRows:
        async def fetch(self, *_a, **_k):
            return []

    @contextlib.asynccontextmanager
    async def _acq():
        yield _NoRows()

    w._acquire_db = _acq
    assert await w._get_active_tenant_ids() == []
    assert w._active_tenants_failures == 0
    await _tick(w, monkeypatch)
    assert [c[0] for c in redis.calls] == ["dialer:heartbeat_ts"]


async def test_a_reaper_that_cannot_get_a_connection_does_not_stop_the_others(monkeypatch, caplog):
    from app.domain.services.dialer import stuck_job_reaper as sjr

    ran = []

    async def jobs(_c):
        ran.append("jobs")

    async def calls(_c):
        ran.append("calls")

    async def sched(_c):
        ran.append("sched")

    monkeypatch.setattr(sjr, "reap_stuck_jobs", jobs)
    monkeypatch.setattr(sjr, "reap_stuck_calls", calls)
    monkeypatch.setattr(sjr, "reap_orphaned_scheduled_jobs", sched)
    w = _worker()
    acquisitions = []

    @contextlib.asynccontextmanager
    async def _acq():
        acquisitions.append(1)
        if len(acquisitions) == 1:
            raise TimeoutError("pool exhausted")
        yield _Conn()

    w._acquire_db = _acq

    class _Q:
        async def reap_stale_processing(self):
            ran.append("zset")

    w.queue_service = _Q()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await w._reap_stuck_jobs_tick()
    assert ran == ["calls", "sched", "zset"] and len(acquisitions) == 3
    assert "reaper_failed name=reap_stuck_jobs error=TimeoutError" in _msgs(caplog)
