"""T2.2 — streams-backed dialer queue tests.

Exercises the happy path and the watchdog with an in-process fake
redis.asyncio that understands XADD / XREADGROUP / XACK /
XPENDING_RANGE / XCLAIM / XLEN. The real client is never touched.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest

from app.domain.models.dialer_job import DialerJob, JobStatus
from app.domain.services.streams_queue_service import (
    GROUP_NAME,
    STREAM_NORMAL,
    STREAM_PRIORITY,
    DialerStreamsQueueService,
    _extract_first_job,
    _pending_id,
    _pending_idle,
    resolve_consumer_name,
)


# ──────────────────────────────────────────────────────────────────────────
# Fake redis client
# ──────────────────────────────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self) -> None:
        # stream_name -> list[(entry_id, fields_dict)]
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        # stream_name -> {group_name -> {consumer_name -> [entry_ids]}}
        self.delivered: dict[str, dict[str, dict[str, list[str]]]] = {}
        # stream_name -> {group_name -> cursor_index}
        self.group_cursor: dict[str, dict[str, int]] = {}
        # Simple pending: stream -> group -> [(entry_id, consumer, idle_ms)]
        self.pending: dict[str, dict[str, list[tuple[str, str, int]]]] = {}
        # Simple ZSET support for SCHEDULED_ZSET stub (only zcard).
        self.zcards: dict[str, int] = {}
        self._next_id = 0

    def _new_id(self) -> str:
        self._next_id += 1
        return f"0-{self._next_id}"

    async def xgroup_create(self, *, name, groupname, id, mkstream=False):
        stream = name
        if stream not in self.streams:
            if not mkstream:
                raise RuntimeError("NOSUCHSTREAM")
            self.streams[stream] = []
        groups = self.group_cursor.setdefault(stream, {})
        if groupname in groups:
            raise RuntimeError("BUSYGROUP Consumer Group already exists")
        groups[groupname] = len(self.streams[stream])  # "$" semantics
        self.delivered.setdefault(stream, {})[groupname] = {}
        self.pending.setdefault(stream, {})[groupname] = []

    async def xadd(self, stream: str, fields: dict) -> str:
        self.streams.setdefault(stream, [])
        entry_id = self._new_id()
        self.streams[stream].append((entry_id, dict(fields)))
        return entry_id

    async def xreadgroup(
        self, *, groupname, consumername, streams, count=1, block=0,
    ):
        assert count == 1
        out: list[Any] = []
        for stream, cursor in streams.items():
            assert cursor == ">"
            entries = self.streams.get(stream, [])
            idx = self.group_cursor.get(stream, {}).get(groupname, 0)
            if idx >= len(entries):
                continue
            entry_id, fields = entries[idx]
            self.group_cursor[stream][groupname] = idx + 1
            self.delivered[stream][groupname].setdefault(consumername, []).append(entry_id)
            self.pending[stream][groupname].append((entry_id, consumername, 0))
            out.append([stream, [[entry_id, dict(fields)]]])
            break
        return out

    async def xack(self, stream: str, groupname: str, entry_id: str) -> int:
        pending = self.pending.get(stream, {}).get(groupname, [])
        before = len(pending)
        self.pending[stream][groupname] = [
            p for p in pending if p[0] != entry_id
        ]
        return before - len(self.pending[stream][groupname])

    async def xpending_range(self, *, name, groupname, min, max, count):
        return [
            (entry_id, consumer, idle, 0)
            for entry_id, consumer, idle in self.pending.get(name, {}).get(groupname, [])
        ]

    async def xclaim(self, *, name, groupname, consumername, min_idle_time, message_ids):
        pending = self.pending.get(name, {}).get(groupname, [])
        claimed = []
        new_pending = []
        for entry_id, consumer, idle in pending:
            if entry_id in message_ids and idle >= min_idle_time:
                new_pending.append((entry_id, consumername, 0))
                claimed.append(entry_id)
            else:
                new_pending.append((entry_id, consumer, idle))
        self.pending[name][groupname] = new_pending
        return claimed

    async def xlen(self, stream: str) -> int:
        return len(self.streams.get(stream, []))

    async def zcard(self, key: str) -> int:
        return int(self.zcards.get(key, 0))

    # Test helper — advance a pending entry's idle time.
    def _tick_idle(self, stream: str, group: str, ms: int) -> None:
        self.pending[stream][group] = [
            (eid, c, idle + ms) for eid, c, idle in self.pending.get(stream, {}).get(group, [])
        ]


def _make_job(priority: int = 5, job_id: str = "j1") -> DialerJob:
    return DialerJob(
        job_id=job_id,
        campaign_id="c1",
        lead_id="l1",
        tenant_id="t1",
        phone_number="+15551234567",
        priority=priority,
        status=JobStatus.PENDING,
        scheduled_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )


# ──────────────────────────────────────────────────────────────────────────
# Parsing helpers — pure-functional
# ──────────────────────────────────────────────────────────────────────────

def test_extract_first_job_decodes_bytes():
    payload = json.dumps({"x": 1})
    result = [[b"stream", [[b"0-1", {b"job": payload.encode()}]]]]
    parsed = _extract_first_job(result)
    assert parsed is not None
    entry_id, raw = parsed
    assert entry_id == "0-1"
    assert json.loads(raw) == {"x": 1}


def test_extract_first_job_handles_empty():
    assert _extract_first_job([]) is None
    assert _extract_first_job(None) is None
    assert _extract_first_job([[b"s", []]]) is None


def test_pending_parse_variants():
    assert _pending_id(("0-1", "worker", 12345, 0)) == "0-1"
    assert _pending_id({"message_id": b"0-1"}) == "0-1"
    assert _pending_id(None) is None
    assert _pending_idle(("0-1", "w", 7777, 0)) == 7777
    assert _pending_idle({"time_since_delivered": 42}) == 42


# ──────────────────────────────────────────────────────────────────────────
# Enqueue + dequeue round-trip
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_enqueue_normal_priority_lands_in_normal_stream():
    r = _FakeRedis()
    svc = DialerStreamsQueueService(r)
    await svc.ensure_groups()
    assert await svc.enqueue_job(_make_job(priority=5)) is True
    assert await r.xlen(STREAM_NORMAL) == 1
    assert await r.xlen(STREAM_PRIORITY) == 0


@pytest.mark.asyncio
async def test_enqueue_high_priority_lands_in_priority_stream():
    r = _FakeRedis()
    svc = DialerStreamsQueueService(r)
    await svc.enqueue_job(_make_job(priority=9))
    assert await r.xlen(STREAM_PRIORITY) == 1
    assert await r.xlen(STREAM_NORMAL) == 0


@pytest.mark.asyncio
async def test_dequeue_returns_priority_first():
    r = _FakeRedis()
    svc = DialerStreamsQueueService(r)
    # Production pattern: worker creates group first, then campaigns
    # enqueue. `$` semantics means late-joining workers don't re-
    # process history — they see only new messages.
    await svc.ensure_groups()
    await svc.enqueue_job(_make_job(priority=5, job_id="normal-1"))
    await svc.enqueue_job(_make_job(priority=9, job_id="priority-1"))
    first = await svc.dequeue_job()
    assert first is not None
    assert first.stream == STREAM_PRIORITY
    assert first.job.job_id == "priority-1"
    second = await svc.dequeue_job()
    assert second is not None
    assert second.stream == STREAM_NORMAL
    assert second.job.job_id == "normal-1"


@pytest.mark.asyncio
async def test_ack_removes_from_pending():
    r = _FakeRedis()
    svc = DialerStreamsQueueService(r)
    await svc.ensure_groups()
    await svc.enqueue_job(_make_job(priority=5))
    result = await svc.dequeue_job()
    assert result is not None
    # One message pending for this consumer.
    assert len(r.pending[STREAM_NORMAL][GROUP_NAME]) == 1
    await svc.ack(result.stream, result.entry_id)
    assert len(r.pending[STREAM_NORMAL][GROUP_NAME]) == 0


@pytest.mark.asyncio
async def test_empty_queue_returns_none():
    r = _FakeRedis()
    svc = DialerStreamsQueueService(r)
    result = await svc.dequeue_job()
    assert result is None


# ──────────────────────────────────────────────────────────────────────────
# Reclaim dead-worker jobs
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reclaim_picks_up_idle_entries():
    r = _FakeRedis()
    # Worker A dequeues but never ACKs, then dies.
    svc_a = DialerStreamsQueueService(r)
    svc_a._consumer = "pod-a"
    await svc_a.ensure_groups()
    await svc_a.enqueue_job(_make_job(priority=5, job_id="orphan"))
    dequeued = await svc_a.dequeue_job()
    assert dequeued is not None

    # Simulate 10 minutes of idle time.
    r._tick_idle(STREAM_NORMAL, GROUP_NAME, 10 * 60 * 1000)

    # Worker B joins the group and reclaims.
    svc_b = DialerStreamsQueueService(r)
    svc_b._consumer = "pod-b"
    count = await svc_b.reclaim_stale(idle_ms=5 * 60 * 1000)
    assert count == 1
    # Ownership transferred.
    pending = r.pending[STREAM_NORMAL][GROUP_NAME]
    assert len(pending) == 1
    assert pending[0][1] == "pod-b"


@pytest.mark.asyncio
async def test_reclaim_ignores_fresh_entries():
    r = _FakeRedis()
    svc = DialerStreamsQueueService(r)
    await svc.enqueue_job(_make_job(priority=5))
    await svc.dequeue_job()  # fresh, idle=0
    count = await svc.reclaim_stale(idle_ms=60_000)
    assert count == 0


# ──────────────────────────────────────────────────────────────────────────
# Stats
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_queue_stats():
    r = _FakeRedis()
    svc = DialerStreamsQueueService(r)
    await svc.enqueue_job(_make_job(priority=5))
    await svc.enqueue_job(_make_job(priority=9, job_id="hi-1"))
    await svc.enqueue_job(_make_job(priority=9, job_id="hi-2"))
    r.zcards["dialer:scheduled"] = 4
    stats = await svc.get_queue_stats()
    assert stats == {
        "priority_stream_length": 2,
        "normal_stream_length": 1,
        "scheduled_count": 4,
    }


# ──────────────────────────────────────────────────────────────────────────
# Consumer naming
# ──────────────────────────────────────────────────────────────────────────

def test_consumer_name_prefers_pod_id(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("POD_ID", "pod-7")
    assert resolve_consumer_name() == "pod-7"


def test_consumer_name_falls_back_to_hostname(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("POD_ID", raising=False)
    assert resolve_consumer_name()  # non-empty string


# ──────────────────────────────────────────────────────────────────────────
# ensure_groups handles BUSYGROUP
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_groups_is_idempotent():
    r = _FakeRedis()
    svc = DialerStreamsQueueService(r)
    await svc.ensure_groups()
    svc._groups_ensured = False  # force a second call
    await svc.ensure_groups()  # must not raise on BUSYGROUP
