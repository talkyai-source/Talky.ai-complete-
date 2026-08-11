"""The TTS pre-buffer must cover the provider's generation latency.

WHY THIS EXISTS (2026-08-07)
----------------------------
`tts_playback` awaits `TelephonyMediaGateway.send_audio` per chunk INSIDE its
`async for` over the TTS generator. Pacing therefore suspends the generator at
`yield`, so at a sentence boundary the NEXT sentence's synthesis cannot begin
until the previous one has drained to within TARGET_AHEAD_S.

Cartesia's generation latency is ~250ms. With a 200ms lead, ~50ms was left
uncovered at every sentence boundary: the agent stops dead, pauses, resumes.
With `response_max_sentences = 3` that is up to two gaps per turn.

THE ALTERNATIVE WAS UNSAFE. Prefetching sentence N+1 while N plays looks like
the obvious fix, but the generator being suspended for the whole audible
duration means CartesiaTTSProvider's per-call lock is held that long too. A
concurrent synthesis blocks on it and, after `_WS_LOCK_ACQUIRE_TIMEOUT_S = 2.0`,
force-swaps the lock and sends `{"cancel": true}` for the in-flight context —
truncating the sentence the caller is currently hearing, for any sentence longer
than ~2s of speech. See test_turn_streamer_tts_overlap.py, which demonstrates
that against the real provider.

RAISING THE LEAD DOES NOT COST BARGE-IN RESPONSIVENESS — the two properties the
old 200ms comment was protecting:

  * DETECTION: the pacing wait is `wait_for(barge_in_event.wait(), timeout=
    overshoot)`, which returns within microseconds of StartOfTurn. A longer
    window is just a longer thing to interrupt, not a longer wait.
  * THE STOP: `clear_output_buffer` flushes BOTH the local packetisation buffer
    AND the C++ gateway's internal queue, so buffered audio is DROPPED rather
    than played out.

These tests pin all of that, because "raise the buffer" is exactly the kind of
change someone later reverts for the barge-in reason that does not actually
apply.
"""
from __future__ import annotations

import os

import pytest

from tests.unit._source_scan import code, function_body

_GATEWAY = "app/infrastructure/telephony/telephony_media_gateway.py"

# Cartesia's documented streaming generation latency floor.
_PROVIDER_GENERATION_LATENCY_S = 0.250


def _send_audio_body() -> str:
    return function_body(code(_GATEWAY), "send_audio")


def _target_ahead_default() -> float:
    """The default the code would use with no env override."""
    body = _send_audio_body()
    # The literal appears twice: the getenv default and the except fallback.
    import re

    hits = re.findall(r'TELEPHONY_TTS_TARGET_AHEAD_S",\s*"([0-9.]+)"', body)
    assert hits, "TARGET_AHEAD_S is no longer env-tunable"
    return float(hits[0])


def test_lead_covers_provider_generation_latency():
    """THE REGRESSION. A lead shorter than generation latency leaves an audible
    gap at every sentence boundary."""
    lead = _target_ahead_default()
    assert lead >= _PROVIDER_GENERATION_LATENCY_S, (
        f"TARGET_AHEAD_S={lead}s is below the ~{_PROVIDER_GENERATION_LATENCY_S}s "
        "provider generation latency — the agent will pause between sentences"
    )


def test_lead_is_not_raised_without_bound():
    """A very large lead hands the gateway more audio than clear_output_buffer
    can usefully drop, and delays how quickly a changed reply reaches the ear.
    """
    assert _target_ahead_default() <= 0.500


def test_lead_is_env_tunable_for_rollback():
    """This is a live-audio behaviour change; it must be revertible without a
    deploy."""
    body = _send_audio_body()
    assert "TELEPHONY_TTS_TARGET_AHEAD_S" in body
    assert "except ValueError" in body, (
        "a malformed env value must fall back to the default, not crash the "
        "audio send path mid-call"
    )


@pytest.mark.parametrize(
    "raw,expected",
    [("0.300", 0.300), ("0.4", 0.4), ("garbage", 0.300), ("", 0.300)],
)
def test_env_override_parsing(raw, expected, monkeypatch):
    """Mirrors the parse in send_audio: bad input must not raise."""
    monkeypatch.setenv("TELEPHONY_TTS_TARGET_AHEAD_S", raw)
    try:
        value = float(os.getenv("TELEPHONY_TTS_TARGET_AHEAD_S", "0.300"))
    except ValueError:
        value = 0.300
    assert value == pytest.approx(expected)


# --------------------------------------------------------------------------
# The two properties that make this safe — pinned so a future reader does not
# revert the lead for a reason that does not apply.
# --------------------------------------------------------------------------

def test_barge_in_interrupts_the_pacing_wait():
    """Detection must not scale with the lead."""
    body = _send_audio_body()
    assert "barge_in_event.wait()" in body, (
        "the pacing wait must be interruptible by barge-in; a plain sleep here "
        "WOULD make a larger lead cost barge-in latency"
    )
    assert "timeout=overshoot" in body


def test_clear_output_buffer_drops_the_gateway_queue():
    """The stop must not scale with the lead either."""
    body = function_body(code(_GATEWAY), "clear_output_buffer")
    lowered = body.lower()
    assert "tts_buffer" in lowered
    assert "interrupt_tts" in lowered, (
        "clear_output_buffer must flush the C++ gateway queue, not just the "
        "local buffer — otherwise buffered audio plays on after a barge-in"
    )


# ── 2026-08-12: negative latency from a silence nudge ──────────────────────

def test_a_first_chunk_stamp_from_before_the_turn_is_discarded():
    """THE REGRESSION. Silence nudges ("Hello?", "Still there?") speak through
    the same TTS path and stamp tts_first_chunk_time, but a nudge is not a turn
    and never calls start_turn — so the stamp landed in the CURRENT turn's
    metrics. mark_tts_first_chunk is first-wins, so it was never corrected, and
    when the real reply set a LATER tts_start_time the subtraction went
    negative:

        Turn 2 latency: -2526ms (TTS-first-chunk: -2894ms)
    """
    from app.domain.services.latency_tracker import LatencyTracker

    t = LatencyTracker()
    t.start_turn("c1", 1)
    m = t._metrics["c1"]

    m.tts_first_chunk_time = 100.0     # the nudge's audio
    m.llm_start_time = 105.0           # this turn started thinking AFTER it
    t.mark_tts_start("c1")             # the real reply's TTS begins now

    assert m.tts_first_chunk_time is None, (
        "audio emitted before this turn's LLM started cannot belong to it"
    )
    assert m.tts_first_chunk_ms is None


def test_a_legitimate_first_chunk_is_kept():
    """Non-vacuity — a stamp from AFTER the LLM started is this turn's."""
    from app.domain.services.latency_tracker import LatencyTracker

    t = LatencyTracker()
    t.start_turn("c2", 1)
    m = t._metrics["c2"]

    m.llm_start_time = 100.0
    m.tts_first_chunk_time = 106.0     # after the LLM started — ours
    t.mark_tts_start("c2")

    assert m.tts_first_chunk_time == 106.0


def test_a_negative_measurement_is_reported_as_unmeasurable_not_fast():
    """Belt and braces: even if two stamps ever disagree again, the property
    must not hand a negative number to the P95 alerter."""
    from app.domain.services.latency_tracker import LatencyMetrics

    m = LatencyMetrics(call_id="c", turn_id=1)
    m.tts_start_time = 200.0
    m.tts_first_chunk_time = 190.0
    assert m.tts_first_chunk_ms is None
