"""Unit tests for the pooled/async TranscriptService.flush_to_database.

These lock in the two fixes made 2026-07:

  1. The per-turn flush runs over a POOLED asyncpg connection (via
     acquire_with_tenant) instead of the blocking postgres_adapter path
     (db_client.table(...).execute() → thread-pool .result() + a fresh
     unpooled asyncpg.connect per turn that stalled every concurrent call).

  2. The UPDATE targets the dialer's REAL calls.id (``target_call_id``) for
     outbound campaign calls, not the voice-session UUID (``call_id``) — the
     latter matches no ``calls`` row, so outbound transcripts silently never
     persisted incrementally.

Both tests fail on the pre-fix code: ``flush_to_database`` had neither the
``target_call_id`` nor the ``db_pool`` keyword, and it drove the write through
the adapter rather than the pool a test can observe.
"""
from __future__ import annotations

import pytest

from app.domain.services.transcript_service import TranscriptService


class _AsyncCM:
    def __init__(self, target):
        self._target = target

    async def __aenter__(self):
        return self._target

    async def __aexit__(self, *args):
        return False


class _FakeConn:
    """Records every statement acquire_with_tenant + the flush execute."""

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    def transaction(self):
        return _AsyncCM(None)

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetchrow(self, sql, *args):
        self.executed.append((sql, args))
        return {"id": "new-transcript-id"}


class _FakePool:
    def __init__(self, conn):
        self._conn = conn
        self.acquire_calls = 0

    def acquire(self, *args, **kwargs):
        self.acquire_calls += 1
        return _AsyncCM(self._conn)


def _calls_updates(conn: _FakeConn) -> list[tuple[str, tuple]]:
    return [
        (sql, args)
        for sql, args in conn.executed
        if sql.strip().upper().startswith("UPDATE CALLS")
    ]


@pytest.mark.asyncio
async def test_flush_targets_dialer_calls_id_not_session_id():
    svc = TranscriptService()
    svc.clear_all_buffers()
    svc.accumulate_turn("session-uuid", "user", "Hello there")

    conn = _FakeConn()
    pool = _FakePool(conn)

    await svc.flush_to_database(
        call_id="session-uuid",
        db_pool=pool,
        target_call_id="dialer-calls-id",
    )

    updates = _calls_updates(conn)
    assert len(updates) == 1, "expected exactly one UPDATE calls"
    _sql, args = updates[0]
    # The WHERE id bind is the last positional arg — it MUST be the dialer's
    # calls.id, never the voice-session UUID (the old, zero-row target).
    assert args[-1] == "dialer-calls-id"
    assert "session-uuid" not in args
    # Transcript text made it into the payload.
    assert any("Hello there" in str(a) for a in args)
    # Proves the pooled connection was leased — not the blocking adapter.
    assert pool.acquire_calls == 1


@pytest.mark.asyncio
async def test_flush_falls_back_to_call_id_when_no_target():
    svc = TranscriptService()
    svc.clear_all_buffers()
    svc.accumulate_turn("session-uuid", "assistant", "Hi!")

    conn = _FakeConn()
    pool = _FakePool(conn)

    # No target_call_id → browser / ask_ai / standalone semantics: the row id
    # is the session's own call_id (calls.id == call_id for those flows).
    await svc.flush_to_database(call_id="session-uuid", db_pool=pool)

    updates = _calls_updates(conn)
    assert len(updates) == 1
    _sql, args = updates[0]
    assert args[-1] == "session-uuid"


@pytest.mark.asyncio
async def test_flush_never_touches_blocking_adapter():
    svc = TranscriptService()
    svc.clear_all_buffers()
    svc.accumulate_turn("s", "user", "hey")

    class _BoomAdapter:
        def table(self, *args, **kwargs):
            raise AssertionError("blocking postgres_adapter path was used")

    conn = _FakeConn()
    pool = _FakePool(conn)

    # db_client is supplied but db_pool is preferred; .table() must never run.
    await svc.flush_to_database(
        call_id="s",
        db_client=_BoomAdapter(),
        db_pool=pool,
        target_call_id="t",
    )

    assert _calls_updates(conn), "flush must have issued the UPDATE via the pool"
