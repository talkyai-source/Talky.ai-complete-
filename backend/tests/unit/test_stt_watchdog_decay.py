"""The silent-stream watchdog must measure SUSTAINED unanswered speech.

Production, week to 2026-09-22: the primary speech engine was torn down and
replaced mid-call on 4 of 17 sessions. The counter behind that decision was a
monotonic sum of every chunk above an RMS gate, reset only when a transcript
arrived. It therefore measured the total quiet-line energy a call had ever
contained, so six seconds gathered a hundred milliseconds at a time over
several minutes tripped it exactly like six seconds of someone talking into a
dead stream. On any line with a little constant hum that is a matter of when.

Draining the counter while the line is quiet makes the window mean what its
name says. These tests pin that, and pin the two things the fix must not break:
genuine sustained speech still trips it, and the replay buffer still covers the
whole window so a real failover loses nothing.
"""
from __future__ import annotations

import struct

import pytest

from app.domain.services.resilient_stt import (
    ReconnectPolicy,
    _SPEECH_RMS_THRESHOLD,
    _WATCHDOG_DECAY_RATIO,
)
import app.domain.services.resilient_stt as rstt


class _Chunk:
    """Minimal stand-in for AudioChunk: the watchdog reads .data and
    .sample_rate and nothing else."""

    def __init__(self, data: bytes, sample_rate: int = 16000):
        self.data = data
        self.sample_rate = sample_rate


def _chunk(rms: int, ms: int = 20, rate: int = 16000) -> "_Chunk":
    """PCM16 mono at roughly the requested RMS, for `ms` milliseconds."""
    samples = int(rate * ms / 1000)
    return _Chunk(struct.pack("<%dh" % samples, *([rms] * samples)), rate)


def _watchdog(voiced_needed_s: float = 1.0):
    return rstt._SilentStreamWatchdog(voiced_seconds=voiced_needed_s)


LOUD = int(_SPEECH_RMS_THRESHOLD * 3)
QUIET = int(_SPEECH_RMS_THRESHOLD // 4)


def test_sustained_speech_still_trips_the_watchdog():
    # The real failure this exists for: the caller talks, nothing comes back.
    wd = _watchdog(voiced_needed_s=0.5)
    tripped = False
    for _ in range(40):  # 800ms of continuous voice
        tripped = wd.observe_audio(_chunk(LOUD), muted=False, agent_speaking=False)
        if tripped:
            break
    assert tripped, "continuous unanswered speech must still fail over"


def test_an_intermittently_noisy_line_never_trips():
    # Alternating voiced and quiet drains as fast as it fills, so a line that
    # is noisy half the time never reaches the window however long the call is.
    wd = _watchdog(voiced_needed_s=0.5)
    for _ in range(2000):  # 40 seconds, alternating
        assert not wd.observe_audio(_chunk(LOUD), muted=False, agent_speaking=False)
        assert not wd.observe_audio(_chunk(QUIET), muted=False, agent_speaking=False)


def test_quiet_drains_progress_already_made():
    wd = _watchdog(voiced_needed_s=1.0)
    for _ in range(20):  # 400ms of voice
        wd.observe_audio(_chunk(LOUD), muted=False, agent_speaking=False)
    banked = wd.voiced_ms
    assert banked > 0
    for _ in range(20):  # 400ms of quiet
        wd.observe_audio(_chunk(QUIET), muted=False, agent_speaking=False)
    assert wd.voiced_ms < banked
    assert wd.voiced_ms == pytest.approx(0.0, abs=1.0)


def test_the_counter_never_goes_negative():
    wd = _watchdog()
    for _ in range(100):
        wd.observe_audio(_chunk(QUIET), muted=False, agent_speaking=False)
    assert wd.voiced_ms == 0.0


def test_the_agents_own_voice_is_still_never_counted():
    # The 2026-08-18 regression: our TTS echoing back on a 2-wire line.
    wd = _watchdog(voiced_needed_s=0.2)
    for _ in range(200):
        assert not wd.observe_audio(_chunk(LOUD), muted=False, agent_speaking=True)


def test_decay_can_be_switched_off_for_a_deployment_that_needs_it():
    wd = rstt._SilentStreamWatchdog(voiced_seconds=1.0, decay_ratio=0.0)
    for _ in range(20):
        wd.observe_audio(_chunk(LOUD), muted=False, agent_speaking=False)
    banked = wd.voiced_ms
    for _ in range(50):
        wd.observe_audio(_chunk(QUIET), muted=False, agent_speaking=False)
    assert wd.voiced_ms == banked, "ratio 0.0 restores the old monotonic sum"


def test_the_default_ratio_is_one_to_one():
    assert _WATCHDOG_DECAY_RATIO == 1.0


# --------------------------------------------------------------------------
# The other half: a failover must not throw away the speech that proved it
# --------------------------------------------------------------------------


def test_the_replay_buffer_covers_the_whole_watchdog_window():
    policy = ReconnectPolicy()
    window_ms = policy.silent_stream_voiced_seconds * 1000
    assert policy.audio_buffer_ms >= window_ms, (
        "the buffer must cover the window, or the very speech that proved the "
        "stream was dead is the speech that gets discarded"
    )


def test_the_watchdog_window_is_tunable_without_a_deploy(monkeypatch):
    import importlib

    monkeypatch.setenv("STT_SILENT_VOICED_SECONDS", "4.0")
    monkeypatch.setenv("STT_REPLAY_BUFFER_MS", "9000")
    reloaded = importlib.reload(rstt)
    try:
        policy = reloaded.ReconnectPolicy()
        assert policy.silent_stream_voiced_seconds == 4.0
        assert policy.audio_buffer_ms == 9000
    finally:
        monkeypatch.delenv("STT_SILENT_VOICED_SECONDS", raising=False)
        monkeypatch.delenv("STT_REPLAY_BUFFER_MS", raising=False)
        importlib.reload(rstt)


def test_the_secondary_watchdog_is_told_when_the_agent_is_speaking():
    # Guard: without agent_speaking the fallback's watchdog counts our own
    # echo, and that signal is what we use to judge whether the provider is
    # genuinely deaf. An echo-blind version lies.
    from pathlib import Path

    source = Path(rstt.__file__).read_text(encoding="utf-8")
    secondary = source.split("secondary_watchdog.observe_audio(", 1)[1][:260]
    assert "agent_speaking=_agent_speaking()" in secondary
