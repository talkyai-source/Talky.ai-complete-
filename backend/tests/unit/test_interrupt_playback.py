"""The one operation that stops the agent — and proves it stopped.

WHY THIS EXISTS (2026-08-08)
----------------------------
Stopping playback was four independent best-effort steps, each swallowing its
own failure, with no assembled verdict. Python declared the agent stopped when
its own `state` said LISTENING — a claim about a local variable, not about
audio in the caller's ear, which is downstream of the C++ gateway's queue.

Two consequences these tests pin shut:

  1. A gateway interrupt that FAILS must not read as success. The caller is
     still being talked over; that is the loudest failure this system has.
  2. Duplicate StartOfTurn / barge-in events must not re-run the teardown.
     Both arming sites fire for the same utterance (production logs two lines
     per occurrence), and re-running would rotate the utterance id twice and
     re-cancel a task mid-unwind.

The C++ gateway has always returned `dropped_frames` / `interrupted_segments`
(http_server.cpp:1697-1703); Python threw the body away. These tests pin that
the acknowledgement now survives all the way to the caller.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from app.domain.services.voice_pipeline.interrupt import (
    _INTERRUPT_DEDUPE_S,
    InterruptResult,
    interrupt_playback,
)


class _Gateway:
    """Media gateway stub returning the real acknowledgement shape."""

    def __init__(self, *, ok=True, dropped=12, segments=1, attempts=1,
                 raises=False, ack=True):
        self.ok, self.dropped, self.segments = ok, dropped, segments
        self.attempts, self.raises, self.ack = attempts, raises, ack
        self.calls = 0

    async def clear_output_buffer(self, call_id):
        self.calls += 1
        if self.raises:
            raise RuntimeError("gateway unreachable")
        if not self.ack:
            return None
        return {
            "ok": self.ok,
            "local_bytes_discarded": 320,
            "pending_bytes": 3,
            "gateway": {
                "ok": self.ok,
                "dropped_frames": self.dropped,
                "interrupted_segments": self.segments,
                "attempts": self.attempts,
                "utterance_rotated": True,
                "error": None if self.ok else "retry failed",
            },
        }


def _session():
    from app.domain.models.session import CallState

    return types.SimpleNamespace(
        call_id="11111111-2222-3333-4444-555555555555",
        state=CallState.SPEAKING,
        tts_active=True,
        current_ai_response="half a sentence",
        current_user_input="stale",
    )


@pytest.mark.asyncio
async def test_successful_interrupt_reports_what_was_discarded():
    gw = _Gateway(dropped=12, segments=1)
    r = await interrupt_playback(_session(), media_gateway=gw, reason="barge_in")

    assert r.ok is True
    assert r.gateway_dropped_frames == 12
    assert r.gateway_dropped_ms == 240          # 20ms per PCMU frame
    assert r.gateway_interrupted_segments == 1
    assert r.local_bytes_discarded == 320
    assert r.pending_bytes == 3
    assert r.utterance_rotated is True
    assert r.errors == []


@pytest.mark.asyncio
async def test_state_switches_to_listening_and_stale_input_is_dropped():
    from app.domain.models.session import CallState

    s = _session()
    await interrupt_playback(s, media_gateway=_Gateway(), reason="barge_in")

    assert s.state == CallState.LISTENING
    assert s.tts_active is False
    assert s.current_ai_response == ""
    assert s.current_user_input == "", "stale transcript must never reach the LLM"


@pytest.mark.asyncio
async def test_gateway_failure_is_NOT_reported_as_success():
    """THE REGRESSION. Python's own state saying LISTENING proves nothing about
    audio already queued in the C++ gateway."""
    from app.domain.models.session import CallState

    s = _session()
    r = await interrupt_playback(
        s, media_gateway=_Gateway(ok=False, attempts=2), reason="barge_in"
    )

    assert r.ok is False, "a failed gateway interrupt must not read as stopped"
    assert any("gateway" in e for e in r.errors)
    # Local state still moves on — we must not leave the session wedged.
    assert s.state == CallState.LISTENING
    assert r.state_changed is True


@pytest.mark.asyncio
async def test_a_raising_gateway_is_a_failure_not_a_silent_pass():
    r = await interrupt_playback(
        _session(), media_gateway=_Gateway(raises=True), reason="barge_in"
    )
    assert r.ok is False
    assert any("clear:" in e for e in r.errors)


@pytest.mark.asyncio
async def test_duplicate_barge_in_is_deduped_not_re_run():
    """Both arming sites fire for one utterance. The teardown must run ONCE."""
    s = _session()
    gw = _Gateway()

    first = await interrupt_playback(s, media_gateway=gw, reason="barge_in")
    second = await interrupt_playback(s, media_gateway=gw, reason="barge_in")

    assert gw.calls == 1, "the second event must not re-run the teardown"
    assert second.deduped is True
    assert first.deduped is False
    assert second.interrupt_id == first.interrupt_id
    assert second.gateway_dropped_frames == first.gateway_dropped_frames


@pytest.mark.asyncio
async def test_a_genuinely_later_barge_in_DOES_run_again():
    """Dedupe must cover the duplicate burst, never a real second interruption."""
    s = _session()
    gw = _Gateway()

    await interrupt_playback(s, media_gateway=gw, reason="barge_in")
    await asyncio.sleep(_INTERRUPT_DEDUPE_S + 0.05)
    again = await interrupt_playback(s, media_gateway=gw, reason="barge_in")

    assert gw.calls == 2
    assert again.deduped is False


@pytest.mark.asyncio
async def test_task_cancellation_is_reported():
    async def _cancel():
        return True

    r = await interrupt_playback(
        _session(), media_gateway=_Gateway(), reason="barge_in", cancel_task=_cancel
    )
    assert r.task_cancelled is True


@pytest.mark.asyncio
async def test_a_failing_canceller_does_not_abort_the_stop():
    """Cancelling the LLM task is bookkeeping; stopping the AUDIO is the job."""
    async def _boom():
        raise RuntimeError("task already gone")

    gw = _Gateway()
    r = await interrupt_playback(
        _session(), media_gateway=gw, reason="barge_in", cancel_task=_boom
    )
    assert gw.calls == 1, "the buffers must still be cleared"
    assert r.ok is True
    assert any("cancel:" in e for e in r.errors)


@pytest.mark.asyncio
async def test_tts_provider_is_told_to_stop_generating():
    stopped = {"v": False}

    async def _clear():
        stopped["v"] = True

    provider = types.SimpleNamespace(clear_queue=_clear)
    await interrupt_playback(
        _session(), media_gateway=_Gateway(), reason="barge_in", tts_provider=provider
    )
    assert stopped["v"] is True


@pytest.mark.asyncio
async def test_gateway_without_the_ack_contract_still_succeeds():
    """Twilio/Vonage bridges predate the acknowledgement — must not read as a
    failure just because they return nothing."""
    r = await interrupt_playback(
        _session(), media_gateway=_Gateway(ack=False), reason="barge_in"
    )
    assert r.ok is True
    assert r.tts_queue_cleared is True


def test_dropped_ms_is_derived_from_frames():
    assert InterruptResult(interrupt_id="x", call_id="c",
                           gateway_dropped_frames=50).gateway_dropped_ms == 1000
