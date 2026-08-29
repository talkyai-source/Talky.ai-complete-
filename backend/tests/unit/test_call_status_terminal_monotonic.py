from __future__ import annotations

import pytest

from app.domain.services import call_status


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


class _MonotonicConn:
    def __init__(self, status: str):
        self.status = status
        self.sql: list[str] = []

    async def execute(self, sql: str, new_status: str, _call_id: str):
        self.sql.append(sql)
        blocked = set(call_status._blocked_predecessor_statuses(call_status.coerce_state(new_status)))
        if self.status == new_status or self.status in blocked:
            return "UPDATE 0"
        self.status = new_status
        return "UPDATE 1"


@pytest.mark.asyncio
@pytest.mark.parametrize("late_state", ["answered", "in_call", "ringing"])
async def test_late_live_event_cannot_regress_ended(monkeypatch, late_state):
    conn = _MonotonicConn("ended")
    emitted: list[str] = []

    async def emit(*_args, **kwargs):
        emitted.append(kwargs["metadata"]["state"])

    from app.domain.services import event_emitter

    monkeypatch.setattr(event_emitter, "emit_event_via_pool", emit)
    await call_status.record_call_state(
        _Pool(conn),
        call_id="call-1",
        tenant_id="tenant-1",
        new_state=late_state,
    )

    assert conn.status == "ended"
    assert emitted == []
    assert "status IS DISTINCT FROM $1" in conn.sql[0]
    assert "ended" in conn.sql[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "late_state",
    ["queued", "dialing", "ringing", "answered", "in_call"],
)
async def test_late_live_event_cannot_escape_termination_pending(
    monkeypatch,
    late_state,
):
    conn = _MonotonicConn("termination_pending")
    emitted: list[str] = []

    async def emit(*_args, **kwargs):
        emitted.append(kwargs["metadata"]["state"])

    from app.domain.services import event_emitter

    monkeypatch.setattr(event_emitter, "emit_event_via_pool", emit)
    await call_status.record_call_state(
        _Pool(conn),
        call_id="call-1",
        tenant_id="tenant-1",
        new_state=late_state,
    )

    assert conn.status == "termination_pending"
    assert emitted == []


@pytest.mark.asyncio
async def test_first_terminal_state_wins_and_duplicate_is_idempotent(monkeypatch):
    conn = _MonotonicConn("in_call")
    emitted: list[str] = []

    async def emit(*_args, **kwargs):
        emitted.append(kwargs["metadata"]["state"])

    from app.domain.services import event_emitter

    monkeypatch.setattr(event_emitter, "emit_event_via_pool", emit)
    pool = _Pool(conn)
    for state in ("ended", "ended", "failed", "answered"):
        await call_status.record_call_state(
            pool,
            call_id="call-1",
            tenant_id="tenant-1",
            new_state=state,
        )

    assert conn.status == "ended"
    assert emitted == ["ended"]
