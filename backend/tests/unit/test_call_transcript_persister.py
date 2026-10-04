"""Unit tests for app.services.scripts.call_transcript_persister.

These tests isolate the helper from FastAPI, asyncpg, and the real
TranscriptService. They exercise three invariants:

  1. bind_telephony_call stashes dialer ids on voice_session WITHOUT touching
     voice_session.call_id (provider connection keys depend on that).
  2. Missing-dialer-row and lookup-failure paths never raise.
  3. save_call_transcript_on_hangup reads the buffer, writes via asyncpg,
     and reports success only after an acknowledged save; failed evidence remains.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.scripts.call_transcript_persister import (
    bind_telephony_call,
    save_call_transcript_on_hangup,
)


def _voice_session(call_id: str = "voice-session-uuid") -> SimpleNamespace:
    call_session = SimpleNamespace(
        call_id=call_id,
        talklee_call_id="tlk_abc",
        tenant_id=None,
    )
    return SimpleNamespace(call_id=call_id, call_session=call_session)


class _AsyncCM:
    """Minimal async-context-manager helper used by both pool.acquire()
    and conn.transaction() fakes."""

    def __init__(self, target):
        self._target = target

    async def __aenter__(self):
        return self._target

    async def __aexit__(self, *args):
        return None


def _make_fake_conn(fetchrow_value: Any = None, execute_side_effect=None) -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=execute_side_effect)
    conn.fetchrow = AsyncMock(return_value=fetchrow_value)
    # `async with conn.transaction(): ...` — wraps the SET LOCAL +
    # UPDATE/INSERT statements that need RLS bypass scoped to one txn.
    conn.transaction = MagicMock(return_value=_AsyncCM(None))
    return conn


def _db_client_with_row(
    internal_call_id: str = "00000000-0000-0000-0000-00000000000a",
    tenant_id: str = "00000000-0000-0000-0000-0000000000b1",
    campaign_id: str = "00000000-0000-0000-0000-0000000000c1",
) -> MagicMock:
    """Fake postgres-adapter Client whose .pool.acquire() yields a
    connection that returns the canned row from fetchrow() — matches the
    asyncpg surface bind_telephony_call now uses (with RLS bypass)."""
    conn = _make_fake_conn(
        fetchrow_value={
            "id": internal_call_id,
            "tenant_id": tenant_id,
            "campaign_id": campaign_id,
        }
    )
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    db = MagicMock()
    db.pool = pool
    return db


def _db_client_empty() -> MagicMock:
    conn = _make_fake_conn(fetchrow_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    db = MagicMock()
    db.pool = pool
    return db


@pytest.mark.asyncio
async def test_bind_is_non_destructive():
    """voice_session.call_id MUST NOT change — STT/TTS/media-gateway
    connection maps were keyed on it during ringing warmup."""
    vs = _voice_session("voice-session-uuid")
    original_call_id = vs.call_id
    original_cs_call_id = vs.call_session.call_id

    binding = await bind_telephony_call(
        voice_session=vs,
        pbx_channel_id="asterisk-channel-123",
        db_client=_db_client_with_row("dialer-id", "tenant-1", "camp-1"),
    )

    assert binding is not None
    assert binding.internal_call_id == "dialer-id"
    assert binding.tenant_id == "tenant-1"
    assert binding.campaign_id == "camp-1"

    # Non-destructive: original ids untouched.
    assert vs.call_id == original_call_id
    assert vs.call_session.call_id == original_cs_call_id

    # Dialer ids stashed on the session for later persist.
    assert vs._dialer_call_id == "dialer-id"
    assert vs._dialer_tenant_id == "tenant-1"
    assert vs._dialer_campaign_id == "camp-1"


@pytest.mark.asyncio
async def test_bind_returns_none_when_no_dialer_row():
    vs = _voice_session()
    binding = await bind_telephony_call(
        voice_session=vs,
        pbx_channel_id="asterisk-channel-missing",
        db_client=_db_client_empty(),
    )
    assert binding is None
    assert not hasattr(vs, "_dialer_call_id")


@pytest.mark.asyncio
async def test_bind_swallows_lookup_exception():
    vs = _voice_session()
    # Pool.acquire raises — bind must swallow and return None.
    db = MagicMock()
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=RuntimeError("connection refused"))
    db.pool = pool

    binding = await bind_telephony_call(
        voice_session=vs,
        pbx_channel_id="x",
        db_client=db,
    )
    assert binding is None
    assert not hasattr(vs, "_dialer_call_id")


@pytest.mark.asyncio
async def test_bind_returns_none_when_db_client_has_no_pool():
    vs = _voice_session()
    db = MagicMock(spec=[])  # no .pool attribute
    binding = await bind_telephony_call(
        voice_session=vs,
        pbx_channel_id="x",
        db_client=db,
    )
    assert binding is None
    assert not hasattr(vs, "_dialer_call_id")


@pytest.mark.asyncio
async def test_bind_uses_rls_bypass():
    """Regression: bind_telephony_call MUST set app.bypass_rls = 'true'
    on the connection before the SELECT, otherwise the calls table's
    RLS policy drops every row (no per-request tenant context exists at
    bind time). This is the bug that broke transcript persistence."""
    vs = _voice_session()
    db = _db_client_with_row()

    await bind_telephony_call(
        voice_session=vs,
        pbx_channel_id="asterisk-channel-123",
        db_client=db,
    )

    conn = db.pool.acquire.return_value._target
    executed_sql = [
        call.args[0] for call in conn.execute.await_args_list
        if call.args
    ]
    assert any("bypass_rls" in sql for sql in executed_sql), (
        "bind_telephony_call must set app.bypass_rls before the SELECT — "
        "without it the calls table's RLS policy hides the row and the "
        "persister falls back to its 'non-campaign' branch."
    )


# --- save_call_transcript_on_hangup ------------------------------------------


class _AsyncPoolCM:
    """Minimal async-context-manager wrapper around an asyncpg connection mock."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt, expected", [("saved-row", True), (None, False)])
async def test_hangup_reports_only_acknowledged_save_and_schedules_summary_after_commit(monkeypatch, receipt, expected):
    vs = _voice_session("session-uuid")
    vs._dialer_call_id = "00000000-0000-0000-0000-000000000001"
    vs._dialer_tenant_id = "00000000-0000-0000-0000-0000000000b1"
    svc = MagicMock()
    svc.save_transcript = AsyncMock(return_value=receipt)
    schedule = MagicMock()
    monkeypatch.setattr("app.services.scripts.call_transcript_persister._schedule_call_summary", schedule)
    pool = MagicMock()
    result = await save_call_transcript_on_hangup(
        voice_session=vs, transcript_service=svc, db_pool=pool,
    )
    assert result is expected
    svc.save_transcript.assert_awaited_once_with(
        "session-uuid", db_pool=pool, tenant_id=vs._dialer_tenant_id,
        target_call_id=vs._dialer_call_id,
    )
    assert schedule.called is expected


@pytest.mark.asyncio
async def test_missing_binding_retains_evidence_without_unscoped_write():
    svc = MagicMock()
    svc.save_transcript = AsyncMock()
    result = await save_call_transcript_on_hangup(
        voice_session=_voice_session("session-uuid"), transcript_service=svc, db_pool=MagicMock(),
    )
    assert result is False
    svc.save_transcript.assert_not_awaited()
    svc.retain_failed_finalization.assert_called_once_with("session-uuid")


@pytest.mark.asyncio
async def test_unexpected_final_save_failure_retains_buffer_and_does_not_claim_success():
    vs = _voice_session("session-uuid")
    vs._dialer_call_id = "00000000-0000-0000-0000-000000000001"
    vs._dialer_tenant_id = "00000000-0000-0000-0000-0000000000b1"
    svc = MagicMock()
    svc.save_transcript = AsyncMock(side_effect=RuntimeError("synthetic DB failure"))
    assert await save_call_transcript_on_hangup(
        voice_session=vs, transcript_service=svc, db_pool=MagicMock(),
    ) is False
    svc.clear_buffer.assert_not_called()
    svc.retain_failed_finalization.assert_called_once_with("session-uuid")
