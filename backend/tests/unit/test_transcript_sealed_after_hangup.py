"""A late STT final must not overwrite a transcript the hangup already saved.

Live call 4291700f (2026-09-24 11:18:14): save_call_transcript_on_hangup wrote
34 turns / 399 words; 44 ms later a late final ("Yes. But") started a fresh
buffer and the per-turn flush wrote that ONE line over the whole transcript.
The lead gate then saw caller_turns=1 and refused to mark a qualified lead.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.transcript_service import TranscriptService
from app.services.scripts.call_transcript_persister import save_call_transcript_on_hangup


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _clean():
    TranscriptService.clear_all_buffers()
    yield
    TranscriptService.clear_all_buffers()


def test_a_sealed_call_takes_no_more_turns():
    ts = TranscriptService()
    ts.accumulate_turn("call-s", "user", "Hello")
    ts.seal("call-s")
    ts.accumulate_turn("call-s", "user", "Yes. But")
    assert ts.get_turns("call-s") == []


@pytest.mark.asyncio
async def test_a_late_final_after_the_hangup_save_is_never_flushed(monkeypatch):
    ts = TranscriptService()
    for i in range(34):
        ts.accumulate_turn("sess-1", "user" if i % 2 else "assistant", f"line {i}")

    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"id": "00000000-0000-0000-0000-000000000001", "tenant_id": "t"})
    conn.fetchval = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=_Acquire(None))
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Acquire(conn))
    monkeypatch.setattr(
        "app.services.scripts.call_transcript_persister._schedule_call_summary",
        lambda *a, **k: None,
    )
    vs = SimpleNamespace(
        call_id="sess-1",
        _dialer_call_id="00000000-0000-0000-0000-000000000001",
        call_session=SimpleNamespace(tenant_id="t", talklee_call_id=None),
    )
    await save_call_transcript_on_hangup(voice_session=vs, transcript_service=ts, db_pool=pool)

    # The late final arrives 44 ms after the save…
    ts.accumulate_turn("sess-1", "user", "Yes. But")
    # …and the next per-turn flush must write nothing.
    writes = AsyncMock()
    monkeypatch.setattr(ts, "_write_calls_transcript", writes)
    await ts.flush_to_database(
        call_id="sess-1", db_pool=pool,
        target_call_id="00000000-0000-0000-0000-000000000001",
    )
    writes.assert_not_awaited()
