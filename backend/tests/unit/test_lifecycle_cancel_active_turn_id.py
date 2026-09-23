"""Regression tests for the cancel_active_turn id mismatch
(hangup-vs-inflight-reply, part 12a, 2026-09-23).

PRODUCTION EVIDENCE
--------------------
Calls 8b3176ca and 6aaeb4dd: ``lifecycle._on_call_ended`` and
``_on_transfer_connected`` both called ``pipeline.cancel_active_turn(call_id)``
with the ARI/PBX channel id the adapter callback hands them (e.g.
``talky-out-8b`` / ``1790168826.2670``), matching ``calls.external_call_uuid``.
But ``VoicePipelineService._pending_llm_tasks`` is keyed by the pipeline
session's OWN call_id (``transcript_handler.py``'s ``call_id =
session.call_id``, the CallSession UUID that tags every other pipeline log
line for the call). ``cancel_active_turn``'s ``self._pending_llm_tasks.pop(
call_id, None)`` therefore silently no-ops on the wrong id — the in-flight
turn is never actually cancelled, so a still-generating reply can be
persisted to the transcript AFTER teardown already read the buffer, or a
send_tts_audio failure post-hangup can still be recorded as a normally
spoken line.

These tests exercise the REAL ``_on_call_ended`` / ``_on_transfer_connected``
functions (not a mock of the code under test) with a fake pipeline that
records which id(s) it was asked to cancel, proving the call site now uses
the pipeline session's own call_id.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.telephony import lifecycle


def _fake_container():
    return SimpleNamespace(is_initialized=False, redis=None, db_pool=None, db_client=None)


@pytest.mark.asyncio
async def test_on_call_ended_cancels_the_pipeline_session_call_id(monkeypatch):
    """The teardown cancel must use the pipeline session's own call_id (the
    id _pending_llm_tasks is actually keyed by), not just the PBX/ARI id
    the adapter callback passes as this function's `call_id` parameter."""
    pbx_call_id = "1790168826.2670"  # what the adapter hands _on_call_ended
    pipeline_call_id = "bd40b76f-1111-2222-3333-444444444444"  # session.call_id

    cancel_calls: list[str] = []

    async def _fake_cancel(cid: str) -> None:
        cancel_calls.append(cid)

    voice_session = SimpleNamespace(
        pipeline=SimpleNamespace(cancel_active_turn=_fake_cancel),
        call_session=SimpleNamespace(call_id=pipeline_call_id),
    )

    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(
            clear_first_speaker=lambda _cid: None,
            clear_ringing_started_at=lambda _cid: None,
            pop_ringing_warmup=lambda _cid: None,
            pop_voice_session=lambda _cid: voice_session,
            remove_gateway_sessions_for_call=lambda _cid: None,
        ),
    )
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: SimpleNamespace())
    monkeypatch.setattr(lifecycle, "_save_call_recording", AsyncMock())
    monkeypatch.setattr(
        lifecycle, "_get_orchestrator", lambda: SimpleNamespace(end_session=AsyncMock())
    )
    monkeypatch.setattr("app.core.container.get_container", _fake_container)

    result = await lifecycle._on_call_ended(
        pbx_call_id,
        terminal_at_monotonic=100.0,
        acknowledge_ledger=False,
    )

    assert result is True
    assert pipeline_call_id in cancel_calls, (
        "cancel_active_turn must be called with the pipeline session's own "
        f"call_id ({pipeline_call_id!r}); got {cancel_calls!r} — the wrong id "
        "makes _pending_llm_tasks.pop() a silent no-op and the in-flight turn "
        "is never actually cancelled"
    )


@pytest.mark.asyncio
async def test_on_transfer_connected_cancels_the_pipeline_session_call_id(monkeypatch):
    """Same id mismatch, second call site: _on_transfer_connected also used
    the raw PBX call_id instead of the pipeline session's own call_id."""
    pbx_call_id = "talky-out-8b"
    pipeline_call_id = "dd4b3fc1-5555-6666-7777-888888888888"

    cancel_calls: list[str] = []

    async def _fake_cancel(cid: str) -> None:
        cancel_calls.append(cid)

    voice_session = SimpleNamespace(
        pipeline=SimpleNamespace(cancel_active_turn=_fake_cancel),
        call_session=SimpleNamespace(call_id=pipeline_call_id),
    )

    monkeypatch.setattr(
        lifecycle,
        "_state",
        lambda: SimpleNamespace(get_voice_session=lambda _cid: voice_session),
    )
    monkeypatch.setattr(lifecycle, "_save_call_recording", AsyncMock())
    monkeypatch.setattr(
        lifecycle, "_get_orchestrator", lambda: SimpleNamespace(end_session=AsyncMock())
    )

    await lifecycle._on_transfer_connected(pbx_call_id, "target-leg-1")

    assert pipeline_call_id in cancel_calls, (
        "transfer cancel_active_turn must be called with the pipeline "
        f"session's own call_id ({pipeline_call_id!r}); got {cancel_calls!r}"
    )
