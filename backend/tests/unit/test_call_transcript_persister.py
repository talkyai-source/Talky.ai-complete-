"""Unit tests for app.services.scripts.call_transcript_persister.

These tests isolate the helper from FastAPI, asyncpg, and the real
TranscriptService. They exercise three invariants:

  1. bind_telephony_call stashes dialer ids on voice_session WITHOUT touching
     voice_session.call_id (provider connection keys depend on that).
  2. Missing-dialer-row and lookup-failure paths never raise.
  3. save_call_transcript_on_hangup reads the buffer, writes via asyncpg,
     and clears the buffer even when the write fails.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict
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


def _db_client_with_row(
    internal_call_id: str = "00000000-0000-0000-0000-00000000000a",
    tenant_id: str = "00000000-0000-0000-0000-0000000000b1",
    campaign_id: str = "00000000-0000-0000-0000-0000000000c1",
) -> MagicMock:
    response = MagicMock()
    response.data = [{
        "id": internal_call_id,
        "tenant_id": tenant_id,
        "campaign_id": campaign_id,
    }]
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = response
    db = MagicMock()
    db.table.return_value = chain
    return db


def _db_client_empty() -> MagicMock:
    response = MagicMock()
    response.data = []
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = response
    db = MagicMock()
    db.table.return_value = chain
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
    db = MagicMock()
    db.table.side_effect = RuntimeError("connection refused")

    binding = await bind_telephony_call(
        voice_session=vs,
        pbx_channel_id="x",
        db_client=db,
    )
    assert binding is None
    assert not hasattr(vs, "_dialer_call_id")


# --- save_call_transcript_on_hangup ------------------------------------------


class _AsyncPoolCM:
    """Minimal async-context-manager wrapper around an asyncpg connection mock."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *args):
        return None


def _make_transcript_service(turns, text="User: hi\nAssistant: hello", metrics=None):
    svc = MagicMock()
    svc.get_transcript_json.return_value = turns
    svc.get_transcript_text.return_value = text
    svc.get_metrics.return_value = metrics or {
        "word_count": 3,
        "turn_count": 2,
        "user_word_count": 1,
        "assistant_word_count": 2,
    }
    svc.clear_buffer = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_save_persists_and_clears_buffer():
    dialer_id = "00000000-0000-0000-0000-000000000001"
    tenant_id = "00000000-0000-0000-0000-0000000000b1"

    vs = _voice_session("session-uuid")
    vs._dialer_call_id = dialer_id
    vs._dialer_tenant_id = tenant_id

    svc = _make_transcript_service(turns=[{"role": "user", "content": "hi"}])

    conn = AsyncMock()
    conn.execute = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncPoolCM(conn))

    await save_call_transcript_on_hangup(
        voice_session=vs,
        transcript_service=svc,
        db_pool=pool,
    )

    # Two execute calls: UPDATE calls, INSERT transcripts
    assert conn.execute.await_count == 2
    svc.clear_buffer.assert_called_once_with("session-uuid")


@pytest.mark.asyncio
async def test_save_skips_when_no_dialer_binding():
    vs = _voice_session("session-uuid")  # no _dialer_call_id set
    svc = _make_transcript_service(turns=[{"role": "user", "content": "hi"}])

    pool = MagicMock()
    pool.acquire = MagicMock()

    await save_call_transcript_on_hangup(
        voice_session=vs,
        transcript_service=svc,
        db_pool=pool,
    )

    pool.acquire.assert_not_called()
    svc.clear_buffer.assert_called_once_with("session-uuid")


@pytest.mark.asyncio
async def test_save_skips_when_buffer_empty():
    vs = _voice_session("session-uuid")
    vs._dialer_call_id = "00000000-0000-0000-0000-000000000001"
    svc = _make_transcript_service(turns=[])

    pool = MagicMock()
    pool.acquire = MagicMock()

    await save_call_transcript_on_hangup(
        voice_session=vs,
        transcript_service=svc,
        db_pool=pool,
    )

    pool.acquire.assert_not_called()
    svc.clear_buffer.assert_called_once_with("session-uuid")


@pytest.mark.asyncio
async def test_save_clears_buffer_even_when_db_fails():
    vs = _voice_session("session-uuid")
    vs._dialer_call_id = "00000000-0000-0000-0000-000000000001"
    svc = _make_transcript_service(turns=[{"role": "user", "content": "hi"}])

    conn = AsyncMock()
    conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncPoolCM(conn))

    # Should NOT raise.
    await save_call_transcript_on_hangup(
        voice_session=vs,
        transcript_service=svc,
        db_pool=pool,
    )

    svc.clear_buffer.assert_called_once_with("session-uuid")
