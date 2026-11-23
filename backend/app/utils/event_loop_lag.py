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

Two observations help investigation, but neither establishes the source:

* The delivered-audio ratio describes batching over the observed interval.
  Ratios near one are consistent with bunching, but cannot exclude packet loss.
* ``current_ms()`` is the most recent scheduling sample, not a measurement
  covering the entire audio gap. A healthy latest sample cannot exonerate this
  process or assign fault to the carrier. Two peaks are printed: the largest
  sample since the process started, and the largest since a given time (the
  call's first audio batch).

``None`` is reported distinctly from ``0.0`` throughout: "we did not measure"
and "the loop was healthy" are different claims, and conflating them is exactly
the ambiguity this exists to remove.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional, Tuple

# Lag at or below this is ordinary scheduler noise on a healthy loop and should
# not be read as a stall.
_NOISE_FLOOR_MS = 2.0

# Samples above the noise floor as (time.monotonic(), ms), newest last, kept so
# that the largest sample since ANY later time is still there: before a sample
# is added, every older one that is not larger is dropped (it can never again be
# the peak of a window that includes the new one). What remains is in time order
# with falling values, so it stays short, and the peak since ``t`` is exact
# however long the call is. time.monotonic() is the clock behind asyncio's
# loop.time(), so a value from ``loop.time()`` (e.g. a call's first audio batch)
# can be compared directly.
_RECENT_MAX = 256
_recent: Deque[Tuple[float, float]] = deque(maxlen=_RECENT_MAX)

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
    if ms > _NOISE_FLOOR_MS:
        while _recent and _recent[-1][1] <= ms:
            _recent.pop()
        _recent.append((time.monotonic(), ms))


def current_ms() -> Optional[float]:
    """Most recent lag in ms, or ``None`` when nothing has been recorded yet."""
    return _last_ms


def peak_ms() -> Optional[float]:
    """Worst lag seen since process start, or ``None`` if never recorded."""
    return _peak_ms if _last_ms is not None else None


def peak_since(t: float) -> Optional[float]:
    """Largest sample above the noise floor recorded at or after ``t``
    (a ``time.monotonic()`` / ``loop.time()`` value); 0.0 when there was none,
    ``None`` when nothing was ever recorded."""
    if _last_ms is None:
        return None
    return max((ms for at, ms in _recent if at >= t), default=0.0)


def reset() -> None:
    """Test hook — clears the published state."""
    global _last_ms, _peak_ms
    _last_ms = None
    _peak_ms = 0.0
    _recent.clear()


def describe(since: Optional[float] = None, source: str = "undetermined") -> str:
    """Compact summary for embedding in another log line.

    A recent scheduling observation is not a causal verdict about an audio
    gap. A healthy sample cannot exonerate earlier stalls, the gateway or the
    callback transport; a stalled sample cannot prove the carrier was healthy.

    ``loop_lag_process_peak_ms`` is the largest sample since the PROCESS
    started. ``loop_lag_call_peak_ms`` (only when ``since`` is given) is the
    largest since that time, e.g. the call's first audio batch. ``source`` is
    log text supplied by the caller; nothing decides anything from it.
    """
    if _last_ms is None:
        if source != "undetermined":
            return f"loop_lag=unmeasured source={source}"
        return "loop_lag=unmeasured"
    observation = "stalled" if _last_ms > _NOISE_FLOOR_MS else "healthy"
    call_peak = ""
    if since is not None:
        call_peak = f"loop_lag_call_peak_ms={peak_since(since):.1f} "
    return (
        f"loop_lag_ms={_last_ms:.1f} {call_peak}loop_lag_process_peak_ms={_peak_ms:.1f} "
        f"loop_recent={observation} source={source}"
    )
