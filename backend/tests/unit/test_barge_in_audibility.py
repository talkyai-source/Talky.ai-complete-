"""Measuring the interruption the CALLER experienced, not the one we performed.

2026-08-13 reported `elapsed_ms` around 0.2-0.94ms for every interrupt and it
was all true and all beside the point. That number is how long our teardown
took. The caller's experience is the gap between opening their mouth and the
agent going quiet, and essentially all of that gap elapses before
``interrupt_playback`` is even called — waiting for Deepgram Flux to decide a
word has started. Reporting only the teardown made a path with an unmeasured
detection latency look flawless.

Two things are pinned here:

  * an acoustic onset anchor, per audio frame, so "when did the caller start
    talking" is a measurement rather than an inference from StartOfTurn; and
  * whether audio from a CANCELLED utterance was subsequently accepted by the
    gateway — i.e. whether the agent resumed speaking after being stopped.

The second exists because the utterance-id rotation protects a narrower window
than its comment claimed: a chunk already stamped with the retired id is
rejected, but a chunk that arrives after the rotation reads the fresh id and is
accepted. Several layers should prevent one ever arriving. "Should" is what we
are replacing with a count.
"""
from __future__ import annotations

import types

import pytest

from app.domain.services.voice_pipeline.audio_ingest import (
    _VOICE_ONSET_GAP_S,
    note_voice_activity,
    voice_onset_age_s,
)
from app.domain.services.voice_pipeline.interrupt import interrupt_playback


def _sum_sq(rms: float, n: int = 640) -> float:
    """Sum of squares for ``n`` samples at the given RMS."""
    return rms * rms * n


def _blank():
    return types.SimpleNamespace()


# ── the onset anchor ────────────────────────────────────────────────────────

def test_a_voiced_frame_arms_the_onset():
    s = _blank()
    note_voice_activity(s, _sum_sq(3000), 640)
    assert s._caller_voice_onset_at is not None
    assert voice_onset_age_s(s) is not None


def test_a_quiet_frame_arms_nothing():
    """Room tone measured 7-14 RMS on the 2026-08-13 calls. If silence armed an
    onset, every barge-in would be timed from the start of the call."""
    s = _blank()
    note_voice_activity(s, _sum_sq(12), 640)
    assert getattr(s, "_caller_voice_onset_at", None) is None
    assert voice_onset_age_s(s) is None


def test_one_continuous_utterance_reports_one_onset():
    """THE property that makes the number mean anything. A sentence is many
    frames; if each re-armed, the measured latency would always be ~40ms and
    would always look excellent."""
    s = _blank()
    note_voice_activity(s, _sum_sq(3000), 640)
    first = s._caller_voice_onset_at
    for _ in range(50):                       # ~2s of continuous speech
        note_voice_activity(s, _sum_sq(3000), 640)
    assert s._caller_voice_onset_at == first


def test_a_pause_starts_a_new_utterance(monkeypatch):
    """Conversely, a barge-in must not inherit the onset of the caller's
    previous turn — that would report a latency of many seconds."""
    s = _blank()
    note_voice_activity(s, _sum_sq(3000), 640)
    first = s._caller_voice_onset_at

    # Simulate the gap by ageing the last-voiced stamp past the threshold.
    s._caller_voice_last_at = s._caller_voice_last_at - (_VOICE_ONSET_GAP_S + 0.1)
    note_voice_activity(s, _sum_sq(3000), 640)

    assert s._caller_voice_onset_at != first


def test_an_unmeasured_onset_is_none_not_zero():
    """None means "we did not measure this". Zero would mean "the agent stopped
    instantly", which is a claim, and a false one."""
    assert voice_onset_age_s(_blank()) is None


def test_a_stale_onset_is_discarded():
    """Nothing legitimate keeps one utterance open for a minute; reporting it
    would put an absurd outlier into the latency distribution."""
    s = _blank()
    note_voice_activity(s, _sum_sq(3000), 640)
    s._caller_voice_onset_at -= 120.0
    assert voice_onset_age_s(s) is None


def test_measurement_never_raises_on_a_hostile_session():
    """This runs on every audio frame of every call. A session object it cannot
    write to must cost nothing worse than a missing measurement."""
    class _NoWrites:
        __slots__ = ()

    note_voice_activity(_NoWrites(), _sum_sq(3000), 640)   # must not raise
    note_voice_activity(_blank(), _sum_sq(3000), 0)        # zero samples
    assert voice_onset_age_s(_NoWrites()) is None


# ── the interrupt reports both halves ───────────────────────────────────────

class _Gateway:
    async def clear_output_buffer(self, call_id):
        return {
            "ok": True, "local_bytes_discarded": 320, "pending_bytes": 0,
            "gateway": {"ok": True, "dropped_frames": 12, "attempts": 1,
                        "interrupted_segments": 1, "utterance_rotated": True},
        }


def _speaking_session(*, onset_age_s: float | None = None):
    from app.domain.models.session import CallState
    import time as _t

    s = types.SimpleNamespace(
        call_id="11111111-2222-3333-4444-555555555555",
        state=CallState.SPEAKING,
        tts_active=True,
        current_ai_response="half a sentence",
        current_user_input="",
    )
    if onset_age_s is not None:
        s._caller_voice_onset_at = _t.monotonic() - onset_age_s
        s._caller_voice_last_at = _t.monotonic()
    return s


@pytest.mark.asyncio
async def test_the_caller_facing_latency_is_reported():
    """The caller had been talking for 300ms before we started stopping. The
    reported figure must include that, not just our teardown."""
    s = _speaking_session(onset_age_s=0.30)
    r = await interrupt_playback(s, media_gateway=_Gateway(), reason="barge_in")

    assert r.detect_ms is not None
    assert 280 <= r.detect_ms <= 400, r.detect_ms
    assert r.speech_to_stop_ms is not None
    # Detection dominates; teardown is the small remainder.
    assert r.speech_to_stop_ms >= r.detect_ms
    assert r.speech_to_stop_ms == pytest.approx(r.detect_ms + r.elapsed_ms)


@pytest.mark.asyncio
async def test_teardown_time_alone_would_have_understated_it():
    """Pins the actual 2026-08-13 reporting failure: elapsed_ms is sub-
    millisecond while the caller was talked over for a third of a second."""
    s = _speaking_session(onset_age_s=0.30)
    r = await interrupt_playback(s, media_gateway=_Gateway(), reason="barge_in")

    assert r.elapsed_ms < 50, "teardown should be trivial"
    assert r.speech_to_stop_ms > 10 * r.elapsed_ms, (
        "the caller-facing figure should be dominated by detection, not teardown"
    )


@pytest.mark.asyncio
async def test_no_onset_leaves_both_figures_unmeasured():
    s = _speaking_session(onset_age_s=None)
    r = await interrupt_playback(s, media_gateway=_Gateway(), reason="barge_in")

    assert r.detect_ms is None
    assert r.speech_to_stop_ms is None
    assert r.as_log()["speech_to_stop_ms"] is None


@pytest.mark.asyncio
async def test_an_ordinary_turn_records_detection_but_no_audible_stop():
    """70% of interrupts on 2026-08-13 were ordinary turns with nothing
    playing. They must not contribute to the barge-in latency distribution —
    no audio stopped, so there is no stop to time."""
    s = _speaking_session(onset_age_s=0.20)
    s.tts_active = False
    r = await interrupt_playback(s, media_gateway=_Gateway(), reason="barge_in")

    assert r.detect_ms is not None, "turn-detection latency is still worth having"
    assert r.speech_to_stop_ms is None


# ── did cancelled audio resume? ─────────────────────────────────────────────

def _adapter():
    """A real AsteriskAdapter with only the attributes these two methods use.
    Built without __init__ deliberately: the constructor pulls in ARI config,
    HTTP sessions and env, none of which this behaviour depends on."""
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

    a = AsteriskAdapter.__new__(AsteriskAdapter)
    a._tts_utterances = {}
    a._tts_error_counts = {}
    a._gateway_sessions = {"call-1": "sess-1"}
    a._gateway_calls = []

    async def _gateway(method, path, payload=None, ok=None):
        a._gateway_calls.append((path, payload))
        return {"ok": True}

    a._gateway = _gateway
    return a


@pytest.mark.asyncio
async def test_audio_accepted_after_an_interrupt_is_flagged_as_resumed():
    import time as _t

    a = _adapter()
    a._tts_utterances["call-1"] = {
        "utterance_id": "fresh", "seq": 0,
        "retired_at": _t.monotonic(),      # the interrupt just happened
        "resumed_chunks": 0, "resumed_bytes": 0, "total_interrupts": 1,
    }

    await a.send_tts_audio("call-1", b"\x7f" * 160)

    utt = a._tts_utterances["call-1"]
    assert utt["resumed_chunks"] == 1
    assert utt["resumed_bytes"] == 160
    assert utt["total_resumed_chunks"] == 1


@pytest.mark.asyncio
async def test_the_next_legitimate_turn_is_not_counted_as_a_resumption():
    """Audio arriving well after the interrupt is the agent's NEXT reply. The
    window must close, or every call with a barge-in would report a false
    resumption and the signal would be worthless."""
    import time as _t
    from app.infrastructure.telephony import asterisk_adapter as mod

    a = _adapter()
    a._tts_utterances["call-1"] = {
        "utterance_id": "fresh", "seq": 0,
        "retired_at": _t.monotonic() - (mod._RESUME_WINDOW_S + 0.5),
        "resumed_chunks": 0, "resumed_bytes": 0, "total_interrupts": 1,
    }

    await a.send_tts_audio("call-1", b"\x7f" * 160)

    utt = a._tts_utterances["call-1"]
    assert utt["resumed_chunks"] == 0
    assert utt.get("total_resumed_chunks", 0) == 0
    assert "retired_at" not in utt, "the window should close once it has passed"


@pytest.mark.asyncio
async def test_audio_outside_any_interrupt_is_never_counted():
    a = _adapter()
    await a.send_tts_audio("call-1", b"\x7f" * 160)
    assert a._tts_utterances["call-1"].get("total_resumed_chunks", 0) == 0


def test_a_clean_call_still_states_its_verdict(caplog):
    """The verdict must be WRITTEN, not implied by absence. A line that only
    appears on failure is indistinguishable from a check that never ran — the
    precise reporting defect that hid two dead STT streams."""
    a = _adapter()
    a._tts_utterances["call-1"] = {
        "utterance_id": "x", "seq": 0,
        "total_interrupts": 3, "total_stale_rejected": 5,
    }
    with caplog.at_level("INFO"):
        a._emit_interrupt_audio_audit("call-1")

    assert "interrupt_audio_audit" in caplog.text
    assert "verdict=clean" in caplog.text
    assert "stale_rejected=5" in caplog.text


def test_a_resumption_is_reported_as_a_warning(caplog):
    a = _adapter()
    a._tts_utterances["call-1"] = {
        "utterance_id": "x", "seq": 0,
        "total_interrupts": 1, "total_resumed_chunks": 7,
    }
    with caplog.at_level("INFO"):
        a._emit_interrupt_audio_audit("call-1")

    assert "verdict=AUDIO_RESUMED" in caplog.text
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_a_call_with_no_barge_in_says_nothing(caplog):
    """Silence is correct here — there was no interruption to attest to, and a
    line claiming 'clean' would inflate the denominator."""
    a = _adapter()
    a._tts_utterances["call-1"] = {"utterance_id": "x", "seq": 0}
    with caplog.at_level("INFO"):
        a._emit_interrupt_audio_audit("call-1")
    assert "interrupt_audio_audit" not in caplog.text
