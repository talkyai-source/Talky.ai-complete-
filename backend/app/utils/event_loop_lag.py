"""Readable view of the event loop's most recent scheduling lag.

This module deliberately owns NO timer. The measurement already exists:
``main._event_loop_lag_heartbeat`` wakes on 10ms absolute deadlines, resyncs
after a stall so one stall yields exactly one sample, and feeds the
``observe_event_loop_lag_seconds`` histogram. A second sampler would measure
the same loop worse and disagree with it under load.

What was missing is not another measurement — it is a way to read the current
one SYNCHRONOUSLY, at the instant something goes wrong. A Prometheus histogram
answers "how has the loop been lately"; it cannot answer "was the loop stalled
when THIS audio batch arrived late", because that requires a value in hand at
the moment of the log call.

WHY THAT MATTERS
----------------
``telephony_audio_gap`` fires when an inbound audio batch arrives later than
expected, and it names three possible causes while being able to distinguish
none of them: a gateway callback drop, a stall in this process, or upstream RTP
loss. On 2026-08-13 that produced 421 warnings across 36 of 38 calls that
nobody could act on.

Two facts settle it, and both are now attached to the warning:

* **Was audio lost?** Reconstructed after the fact for that run from the
  ``audio_level`` sample counts: delivery ratio p50 exactly 1.000, mean 1.0089,
  3.4% of one-second windows short against 3.1% long. Near-symmetric — the
  signature of BUNCHING, not loss. Every byte arrived, some of it late and in
  bursts. RTP loss was never the cause and could have been struck off on day
  one. That ratio is now computed live, per warning.
* **Could this process run?** ``current_ms()`` below. High at gap time means we
  were too busy to service the callback and it is ours to fix; ~0 means we were
  idle and waiting, so the lateness came from outside.

``None`` is reported distinctly from ``0.0`` throughout: "we did not measure"
and "the loop was healthy" are different claims, and conflating them is exactly
the ambiguity this exists to remove.
"""
from __future__ import annotations

from typing import Optional

# Lag at or below this is ordinary scheduler noise on a healthy loop and should
# not be read as a stall.
_NOISE_FLOOR_MS = 2.0

_last_ms: Optional[float] = None
_peak_ms: float = 0.0


def record(lag_seconds: float) -> None:
    """Publish one observation. Called by the existing heartbeat alongside its
    histogram write — it is the producer; this module never samples for itself.

    Cheap enough for a 10Hz..100Hz caller: two floats and a comparison.
    """
    global _last_ms, _peak_ms
    ms = lag_seconds * 1000.0
    if ms < 0:
        ms = 0.0
    _last_ms = ms
    if ms > _peak_ms:
        _peak_ms = ms


def current_ms() -> Optional[float]:
    """Most recent lag in ms, or ``None`` when nothing has been recorded yet."""
    return _last_ms


def peak_ms() -> Optional[float]:
    """Worst lag seen since process start, or ``None`` if never recorded."""
    return _peak_ms if _last_ms is not None else None


def reset() -> None:
    """Test hook — clears the published state."""
    global _last_ms, _peak_ms
    _last_ms = None
    _peak_ms = 0.0


def describe() -> str:
    """Compact summary for embedding in another log line.

    Emits ``stall=ours`` / ``stall=not-ours`` so the reader of a gap warning
    gets the verdict without having to remember what a healthy lag looks like.
    """
    if _last_ms is None:
        return "loop_lag=unmeasured"
    verdict = "ours" if _last_ms > _NOISE_FLOOR_MS else "not-ours"
    return f"loop_lag_ms={_last_ms:.1f} loop_lag_peak_ms={_peak_ms:.1f} stall={verdict}"
