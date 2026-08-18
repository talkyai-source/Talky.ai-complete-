"""A barge-in landing just after Python stops streaming, while the gateway drains.

WHY (2026-08-18, call d068f4b8, interrupt 0479a76d17ef)

    20:50:49.454  turn_complete            ← Python's last chunk; tts_active → False
    20:50:50.388  caller's acoustic onset
    20:50:51.606  Flux StartOfTurn - barge-in detected
    20:50:51.607  interrupt_step=nothing_playing ... tts_active=False detect_ms=1218.4

`interrupt_playback` decided there was nothing to stop from ONE local variable
and returned before calling `clear_output_buffer`. So the C++ gateway was never
asked, never told to stop, and the utterance id was never rotated.

`tts_active` means "Python is still streaming TTS". It does not mean "the caller
has stopped hearing audio" — the gateway holds its own queue and paces it in
real time. Every interrupt that same day which DID ask the gateway found
240-300ms still sitting in it (`dropped_ms` p50 240, max 300).

On this particular call the timing worked out and the caller was probably not
talked over — Python finished 0.93s before they spoke. That is luck, not
design: the code could not have known, because it did not look.

So the fix is narrow. The `nothing_playing` shortcut stays — two thirds of all
interrupt events are ordinary turns and must not pay for a gateway round-trip
or pollute the barge-in metric. It just no longer applies during the brief
window where the gateway may still be draining. There, we ASK.
"""
from __future__ import annotations

import time
import types

import pytest

from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline import interrupt as interrupt_mod
from app.domain.services.voice_pipeline.interrupt import (
    _GATEWAY_DRAIN_S,
    _playback_may_still_be_audible,
    interrupt_playback,
)


class _Gateway:
    """Records whether it was asked at all — the whole point of the fix."""

    def __init__(self, dropped: int = 12):
        self.calls = 0
        self.dropped = dropped

    async def clear_output_buffer(self, call_id):
        self.calls += 1
        return {
            "ok": True, "local_bytes_discarded": 0, "pending_bytes": 0,
            "gateway": {"ok": True, "dropped_frames": self.dropped,
                        "dropped_ms": self.dropped * 20, "attempts": 1,
                        "interrupted_segments": 1, "utterance_rotated": True},
        }


def _session(*, tts_active: bool, last_chunk_age_s=None) -> CallSession:
    """A REAL CallSession — it rejects undeclared public attributes, which is
    how two earlier features ended up silently dead."""
    from app.domain.models.session import CallState

    s = CallSession(
        call_id="d068f4b8-cd1a-4e56-a3bc-c4c53186ac6c",
        campaign_id="c2b6734d-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1", provider_call_id="talky-out-1",
        system_prompt="sp", voice_id="v1",
    )
    s.state = CallState.SPEAKING
    s.tts_active = tts_active
    if last_chunk_age_s is not None:
        s._last_tts_chunk_at = time.monotonic() - last_chunk_age_s
    return s


# ── the drain-window helper ─────────────────────────────────────────────────

def test_no_stamp_means_certainly_nothing_playing():
    """Sessions that never sent TTS (and any path that does not stamp) must
    behave exactly as before, not gain a mystery window."""
    assert _playback_may_still_be_audible(_session(tts_active=False)) is None


def test_inside_the_window_reports_an_age():
    s = _session(tts_active=False, last_chunk_age_s=0.2)
    age = _playback_may_still_be_audible(s)
    assert age is not None and 150 <= age <= 350


def test_outside_the_window_reports_nothing():
    s = _session(tts_active=False, last_chunk_age_s=_GATEWAY_DRAIN_S + 1.0)
    assert _playback_may_still_be_audible(s) is None


def test_the_window_can_be_switched_off(monkeypatch):
    """Kill switch. VOICE_GATEWAY_DRAIN_S=0 restores the exact pre-fix
    behaviour without a redeploy."""
    monkeypatch.setattr(interrupt_mod, "_GATEWAY_DRAIN_S", 0.0)
    s = _session(tts_active=False, last_chunk_age_s=0.1)
    assert _playback_may_still_be_audible(s) is None


def test_a_hostile_session_degrades_quietly():
    s = types.SimpleNamespace(_last_tts_chunk_at="not-a-number")
    assert _playback_may_still_be_audible(s) is None


# ── the decision ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_production_case_now_asks_the_gateway():
    """THE FIX. Python has just finished; the caller barges in 0.3s later.
    Previously: `nothing_playing`, gateway never contacted. Now: we ask."""
    gw = _Gateway(dropped=12)
    s = _session(tts_active=False, last_chunk_age_s=0.3)

    r = await interrupt_playback(s, media_gateway=gw, reason="barge_in")

    assert gw.calls == 1, "the gateway was still not asked"
    assert r.gateway_dropped_frames == 12
    assert r.gateway_dropped_ms == 240
    assert r.drain_probe is True
    assert r.utterance_rotated is True


@pytest.mark.asyncio
async def test_an_ordinary_turn_still_takes_the_cheap_path():
    """THE LOAD-BEARING NEGATIVE. Two thirds of interrupt events are ordinary
    turns. They must not pay for a gateway round-trip — that shortcut is why
    the barge-in metric means anything."""
    gw = _Gateway()
    s = _session(tts_active=False, last_chunk_age_s=_GATEWAY_DRAIN_S + 2.0)

    r = await interrupt_playback(s, media_gateway=gw, reason="barge_in")

    assert gw.calls == 0, "an ordinary turn hit the gateway"
    assert r.ok is True
    assert r.drain_probe is False


@pytest.mark.asyncio
async def test_a_session_that_never_spoke_takes_the_cheap_path():
    gw = _Gateway()
    r = await interrupt_playback(_session(tts_active=False),
                                 media_gateway=gw, reason="barge_in")
    assert gw.calls == 0
    assert r.drain_probe is False


@pytest.mark.asyncio
async def test_audible_playback_is_unaffected():
    """The normal barge-in path must be untouched — it is the part that is
    working, and this change must not go near it."""
    gw = _Gateway(dropped=13)
    s = _session(tts_active=True)

    r = await interrupt_playback(s, media_gateway=gw, reason="barge_in")

    assert gw.calls == 1
    assert r.drain_probe is False, "real playback must not be labelled a probe"
    assert r.gateway_dropped_frames == 13


# ── the metric must stay honest ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_probe_that_finds_an_empty_queue_is_not_counted(monkeypatch):
    """If the gateway was already empty, nothing was stopped and it was not a
    barge-in. Counting it would re-introduce exactly the noise the
    `nothing_playing` shortcut exists to remove."""
    counted = []
    import app.infrastructure.metrics.voice_metrics as vm
    monkeypatch.setattr(vm, "record_interrupt_outcome",
                        lambda **kw: counted.append(kw))

    gw = _Gateway(dropped=0)
    s = _session(tts_active=False, last_chunk_age_s=0.3)
    r = await interrupt_playback(s, media_gateway=gw, reason="barge_in")

    assert gw.calls == 1, "we should still have asked"
    assert r.drain_probe is True
    assert counted == [], "an empty-queue probe was counted as a barge-in"


@pytest.mark.asyncio
async def test_a_probe_that_finds_real_audio_IS_counted(monkeypatch):
    """The converse: audio was queued and we stopped it. That is a genuine
    interruption and must appear in the metric."""
    counted = []
    import app.infrastructure.metrics.voice_metrics as vm
    monkeypatch.setattr(vm, "record_interrupt_outcome",
                        lambda **kw: counted.append(kw))

    gw = _Gateway(dropped=12)
    s = _session(tts_active=False, last_chunk_age_s=0.3)
    await interrupt_playback(s, media_gateway=gw, reason="barge_in")

    assert len(counted) == 1
    assert counted[0]["dropped_frames"] == 12


# ── observability ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_decision_is_attributable_in_the_log(caplog):
    """`drain_ms` on the begin line, `drain_probe` in the verdict — so which
    path a given interrupt took is a fact in the journal, not a deduction."""
    s = _session(tts_active=False, last_chunk_age_s=0.3)
    with caplog.at_level("INFO"):
        await interrupt_playback(s, media_gateway=_Gateway(), reason="barge_in")

    assert "drain_ms=" in caplog.text
    assert "'drain_probe': True" in caplog.text


@pytest.mark.asyncio
async def test_the_stamp_survives_a_real_call_session():
    """The pydantic trap, pinned. `_last_tts_chunk_at` is underscore-prefixed
    precisely because the public form raises on this model."""
    s = _session(tts_active=False)
    s._last_tts_chunk_at = time.monotonic()
    assert isinstance(s._last_tts_chunk_at, float)

    with pytest.raises(ValueError, match="has no field"):
        s.last_tts_chunk_at = time.monotonic()
