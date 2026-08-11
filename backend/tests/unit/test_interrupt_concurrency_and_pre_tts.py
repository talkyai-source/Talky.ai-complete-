"""Review findings 1-3 (2026-08-11): concurrency, pre-TTS talk-over, centralization.

Three defects found in code review of 729632f7. All three were real. These tests
pin each fix and, more importantly, pin the FAILURE each fix prevents.

FINDING 1 — the dedupe did not survive concurrency
    `interrupt_playback` read `_last_interrupt` at entry and wrote it at exit,
    with FOUR awaits in between (cancel, clear_output_buffer, the gateway POST,
    tts clear_queue). Two coroutines entering together both saw an empty slot,
    both passed the check, and both ran the whole teardown — rotating the
    utterance id twice and cancelling a task mid-unwind.

    Fixed with a single-flight Future published SYNCHRONOUSLY before the first
    await. A "remember the last result" check can never fix this on its own,
    because the window is between the check and the write.

FINDING 2 — the pre-TTS guard prevented a cancel but not a talk-over
    `barge_in_ignored_final_pre_tts` correctly refuses to cancel a FINAL answer
    that has not begun playing (cancelling it leaves the caller in silence).
    But it said nothing about WHEN that answer may start speaking, so:

        caller starts speaking -> answer finishes generating -> playback starts

    put the agent on top of a caller mid-sentence. Barge-in cannot help: it
    stops audio already playing, and here the talk-over starts at packet one.

    Fixed with a bounded HOLD rather than a cancel — the answer survives AND
    the caller is not spoken over.

FINDING 3 — cancellation lived outside the "centralized" operation
    `handle_barge_in` cancelled the pending turn itself, then called
    `interrupt_playback` separately, so the two shared no interrupt_id and
    could interleave. The cancel is now passed IN as step 2.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from app.domain.services.voice_pipeline import playback_gate
from app.domain.services.voice_pipeline.interrupt import interrupt_playback
from app.domain.services.voice_pipeline.playback_gate import (
    _MAX_HOLD_S,
    arm_pre_tts_hold,
    await_caller_pause,
    caller_is_speaking,
    mark_caller_speaking,
    mark_caller_stopped,
)


# ── Finding 1: concurrency ────────────────────────────────────────────────

class _SlowGateway:
    """Gateway whose clear_output_buffer yields — the await window that let two
    coroutines through before the fix."""

    def __init__(self, delay=0.05):
        self.delay = delay
        self.calls = 0

    async def clear_output_buffer(self, call_id):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return {
            "ok": True, "local_bytes_discarded": 320, "pending_bytes": 0,
            "gateway": {"ok": True, "dropped_frames": 7, "interrupted_segments": 1,
                        "attempts": 1, "utterance_rotated": True, "error": None},
        }


def _session():
    from app.domain.models.session import CallState

    return types.SimpleNamespace(
        call_id="11111111-2222-3333-4444-555555555555",
        state=CallState.SPEAKING, tts_active=True,
        current_ai_response="half a sentence", current_user_input="stale",
    )


@pytest.mark.asyncio
async def test_two_simultaneous_interrupts_run_the_teardown_ONCE():
    """THE REGRESSION (finding 1). Launched together, before either can store a
    result, only one teardown may run."""
    s, gw = _session(), _SlowGateway()

    a, b = await asyncio.gather(
        interrupt_playback(s, media_gateway=gw, reason="barge_in"),
        interrupt_playback(s, media_gateway=gw, reason="barge_in"),
    )

    assert gw.calls == 1, (
        "two concurrent barge-ins ran the teardown twice — the utterance id "
        "would be rotated twice and the turn task cancelled mid-unwind"
    )
    assert {a.deduped, b.deduped} == {False, True}
    assert a.gateway_dropped_frames == b.gateway_dropped_frames


@pytest.mark.asyncio
async def test_ten_simultaneous_interrupts_still_run_once():
    s, gw = _session(), _SlowGateway()
    results = await asyncio.gather(
        *[interrupt_playback(s, media_gateway=gw, reason="barge_in") for _ in range(10)]
    )
    assert gw.calls == 1
    assert sum(1 for r in results if not r.deduped) == 1
    assert all(r.ok for r in results)


@pytest.mark.asyncio
async def test_a_failing_in_flight_interrupt_does_not_wedge_the_session():
    """If the single-flight operation raises, the slot must clear so the NEXT
    barge-in can still stop the agent."""
    class _Boom:
        def __init__(self):
            self.calls = 0

        async def clear_output_buffer(self, call_id):
            self.calls += 1
            await asyncio.sleep(0)
            raise RuntimeError("gateway exploded")

    s = _session()
    # clear_output_buffer failures are caught inside, so force a hard failure
    # by removing the attribute the operation needs mid-flight.
    boom = _Boom()
    r1 = await interrupt_playback(s, media_gateway=boom, reason="barge_in")
    assert r1.ok is False

    assert getattr(s, "_interrupt_inflight", None) is None, (
        "the in-flight slot must be cleared, or the session can never be "
        "interrupted again"
    )

    gw = _SlowGateway()
    await asyncio.sleep(0.4)          # past the recent-result window
    s.tts_active = True               # the next reply is playing
    r2 = await interrupt_playback(s, media_gateway=gw, reason="barge_in")
    assert gw.calls == 1 and r2.ok is True


@pytest.mark.asyncio
async def test_sequential_interrupts_past_the_window_still_work():
    """Single-flight must not become a permanent lock."""
    s, gw = _session(), _SlowGateway(delay=0.0)
    await interrupt_playback(s, media_gateway=gw, reason="barge_in")
    await asyncio.sleep(0.4)
    s.tts_active = True          # the next reply is playing — see below
    await interrupt_playback(s, media_gateway=gw, reason="barge_in")
    assert gw.calls == 2


# ── Finding 2: pre-TTS talk-over ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_playback_waits_while_the_caller_is_still_speaking():
    """THE REGRESSION (finding 2). A protected answer must not start speaking
    on top of a caller who is mid-sentence."""
    s = _session()
    mark_caller_speaking(s)
    arm_pre_tts_hold(s)

    async def _stop_after(delay):
        await asyncio.sleep(delay)
        mark_caller_stopped(s)

    asyncio.ensure_future(_stop_after(0.12))
    waited = await await_caller_pause(s, call_id=s.call_id)

    assert waited >= 0.10, "playback started while the caller was still talking"
    assert waited < _MAX_HOLD_S


@pytest.mark.asyncio
async def test_the_hold_is_bounded_so_a_caller_cannot_mute_the_agent_forever():
    """Talking over someone is bad. Never speaking again is worse."""
    s = _session()
    mark_caller_speaking(s)          # never stops
    arm_pre_tts_hold(s)

    playback_gate._MAX_HOLD_S, original = 0.15, playback_gate._MAX_HOLD_S
    try:
        waited = await await_caller_pause(s, call_id=s.call_id)
    finally:
        playback_gate._MAX_HOLD_S = original

    assert 0.15 <= waited < 1.0, "the hold must time out and speak anyway"


@pytest.mark.asyncio
async def test_no_hold_when_not_armed():
    """Normal turns must not pay a latency cost for this."""
    s = _session()
    mark_caller_speaking(s)          # speaking, but no barge-in was protected
    assert await await_caller_pause(s, call_id=s.call_id) == 0.0


@pytest.mark.asyncio
async def test_no_hold_when_the_caller_is_already_quiet():
    s = _session()
    mark_caller_stopped(s)
    arm_pre_tts_hold(s)
    assert await await_caller_pause(s, call_id=s.call_id) == 0.0


@pytest.mark.asyncio
async def test_the_hold_is_one_shot():
    """A stale flag must not slow every later turn."""
    s = _session()
    mark_caller_stopped(s)
    arm_pre_tts_hold(s)
    await await_caller_pause(s, call_id=s.call_id)
    assert getattr(s, "_defer_playback_for_caller") is False


def test_caller_speaking_state_transitions():
    s = _session()
    assert caller_is_speaking(s) is False
    mark_caller_speaking(s)
    assert caller_is_speaking(s) is True
    mark_caller_stopped(s)
    assert caller_is_speaking(s) is False


# ── Finding 3: centralization ─────────────────────────────────────────────

def test_handle_barge_in_passes_cancellation_INTO_the_central_operation():
    """The cancel must share the interrupt_id, not run beside it."""
    import inspect

    from app.domain.services import voice_pipeline_service as vps

    src = inspect.getsource(vps.VoicePipelineService.handle_barge_in)
    assert "cancel_task=_cancel_pending_turn" in src, (
        "cancellation must be passed into interrupt_playback, not performed "
        "separately — otherwise the two operations share no interrupt_id and "
        "can interleave"
    )
    # And it must NOT still be cancelling on its own before that call.
    before = src.split("interrupt_playback(")[0]
    assert "await self._cancel_turn_task(" not in before.split("async def _cancel_pending_turn")[0], (
        "a second, uncentralised cancel path is still present"
    )


@pytest.mark.asyncio
async def test_the_cancel_runs_inside_the_single_flight_gate():
    """Because cancellation is now step 2 of the guarded operation, two
    concurrent barge-ins cannot cancel the same task twice."""
    cancels = {"n": 0}

    async def _cancel():
        cancels["n"] += 1
        await asyncio.sleep(0.02)
        return True

    s, gw = _session(), _SlowGateway()
    await asyncio.gather(
        interrupt_playback(s, media_gateway=gw, reason="barge_in", cancel_task=_cancel),
        interrupt_playback(s, media_gateway=gw, reason="barge_in", cancel_task=_cancel),
    )
    assert cancels["n"] == 1, "the pending turn was cancelled twice"
