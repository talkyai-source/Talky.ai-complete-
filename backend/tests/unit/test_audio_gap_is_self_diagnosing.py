"""The audio-gap warning has to answer its own question.

2026-08-13 produced 421 ``telephony_audio_gap`` warnings across 36 of 38 calls.
Every one named three possible causes — gateway callback drop, event-loop
stall, upstream RTP loss — and distinguished none of them, so not one was
actionable. The comment above the log call even records that naming a suspect
had previously sent an investigation down the wrong path.

Reconstructing the answer afterwards from the ``audio_level`` sample counts of
that run gave a delivery ratio of p50 exactly 1.000 / mean 1.0089, with 3.4% of
one-second windows short against 3.1% long — near-symmetric, which is bunching,
not loss. Nothing was ever lost. "RTP loss" could have been struck off on day
one if the warning had carried the number.

So the fix is not a new theory about the cause; it is two fields that make the
NEXT occurrence self-diagnosing:

  arrived_ratio  ~1.0 => nothing lost, purely a timing artefact
                 <1.0 sustained => real loss, a different problem
  loop_lag_ms    high => we were too busy to take the callback; ours to fix
                 ~0   => we were idle and waiting; it arrived late from outside

These tests pin both, and pin that "unmeasured" stays distinguishable from
"healthy" — conflating those two is the ambiguity the whole change exists to
remove.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway
from app.utils import event_loop_lag


@pytest.fixture(autouse=True)
def _clean_lag():
    event_loop_lag.reset()
    yield
    event_loop_lag.reset()


# ── the readable lag view ────────────────────────────────────────────────────

def test_unmeasured_is_not_the_same_as_healthy():
    """THE distinction. A gap warning that says loop_lag_ms=0.0 when nothing
    was ever sampled is asserting the loop was fine on no evidence."""
    assert event_loop_lag.current_ms() is None
    assert event_loop_lag.peak_ms() is None
    assert event_loop_lag.describe() == "loop_lag=unmeasured"

    event_loop_lag.record(0.0)
    assert event_loop_lag.current_ms() == 0.0
    assert "unmeasured" not in event_loop_lag.describe()


def test_a_healthy_loop_reads_as_not_ours():
    event_loop_lag.record(0.0005)          # 0.5ms — ordinary scheduler noise
    assert "stall=not-ours" in event_loop_lag.describe()


def test_a_stalled_loop_reads_as_ours():
    event_loop_lag.record(0.250)           # 250ms — we were blocked
    d = event_loop_lag.describe()
    assert "stall=ours" in d
    assert "loop_lag_ms=250.0" in d


def test_peak_is_sticky_but_current_tracks_the_latest():
    """A gap warning wants both: what the loop is doing right now, and whether
    this process has ever stalled badly during the call."""
    event_loop_lag.record(0.300)
    event_loop_lag.record(0.001)
    assert event_loop_lag.current_ms() == pytest.approx(1.0)
    assert event_loop_lag.peak_ms() == pytest.approx(300.0)


def test_negative_lag_is_clamped():
    """Clock adjustments must not produce a negative stall."""
    event_loop_lag.record(-0.5)
    assert event_loop_lag.current_ms() == 0.0


def test_record_is_cheap_enough_for_the_heartbeat():
    """It is called at 100Hz for the life of the process, so it must stay two
    floats and a comparison — no allocation, no I/O, no locking."""
    for i in range(100_000):
        event_loop_lag.record(i % 7 / 1000.0)
    assert event_loop_lag.current_ms() is not None


# ── the warning itself ───────────────────────────────────────────────────────

async def _gateway_with_session(call_id: str) -> TelephonyMediaGateway:
    gateway = TelephonyMediaGateway()
    # 8kHz internal rate so on_audio_received skips the resample (no soxr
    # needed here) — the gap branch runs before any decoding regardless.
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    await gateway.on_call_started(
        call_id, {"adapter": AsyncMock(), "pbx_call_id": f"pbx-{call_id}"},
    )
    return gateway


@pytest.mark.asyncio
async def test_gap_warning_carries_both_discriminators(caplog):
    call_id = "gap-diag"
    gateway = await _gateway_with_session(call_id)
    session = gateway._sessions[call_id]
    event_loop_lag.record(0.180)     # the loop WAS stalled

    frame = b"\xff" * 320           # 40ms of µ-law silence at 8kHz
    with caplog.at_level(logging.WARNING):
        await gateway.on_audio_received(call_id, frame)
        # Backdate the last-arrival stamp so the next callback looks late.
        session.last_audio_received_at -= 0.500
        await gateway.on_audio_received(call_id, frame)

    warnings = [r for r in caplog.records if "telephony_audio_gap" in r.getMessage()]
    assert warnings, "the late batch produced no gap warning"
    msg = warnings[-1].getMessage()
    assert "arrived_ratio=" in msg, "cannot tell late from lost"
    assert "loop_lag_ms=" in msg, "cannot tell whose stall it was"
    assert "stall=ours" in msg
    assert warnings[-1].loop_lag_ms == pytest.approx(180.0)


@pytest.mark.asyncio
async def test_gap_warning_says_not_ours_when_the_loop_was_idle(caplog):
    """The other half of the verdict — this is what rules our own code OUT."""
    call_id = "gap-external"
    gateway = await _gateway_with_session(call_id)
    session = gateway._sessions[call_id]
    event_loop_lag.record(0.0004)    # loop was fine

    frame = b"\xff" * 320
    with caplog.at_level(logging.WARNING):
        await gateway.on_audio_received(call_id, frame)
        session.last_audio_received_at -= 0.500
        await gateway.on_audio_received(call_id, frame)

    msg = [r for r in caplog.records if "telephony_audio_gap" in r.getMessage()][-1].getMessage()
    assert "stall=not-ours" in msg


@pytest.mark.asyncio
async def test_no_warning_when_audio_is_on_time(caplog):
    """Non-vacuity: the detector must still be quiet on a healthy stream, or
    the fields above are just noise on every call."""
    call_id = "gap-none"
    gateway = await _gateway_with_session(call_id)
    frame = b"\xff" * 320
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            await gateway.on_audio_received(call_id, frame)

    assert not [r for r in caplog.records if "telephony_audio_gap" in r.getMessage()]


@pytest.mark.asyncio
async def test_warning_survives_an_unmeasured_loop(caplog):
    """The heartbeat may not have ticked yet on a very early call. The warning
    must still be emitted — degraded, not missing."""
    call_id = "gap-unmeasured"
    gateway = await _gateway_with_session(call_id)
    session = gateway._sessions[call_id]

    frame = b"\xff" * 320
    with caplog.at_level(logging.WARNING):
        await gateway.on_audio_received(call_id, frame)
        session.last_audio_received_at -= 0.500
        await gateway.on_audio_received(call_id, frame)

    msg = [r for r in caplog.records if "telephony_audio_gap" in r.getMessage()][-1].getMessage()
    assert "loop_lag=unmeasured" in msg
