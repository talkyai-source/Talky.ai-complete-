"""Tests for the barge-in TTS-stop latency metric (gap #3).

Measures how fast the agent's audio is silenced after the caller interrupts.
"""
from __future__ import annotations

import time

import app.infrastructure.metrics.voice_metrics as vm
from app.domain.services.voice_pipeline.tts_playback import TtsPlayback


class _FakeSession:
    """Minimal stand-in — the helper only touches _barge_in_set_monotonic."""


def test_observe_barge_in_stop_ms_accepts_and_rejects():
    # Valid value records; junk values are ignored without raising.
    vm.observe_barge_in_stop_ms(45.0)
    vm.observe_barge_in_stop_ms(0.0)
    vm.observe_barge_in_stop_ms(-1.0)      # negative ignored
    vm.observe_barge_in_stop_ms(None)      # type: ignore[arg-type]


def test_record_barge_in_stop_observes_and_resets(monkeypatch):
    seen: list[float] = []
    monkeypatch.setattr(vm, "observe_barge_in_stop_ms", lambda ms: seen.append(ms))

    pb = TtsPlayback(pipeline=object())
    session = _FakeSession()
    session._barge_in_set_monotonic = time.monotonic() - 0.04  # barge-in ~40ms ago

    pb._record_barge_in_stop(session)

    assert len(seen) == 1
    assert 20.0 <= seen[0] <= 250.0            # ~40ms, generous scheduling slack
    assert session._barge_in_set_monotonic is None   # stamp consumed


def test_record_barge_in_stop_noop_without_stamp(monkeypatch):
    seen: list[float] = []
    monkeypatch.setattr(vm, "observe_barge_in_stop_ms", lambda ms: seen.append(ms))

    pb = TtsPlayback(pipeline=object())
    session = _FakeSession()
    session._barge_in_set_monotonic = None

    pb._record_barge_in_stop(session)

    assert seen == []                          # nothing observed
    assert session._barge_in_set_monotonic is None
