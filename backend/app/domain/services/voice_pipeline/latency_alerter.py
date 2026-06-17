"""Process-wide rolling-P95 latency alerter.

Per-turn latency is already logged and histogrammed — but nobody is *watching*.
A gradual P95 regression (provider slowdown, prompt bloat, contention) stays
invisible until someone eyeballs a dashboard. This keeps a sliding window of
recent turn latencies across ALL calls and, when the window P95 crosses a
threshold, emits a WARNING log + Prometheus gauge/counter — a paging signal that
needs no external query — plus a recovery log when it drops back.

Single-process / single-thread by design (the voice API runs uvicorn
--workers 1; the event loop serialises access), so no lock is needed. Fully
fail-soft: recording a sample must never affect a call.

Tunables (env):
  VOICE_P95_WINDOW          max samples kept            (default 120)
  VOICE_P95_WINDOW_S        max sample age, seconds     (default 300)
  VOICE_P95_MIN_SAMPLES     min samples before alerting (default 20)
  VOICE_P95_ALERT_MS        fire at/above this P95 (ms) (default 1500)
  VOICE_P95_CLEAR_MS        recover at/below this (ms)  (default 1100)
  VOICE_P95_COOLDOWN_S      min seconds between re-fires(default 120)
"""
from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Deque, Optional, Tuple

logger = logging.getLogger(__name__)

_WINDOW = int(os.getenv("VOICE_P95_WINDOW", "120"))
_WINDOW_S = float(os.getenv("VOICE_P95_WINDOW_S", "300"))
_MIN_SAMPLES = int(os.getenv("VOICE_P95_MIN_SAMPLES", "20"))
_ALERT_MS = float(os.getenv("VOICE_P95_ALERT_MS", "1500"))
# Hysteresis: clear below a LOWER threshold than we fire at, so a P95 hovering
# near the line doesn't flap firing/cleared every turn.
_CLEAR_MS = float(os.getenv("VOICE_P95_CLEAR_MS", "1100"))
_COOLDOWN_S = float(os.getenv("VOICE_P95_COOLDOWN_S", "120"))


def _percentile(sorted_vals: list, pct: float) -> float:
    """Linear-interpolation percentile over a pre-sorted list (non-empty)."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_vals) - 1)
    frac = rank - low
    return sorted_vals[low] + (sorted_vals[high] - sorted_vals[low]) * frac


class LatencyAlerter:
    """Sliding-window P95 watcher with hysteresis + cooldown."""

    def __init__(self) -> None:
        self._samples: Deque[Tuple[float, float]] = deque(maxlen=_WINDOW)
        self._firing = False
        # -inf so the first-ever alert is never blocked by the cooldown window
        # (monotonic clocks start arbitrarily; 0.0 would gate an early alert).
        self._last_alert_ts = float("-inf")

    def _prune(self, now: float) -> None:
        cutoff = now - _WINDOW_S
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def record(self, latency_ms: float, *, now: Optional[float] = None) -> Optional[float]:
        """Add one turn's total latency (ms); return the current window P95 (or
        None if too few samples). Emits the alert/recovery side effects."""
        if latency_ms is None or latency_ms < 0:
            return None
        now = time.monotonic() if now is None else now
        self._samples.append((now, float(latency_ms)))
        self._prune(now)

        if len(self._samples) < _MIN_SAMPLES:
            return None

        vals = sorted(v for _, v in self._samples)
        p95 = _percentile(vals, 95.0)
        self._publish_gauge(p95)

        if not self._firing:
            if p95 >= _ALERT_MS and (now - self._last_alert_ts) >= _COOLDOWN_S:
                self._firing = True
                self._last_alert_ts = now
                logger.warning(
                    "p95_latency_alert state=firing p95_ms=%.0f threshold_ms=%.0f "
                    "samples=%d window_s=%.0f — turn latency is degraded",
                    p95, _ALERT_MS, len(self._samples), _WINDOW_S,
                )
                self._record_transition("firing")
        else:
            if p95 <= _CLEAR_MS:
                self._firing = False
                logger.info(
                    "p95_latency_alert state=cleared p95_ms=%.0f clear_ms=%.0f samples=%d "
                    "— turn latency recovered",
                    p95, _CLEAR_MS, len(self._samples),
                )
                self._record_transition("cleared")
        return p95

    def current_p95(self) -> Optional[float]:
        """Window P95 right now, or None if too few samples."""
        if len(self._samples) < _MIN_SAMPLES:
            return None
        return _percentile(sorted(v for _, v in self._samples), 95.0)

    # — side effects isolated so tests can run without prometheus_client —
    @staticmethod
    def _publish_gauge(p95: float) -> None:
        try:
            from app.infrastructure.metrics.voice_metrics import set_turn_latency_p95_ms
            set_turn_latency_p95_ms(p95)
        except Exception as exc:  # noqa: BLE001 — metrics must never break a call
            logger.debug("p95_gauge_publish_failed err=%s", exc)

    @staticmethod
    def _record_transition(state: str) -> None:
        try:
            from app.infrastructure.metrics.voice_metrics import record_p95_alert
            record_p95_alert(state)
        except Exception as exc:  # noqa: BLE001
            logger.debug("p95_alert_record_failed err=%s", exc)


_alerter: Optional[LatencyAlerter] = None


def get_latency_alerter() -> LatencyAlerter:
    global _alerter
    if _alerter is None:
        _alerter = LatencyAlerter()
    return _alerter


def record_turn_latency_ms(latency_ms: float) -> None:
    """Record one completed turn's total latency into the rolling P95 watcher.
    Fail-soft — never raises into the call path."""
    try:
        get_latency_alerter().record(latency_ms)
    except Exception as exc:  # noqa: BLE001
        logger.debug("latency_alerter_record_failed err=%s", exc)
