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


class _Txn:
    """asyncpg transaction double.

    ``acquire_with_tenant`` opens a transaction before ``SET LOCAL
    app.current_tenant_id``, since SET LOCAL is transaction scoped.
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _MonotonicConn:
    def __init__(self, status: str):
        self.status = status
        self.sql: list[str] = []

    def transaction(self):
        return _Txn()

    async def fetchval(self, *_args, **_kwargs):
        # These cases all model a row that genuinely EXISTS and whose write was
        # rejected by the monotonic CAS guard. record_call_state now re-selects
        # after "UPDATE 0" to separate that real duplicate from a row that is
        # not visible in this tenant context, so the probe must report the row
        # as present — otherwise the test would be asserting the RLS-invisible
        # path instead of the monotonicity path it is named for.
        return 1

    async def execute(self, sql: str, *args):
        # acquire_with_tenant issues `SET LOCAL app.current_tenant_id = '...'`
        # on the same connection with no bind parameters, so this double has to
        # accept a one-argument call as well as the parameterised UPDATE.
        if not args:
            return "SET"
        new_status, _call_id = args[0], args[1]
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
        tenant_id="11111111-1111-4111-8111-111111111111",
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
        tenant_id="11111111-1111-4111-8111-111111111111",
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
            tenant_id="11111111-1111-4111-8111-111111111111",
            new_state=state,
        )

    assert conn.status == "ended"
    assert emitted == ["ended"]
