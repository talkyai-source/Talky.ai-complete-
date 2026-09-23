"""latency-tracker-negative (day0923 forensics).

mark_audio_start / mark_tts_end are plain first-write-wins (`if ... is
None: set`), unlike mark_tts_start, which already discards a stamp older
than the current turn's llm_start_time (2026-08-12 fix, for the identical
class of bug on tts_first_chunk_time). A silence-monitor nudge speaks
through the SAME synthesize_and_send_audio path before the caller has said
anything for the turn, stamping tts_end_time/audio_start_time early; when
the real reply lands minutes-of-monotonic-time later, those stale, early
stamps survive and the turn's own (later) speech_end_time/tts_start_time
subtract against them negative:

    day0923/3a17c06c.talky-api.log:104  10:33:01.09 [SilenceMonitor] nudging: Hello?
    day0923/3a17c06c.talky-api.log:127  10:33:05.03 Flux EndOfTurn (turn_end)
    day0923/3a17c06c.talky-api.log:143  10:33:07.91 [OK] Turn 0 latency: -3795ms (... TTS-total: -4132ms)
"""
from __future__ import annotations

import pytest

from app.domain.services.latency_tracker import LatencyMetrics, LatencyTracker


def test_a_preturn_nudge_no_longer_makes_total_latency_negative(monkeypatch):
    # Chronology mirrors 3a17c06c: nudge fires early in the turn (before the
    # caller has spoken), then the real turn happens much later.
    times = iter([
        100.0,  # start_turn -> listening_start_time / monotonic_anchor
        101.0,  # nudge: mark_tts_start -> tts_start_time
        101.1,  # nudge: mark_tts_first_chunk -> tts_first_chunk_time
        101.2,  # nudge: mark_response_start -> response_start_time + audio_start_time
        101.3,  # nudge: mark_tts_end -> tts_end_time
        105.0,  # caller actually speaks: mark_speech_end -> speech_end_time
        105.1,  # mark_llm_start -> llm_start_time
        106.0,  # mark_llm_end
        107.0,  # real reply: mark_tts_start -> fresh tts_start_time (+ clears stale tts_first_chunk_time)
        108.0,  # real reply: mark_tts_end
        108.1,  # real reply: mark_audio_start
    ])
    monkeypatch.setattr(
        "app.domain.services.latency_tracker.time.monotonic", lambda: next(times)
    )

    tracker = LatencyTracker()
    call_id = "3a17c06c"
    tracker.start_turn(call_id, 0)

    # The nudge — fire-and-forget through the same TTS path, no start_turn.
    tracker.mark_tts_start(call_id)
    tracker.mark_tts_first_chunk(call_id)
    tracker.mark_response_start(call_id)
    tracker.mark_tts_end(call_id)

    # The real turn.
    tracker.mark_speech_end(call_id)
    tracker.mark_llm_start(call_id)
    tracker.mark_llm_end(call_id)
    tracker.mark_tts_start(call_id)
    tracker.mark_tts_end(call_id)
    tracker.mark_audio_start(call_id)

    metrics = tracker._metrics[call_id]
    assert metrics.total_latency_ms is not None
    assert metrics.total_latency_ms >= 0, f"total_latency_ms went negative: {metrics.total_latency_ms}"
    assert metrics.tts_latency_ms is not None
    assert metrics.tts_latency_ms >= 0, f"tts_latency_ms went negative: {metrics.tts_latency_ms}"
    # Sanity: the real, un-poisoned numbers.
    assert metrics.total_latency_ms == pytest.approx((108.1 - 105.0) * 1000)
    assert metrics.tts_latency_ms == pytest.approx((108.0 - 107.0) * 1000)


def test_total_latency_ms_clamps_negative_to_none():
    """Defense in depth alongside the staleness guard above — mirrors the
    clamp tts_first_chunk_ms already applies."""
    metrics = LatencyMetrics(
        call_id="c", turn_id=0, speech_end_time=10.0, audio_start_time=9.0,
    )
    assert metrics.total_latency_ms is None


def test_tts_latency_ms_clamps_negative_to_none():
    metrics = LatencyMetrics(
        call_id="c", turn_id=0, tts_start_time=10.0, tts_end_time=9.0,
    )
    assert metrics.tts_latency_ms is None


def test_positive_total_and_tts_latency_are_unaffected():
    metrics = LatencyMetrics(
        call_id="c", turn_id=0,
        speech_end_time=10.0, audio_start_time=10.5,
        tts_start_time=10.1, tts_end_time=10.4,
    )
    assert metrics.total_latency_ms == pytest.approx(500.0)
    assert metrics.tts_latency_ms == pytest.approx(300.0)


def test_empty_final_turn_still_reports_na_not_a_number():
    """day0923/a5e033c7.talky-api.log turn 16 and b97ce4c5.talky-api.log turn
    23: llm_response said='' + reason=user_goodbye, TTS never invoked. This
    is a genuinely different case from the nudge bug above — no stamps at
    all, not stale ones — and must keep reporting None, not 0 or a clamp
    artifact."""
    metrics = LatencyMetrics(call_id="c", turn_id=16, speech_end_time=10.0)
    assert metrics.total_latency_ms is None
    assert metrics.tts_latency_ms is None
