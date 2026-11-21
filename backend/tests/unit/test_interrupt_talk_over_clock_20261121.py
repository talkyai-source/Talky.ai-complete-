"""Barge-in stop figures: the talk-over clock, and the clear-before-wait order.

FIX 1 (2026-11-21, the 58 interruptions of the real calls of 20 Nov)
    `speech_to_stop_ms` starts at the first loud frame of an unbroken run of
    caller sound, and was logged even when no agent audio was queued: 33 of 58
    stops had `gw_frames=0 local_bytes=0`, and one reported 10 s of "talk over"
    for a caller whose opening "hello" started the clock 10 s before the agent's
    audio began. `speech_to_stop_ms` stays exactly as it was; the record now
    also carries `audible` and `talk_over_ms`.

FIX 2
    On a stop with agent audio playing, the gateway used to be cleared only
    after the reply task had been cancelled AND waited for (up to 250 ms). The
    cancel is now REQUESTED first (so the reply task can send nothing more under
    the utterance id the clear rotates), the gateway is cleared next, and only
    then is the unwind waited for.
"""
from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline import interrupt as interrupt_mod
from app.domain.services.voice_pipeline import tts_playback as tts_playback_mod
from app.domain.services.voice_pipeline.interrupt import _do_interrupt


def _session(*, tts_active: bool = True) -> CallSession:
    s = CallSession(
        call_id="d068f4b8-cd1a-4e56-a3bc-c4c53186ac6c",
        campaign_id="c2b6734d-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1", provider_call_id="talky-out-1",
        system_prompt="sp", voice_id="v1",
    )
    s.state = CallState.SPEAKING
    s.tts_active = tts_active
    return s


class _Gateway:
    def __init__(self, *, frames: int = 20, local_bytes: int = 0,
                 events: list | None = None, fail: bool = False):
        self.frames = frames
        self.local_bytes = local_bytes
        self.events = events if events is not None else []
        self.fail = fail
        self.calls = 0
        self.clear_at: float | None = None

    async def clear_output_buffer(self, call_id):
        self.calls += 1
        self.clear_at = time.monotonic()
        self.events.append("clear")
        if self.fail:
            raise RuntimeError("gateway down")
        return {
            "ok": True, "local_bytes_discarded": self.local_bytes,
            "pending_bytes": 0,
            "gateway": {"ok": True, "dropped_frames": self.frames,
                        "interrupted_segments": 1 if self.frames else 0,
                        "attempts": 1, "utterance_rotated": True},
        }


class _Tts:
    def __init__(self, events):
        self.events = events
        self.calls = 0

    async def clear_queue(self):
        self.calls += 1
        self.events.append("tts_clear")


def _canceller(events, *, delay: float = 0.0, result: bool = True):
    state = {"n": 0, "done_at": None}

    async def _cancel():
        state["n"] += 1
        events.append("cancel_requested")
        if delay:
            await asyncio.sleep(delay)
        state["done_at"] = time.monotonic()
        events.append("cancel_done")
        return result

    _cancel.state = state
    return _cancel


async def _run(s, gw, *, cancel_task=None, tts=None):
    return await _do_interrupt(
        s, call_id=s.call_id, media_gateway=gw, reason="barge_in",
        cancel_task=cancel_task, tts_provider=tts,
    )


# ── FIX 1: talk_over_ms / audible ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_talk_over_counts_from_the_agents_audio_not_the_callers_hello():
    s = _session()
    now = time.monotonic()
    s._caller_voice_onset_at = now - 10.0      # caller's opening "hello"
    s._first_tts_chunk_at = now - 1.0          # agent audio began 1 s ago
    s._last_tts_chunk_at = now - 0.05

    r = await _run(s, _Gateway(frames=20))

    assert r.audible is True
    assert 950 <= r.talk_over_ms <= 1500, r.talk_over_ms
    # the old figure is untouched and still carries the 10 s onset
    assert r.speech_to_stop_ms >= 10000
    assert r.speech_to_stop_ms == pytest.approx(r.detect_ms + r.elapsed_ms, abs=0.01)
    log = r.as_log()
    assert log["audible"] is True and 950 <= log["talk_over_ms"] <= 1500
    assert "speech_to_stop_ms" in log and "detect_ms" in log


@pytest.mark.asyncio
async def test_talk_over_is_capped_by_the_detection_time_when_that_is_shorter():
    s = _session()
    now = time.monotonic()
    s._caller_voice_onset_at = now - 0.4       # caller spoke 0.4 s ago
    s._first_tts_chunk_at = now - 5.0          # agent has talked for 5 s
    s._last_tts_chunk_at = now - 0.05

    r = await _run(s, _Gateway(frames=20))

    assert r.audible is True
    assert 350 <= r.talk_over_ms <= 800, r.talk_over_ms


@pytest.mark.asyncio
async def test_nothing_queued_is_not_audible_and_talk_over_is_zero():
    s = _session()
    now = time.monotonic()
    s._caller_voice_onset_at = now - 10.0
    s._first_tts_chunk_at = now - 1.0
    s._last_tts_chunk_at = now - 0.05

    r = await _run(s, _Gateway(frames=0, local_bytes=0))

    assert r.audible is False
    assert r.talk_over_ms == 0
    assert r.as_log()["talk_over_ms"] == 0
    # unchanged from today: onset age + teardown
    assert r.speech_to_stop_ms >= 10000
    assert r.speech_to_stop_ms == pytest.approx(r.detect_ms + r.elapsed_ms, abs=0.01)


@pytest.mark.asyncio
async def test_local_bytes_alone_make_the_stop_audible():
    s = _session()
    now = time.monotonic()
    s._first_tts_chunk_at = now - 0.5
    s._last_tts_chunk_at = now - 0.05
    r = await _run(s, _Gateway(frames=0, local_bytes=320))
    assert r.audible is True
    assert r.talk_over_ms is not None and r.talk_over_ms >= 450


@pytest.mark.asyncio
async def test_stop_before_any_agent_audio_started_does_not_crash():
    s = _session()                              # no first-chunk stamp at all
    s._caller_voice_onset_at = time.monotonic() - 3.0

    r = await _run(s, _Gateway(frames=0, local_bytes=0))

    assert r.audible is False
    assert r.talk_over_ms == 0
    assert r.ok is True


@pytest.mark.asyncio
async def test_a_garbage_stamp_never_stops_the_stop():
    s = _session()
    s._first_tts_chunk_at = "not-a-number"
    r = await _run(s, _Gateway(frames=5))
    assert r.ok is True and r.audible is True


def test_first_chunk_is_stamped_once_per_reply_and_reset_for_the_next():
    stamp = tts_playback_mod._stamp_first_audio_chunk
    s = _session()
    t0 = 1000.0
    stamp(s, t0)
    assert s._first_tts_chunk_at == t0
    s._last_tts_chunk_at = t0                   # what the sender does next
    stamp(s, t0 + 0.2)                          # same reply, next chunk
    assert s._first_tts_chunk_at == t0
    s._last_tts_chunk_at = t0 + 0.2
    stamp(s, t0 + 0.4)                          # still the same reply
    assert s._first_tts_chunk_at == t0
    s._last_tts_chunk_at = t0 + 0.4
    # the agent went quiet for longer than the gateway drain window, then a
    # NEW reply starts: the stamp moves to its first chunk
    stamp(s, t0 + 5.0)
    assert s._first_tts_chunk_at == t0 + 5.0


def test_the_stamp_is_taken_where_the_chunk_is_handed_on():
    """Guard (source text, not behaviour): the stamp sits right after the
    first-audio hand-off in synthesize_and_send, next to _last_tts_chunk_at."""
    import inspect

    src = inspect.getsource(tts_playback_mod)
    i = src.index("await self._p.media_gateway.send_audio(call_id, raw)")
    j = src.index("_stamp_first_audio_chunk(session", i)
    k = src.index("session._last_tts_chunk_at = _now_chunk", i)
    assert i < j < k + 200


# ── FIX 2: the clear goes ahead of the wait for the cancel ──────────────────

@pytest.mark.asyncio
async def test_gateway_is_cleared_before_a_slow_cancel_finishes():
    events: list = []
    s = _session()
    s._last_tts_chunk_at = time.monotonic() - 0.05
    gw = _Gateway(events=events)
    cancel = _canceller(events, delay=0.2)

    r = await _run(s, gw, cancel_task=cancel, tts=_Tts(events))

    # cancel is requested first (the reply task can send nothing more under the
    # rotated utterance id), the clear lands next, then the unwind is awaited
    assert events == ["cancel_requested", "clear", "cancel_done", "tts_clear"]
    assert gw.clear_at < cancel.state["done_at"]
    assert (cancel.state["done_at"] - gw.clear_at) >= 0.15
    assert r.task_cancelled is True and r.ok is True


@pytest.mark.asyncio
async def test_drain_only_stop_also_clears_before_the_wait():
    """tts_active already False but inside the drain window: same new order."""
    events: list = []
    s = _session(tts_active=False)
    s._last_tts_chunk_at = time.monotonic() - 0.3
    gw = _Gateway(events=events)
    cancel = _canceller(events, delay=0.1, result=False)

    r = await _run(s, gw, cancel_task=cancel)

    assert events == ["cancel_requested", "clear", "cancel_done"]
    assert r.drain_probe is True        # nothing was cancelled, gateway asked
    assert r.task_cancelled is False


@pytest.mark.asyncio
async def test_nothing_playing_path_calls_exactly_what_it_called_before():
    """Recorded on the UNCHANGED code: a canceller is run (once, awaited
    inline), the gateway and the provider are never touched."""
    events: list = []
    s = _session(tts_active=False)              # no stamp, no drain window
    gw = _Gateway(events=events)
    tts = _Tts(events)
    cancel = _canceller(events, delay=0.05, result=False)

    r = await _run(s, gw, cancel_task=cancel, tts=tts)

    assert events == ["cancel_requested", "cancel_done"]
    assert gw.calls == 0 and tts.calls == 0
    assert r.ok is True and r.task_cancelled is False


@pytest.mark.asyncio
async def test_a_cancelled_speculative_turn_keeps_the_old_order():
    """Nothing audible but a real turn was cancelled: cancel is awaited, THEN
    the gateway is asked (unchanged)."""
    events: list = []
    s = _session(tts_active=False)
    gw = _Gateway(events=events, frames=0)
    cancel = _canceller(events, delay=0.05, result=True)

    r = await _run(s, gw, cancel_task=cancel, tts=_Tts(events))

    assert events == ["cancel_requested", "cancel_done", "clear", "tts_clear"]
    assert r.task_cancelled is True


@pytest.mark.asyncio
async def test_a_failing_clear_still_cancels_and_still_finishes():
    events: list = []
    s = _session()
    s._last_tts_chunk_at = time.monotonic() - 0.05
    gw = _Gateway(events=events, fail=True)
    cancel = _canceller(events, delay=0.05)
    tts = _Tts(events)

    r = await _run(s, gw, cancel_task=cancel, tts=tts)

    assert cancel.state["n"] == 1
    assert r.task_cancelled is True
    assert any(e.startswith("clear:") for e in r.errors)
    assert events == ["cancel_requested", "clear", "cancel_done", "tts_clear"]


@pytest.mark.asyncio
async def test_a_failing_canceller_does_not_block_the_clear():
    events: list = []
    s = _session()
    s._last_tts_chunk_at = time.monotonic() - 0.05
    gw = _Gateway(events=events)

    async def _boom():
        events.append("cancel_requested")
        raise RuntimeError("cancel blew up")

    r = await _run(s, gw, cancel_task=_boom, tts=_Tts(events))

    assert gw.calls == 1
    assert any(e.startswith("cancel:") for e in r.errors)
    assert events == ["cancel_requested", "clear", "tts_clear"]


@pytest.mark.asyncio
async def test_no_step_runs_twice_and_step_log_order(caplog):
    events: list = []
    s = _session()
    s._last_tts_chunk_at = time.monotonic() - 0.05
    gw = _Gateway(events=events)
    cancel = _canceller(events, delay=0.05)
    tts = _Tts(events)

    with caplog.at_level(logging.INFO, logger=interrupt_mod.logger.name):
        await _run(s, gw, cancel_task=cancel, tts=tts)

    assert gw.calls == 1 and cancel.state["n"] == 1 and tts.calls == 1
    steps = [
        rec.getMessage().split("interrupt_step=")[1].split()[0]
        for rec in caplog.records if "interrupt_step=" in rec.getMessage()
    ]
    assert steps == [
        "begin", "state_listening", "buffers_cleared", "cpp_interrupt",
        "task_cancelled", "tts_provider_cleared",
    ]
