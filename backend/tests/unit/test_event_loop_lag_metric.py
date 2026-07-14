"""Tests for the event-loop scheduling-lag heartbeat metric.

Covers three things:

1. ``observe_event_loop_lag_seconds`` records a sample into its dedicated
   fine-grained histogram (and ignores bad values).
2. The observer is FAIL-SOFT — a broken underlying collector must never raise,
   because it is driven by a hot heartbeat task that cannot be allowed to die.
3. The real ``_event_loop_lag_heartbeat`` coroutine (from ``app.main``) actually
   records lag observations when driven for a few iterations and stops cleanly
   on its stop-event — proving the end-to-end wiring, not a replica.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from prometheus_client import REGISTRY

import app.infrastructure.metrics.voice_metrics as vm
from app.infrastructure.metrics.voice_metrics import (
    observe_event_loop_lag_seconds,
)


def _histogram_count(name: str) -> float:
    """Read the unlabelled histogram's total observation count from the
    registry, the same way Grafana/prom2json would scrape it."""
    for collector in REGISTRY.collect():
        if collector.name != name:
            continue
        for sample in collector.samples:
            if sample.name == f"{name}_count" and sample.labels == {}:
                return sample.value
    return 0.0


class TestObserveEventLoopLag:
    def test_valid_observation_is_recorded(self):
        before = _histogram_count("voice_event_loop_lag_seconds")
        observe_event_loop_lag_seconds(0.012)
        after = _histogram_count("voice_event_loop_lag_seconds")
        assert after == before + 1

    def test_negative_and_none_are_ignored(self):
        before = _histogram_count("voice_event_loop_lag_seconds")
        observe_event_loop_lag_seconds(-0.001)
        observe_event_loop_lag_seconds(None)  # type: ignore[arg-type]
        after = _histogram_count("voice_event_loop_lag_seconds")
        assert after == before

    def test_dedicated_fine_grained_buckets(self):
        # A 5ms lag must land in a sub-250ms bucket that the per-turn latency
        # buckets could never resolve — proves the metric has its own buckets.
        assert 0.005 in vm._EVENT_LOOP_LAG_BUCKETS_S
        assert min(vm._EVENT_LOOP_LAG_BUCKETS_S) <= 0.001

    def test_fail_soft_when_collector_raises(self, monkeypatch):
        class _Boom:
            def observe(self, _v):
                raise RuntimeError("prometheus exploded")

        monkeypatch.setattr(vm, "_event_loop_lag", _Boom())
        # Must NOT raise — the heartbeat depends on this contract.
        observe_event_loop_lag_seconds(0.02)


@pytest.mark.asyncio
async def test_heartbeat_records_observations_and_stops_cleanly():
    """Drive the REAL heartbeat coroutine with a short-lived stop-event and
    confirm it (a) records lag observations and (b) returns promptly when the
    stop-event is set — the same drain path lifespan uses on shutdown."""
    from app.main import _event_loop_lag_heartbeat

    before = _histogram_count("voice_event_loop_lag_seconds")
    stop = asyncio.Event()
    task = asyncio.create_task(_event_loop_lag_heartbeat(stop))

    # 10ms period → ~5+ ticks in 80ms.
    await asyncio.sleep(0.08)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)

    after = _histogram_count("voice_event_loop_lag_seconds")
    assert after >= before + 3
    assert task.done() and task.exception() is None


@pytest.mark.asyncio
async def test_heartbeat_stall_yields_single_lag_sample(monkeypatch):
    """A single long stall must produce exactly ONE lag observation (the
    resync), not a catch-up burst of zero-sleep samples for the same stall.

    Blocking the event loop synchronously for 200ms (20x the 10ms period)
    reproduces a real production stall: every scheduled callback, including
    the heartbeat's pending ``sleep``, is starved for that whole window and
    then wakes up all at once. Without the resync fix, the un-advanced
    deadline stays in the past and the next ~20 iterations would each
    sleep(0) and emit their own lag sample for that one stall.
    """
    from app.main import _event_loop_lag_heartbeat

    samples: list[float] = []
    monkeypatch.setattr(
        vm, "observe_event_loop_lag_seconds", lambda lag: samples.append(lag)
    )

    stop = asyncio.Event()
    task = asyncio.create_task(_event_loop_lag_heartbeat(stop))

    # Let the heartbeat arm its first deadline and take at least one normal
    # tick before the stall.
    await asyncio.sleep(0.03)

    # Simulate the stall: a synchronous blocking call on the event loop
    # thread (not run_in_executor) starves every other coroutine, exactly
    # like a slow blocking call in production would.
    time.sleep(0.2)

    # Let the loop resume and process the single overdue wakeup, then a few
    # more normal ticks to prove steady-state cadence resumed.
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)

    stall_samples = [s for s in samples if s > 0.05]
    assert len(stall_samples) == 1, (
        f"expected exactly one stall sample, got {len(stall_samples)}: {samples}"
    )


@pytest.mark.asyncio
async def test_heartbeat_cancels_cleanly():
    """A cancelled heartbeat (the hard-shutdown path) propagates CancelledError
    rather than swallowing it — so ``asyncio.gather`` can reap it."""
    from app.main import _event_loop_lag_heartbeat

    stop = asyncio.Event()
    task = asyncio.create_task(_event_loop_lag_heartbeat(stop))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
