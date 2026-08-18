"""The watchdog must not count the agent's own voice as unanswered caller speech.

THE INCIDENT (2026-08-18). Six of fourteen answered calls abandoned Deepgram
Flux within seconds of the greeting:

    18:53:25  recording_disclosure_speaking reason=tenant_default_two_party
    18:53:30  recording_disclosure_spoken
    18:53:30  outbound_greeting_presynth chunks=29
    18:53:31  outbound_greeting_presynth_done elapsed_ms=1011
    18:53:33  resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
    18:53:33  resilient_stt_failed_over_to=deepgram-nova:nova-3

In all six trip windows the agent was mid-utterance. The "voiced caller audio"
was our own TTS returning on a 2-wire line at RMS 700-4200. Every trip was a
false positive, at a rate of 43% of calls.

WHY THE EXISTING GUARD DID NOTHING. The watchdog discounted audio only while
the provider reported itself ``muted``. On telephony ``mute_during_tts`` is
False by design (telephony_settings.py:214) because muting STT would destroy
barge-in, and there is not one ``mute()`` call in the telephony path. So the
flag was constant-False on every phone call and the echo protection — which the
docstring described in detail — had never once applied.

THE SHAPE OF THE FIX. Not ducking the microphone: that is the industry's other
option and it trades away barge-in, which is exactly what telephony leaves STT
live for. Instead the watchdog abstains from COUNTING while the agent speaks,
because during that window line energy is either echo or a genuine barge-in and
without AEC we cannot tell which.

These tests pin both directions: it must stop counting our own audio, and it
must still catch a genuinely dead stream during a listening window.
"""
from __future__ import annotations

from typing import AsyncIterator, Callable, Optional

import pytest

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import AudioChunk, TranscriptChunk
from app.domain.models.session import CallSession
from app.domain.services.resilient_stt import (
    ReconnectPolicy,
    ResilientSTTProvider,
    _ECHO_TAIL_S,
    _SilentStreamWatchdog,
)

_SAMPLES = 640  # 40ms @ 16kHz — the production frame


def _pcm(amplitude: int) -> bytes:
    out = bytearray()
    for i in range(_SAMPLES):
        s = amplitude if i % 2 == 0 else -amplitude
        out += int(s).to_bytes(2, "little", signed=True)
    return bytes(out)


def _voiced() -> AudioChunk:
    """RMS 3000 — comfortably above the 500 threshold, and in the same band as
    the echo actually measured on the 2026-08-18 calls (700-4200)."""
    return AudioChunk(data=_pcm(3000), sample_rate=16000)


def _quiet() -> AudioChunk:
    return AudioChunk(data=_pcm(10), sample_rate=16000)


def _watchdog(seconds: float = 6.0) -> _SilentStreamWatchdog:
    return _SilentStreamWatchdog(voiced_seconds=seconds)


# ── the watchdog itself ─────────────────────────────────────────────────────

def test_agent_audio_is_not_counted():
    """THE FIX. 6 seconds of our own TTS must not look like an unanswered
    caller — this is the exact input that failed over six calls."""
    w = _watchdog()
    for _ in range(200):                       # 8s of audio, all agent
        assert w.observe_audio(_voiced(), agent_speaking=True) is False
    assert w.tripped is False
    assert w.voiced_ms == 0.0
    assert w.suppressed_ms > 6000, "suppression was not recorded"


def test_caller_audio_is_still_counted():
    """The load-bearing negative: the 2026-08-13 dead-stream case must still
    fire, or this fix trades one silent failure for another."""
    w = _watchdog()
    tripped = False
    for _ in range(200):
        if w.observe_audio(_voiced(), agent_speaking=False):
            tripped = True
            break
    assert tripped is True
    assert w.voiced_ms >= 6000
    assert w.suppressed_ms == 0.0


def test_a_quiet_caller_still_never_trips():
    w = _watchdog()
    for _ in range(400):
        w.observe_audio(_quiet(), agent_speaking=False)
    assert w.tripped is False
    assert w.suppressed_ms == 0.0, "silence must not be recorded as suppressed"


def test_the_muted_path_still_works():
    """Browser and ask-AI sessions DO mute. That guard must survive — it is not
    redundant, it covers the platforms telephony's probe does not."""
    w = _watchdog()
    for _ in range(200):
        w.observe_audio(_voiced(), muted=True)
    assert w.tripped is False
    assert w.suppressed_ms > 6000


def test_the_echo_tail_keeps_suppressing_briefly_after_the_agent_stops():
    """Echo does not stop the instant TTS does. The pipeline already waits
    250ms before trusting the mic again; the watchdog uses the same window so
    the two cannot drift apart."""
    w = _watchdog()
    w.observe_audio(_voiced(), agent_speaking=True)
    before = w.voiced_ms

    # One frame (40ms) into the tail — still inside the decay window.
    w.observe_audio(_voiced(), agent_speaking=False)
    assert w.voiced_ms == before, "counted audio inside the echo decay tail"


def test_counting_resumes_once_the_tail_expires():
    """The tail must be a short pause, not a permanent off-switch — otherwise a
    talkative agent disables dead-stream detection for the whole call."""
    w = _watchdog()
    w.observe_audio(_voiced(), agent_speaking=True)
    # Drain the tail: 40ms per frame.
    for _ in range(int(_ECHO_TAIL_S * 1000 / 40) + 2):
        w.observe_audio(_quiet(), agent_speaking=False)

    w.observe_audio(_voiced(), agent_speaking=False)
    assert w.voiced_ms > 0, "watchdog never resumed counting after the tail"


def test_a_talkative_agent_cannot_disable_detection_forever():
    """Interleaved agent/caller audio — the realistic conversation shape. The
    caller's own speech must still accumulate to a trip."""
    w = _watchdog(seconds=1.0)
    tripped = False
    for _ in range(200):
        w.observe_audio(_voiced(), agent_speaking=True)      # agent talks
        for _ in range(20):                                   # then caller does
            if w.observe_audio(_voiced(), agent_speaking=False):
                tripped = True
                break
        if tripped:
            break
    assert tripped is True


# ── the probe, against the REAL session object ──────────────────────────────
#
# Both previous versions of this guard were tested against SimpleNamespace and
# both were dead in production. A double more permissive than the real object
# cannot prove the real object works.

def _session(**kw) -> CallSession:
    return CallSession(
        call_id="5529d6f9-39cf-4830-8e63-705b2bd0ad24",
        campaign_id="c2b6734d-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1", provider_call_id="talky-out-1",
        system_prompt="sp", voice_id="v1", **kw
    )


def test_tts_active_is_readable_on_a_real_call_session():
    """The probe reads exactly this. If tts_active were undeclared — as
    last_audio_rms was — the whole guard would be inert again."""
    s = _session()
    assert s.tts_active is False
    s.tts_active = True
    assert s.tts_active is True


def test_the_probe_shape_the_pipeline_installs():
    """Mirrors audio_ingest's lambda verbatim, so a rename there fails here."""
    s = _session()
    probe = lambda: bool(getattr(s, "tts_active", False))

    assert probe() is False
    s.tts_active = True
    assert probe() is True


class _Silent(STTProvider):
    """Connects, accepts every frame, never answers — production's dead Flux."""

    def __init__(self, name: str = "deepgram-flux"):
        self._name, self.received = name, 0

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self, config: dict) -> None: ...
    async def cleanup(self) -> None: ...
    def is_muted(self, call_id: str) -> bool: return False

    async def stream_transcribe(self, audio_stream, **kw) -> AsyncIterator[TranscriptChunk]:
        async for _ in audio_stream:
            self.received += 1
        return
        yield  # pragma: no cover


class _Echo(STTProvider):
    def __init__(self, name: str = "deepgram-nova"):
        self._name, self.received = name, 0

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self, config: dict) -> None: ...
    async def cleanup(self) -> None: ...
    def is_muted(self, call_id: str) -> bool: return False

    async def stream_transcribe(self, audio_stream, **kw) -> AsyncIterator[TranscriptChunk]:
        async for _ in audio_stream:
            self.received += 1
            yield TranscriptChunk(text="rescued", is_final=True)


async def _stream(n: int) -> AsyncIterator[AudioChunk]:
    for _ in range(n):
        yield _voiced()


def _policy() -> ReconnectPolicy:
    return ReconnectPolicy(silent_stream_voiced_seconds=1.0, audio_buffer_ms=200)


@pytest.mark.asyncio
async def test_end_to_end_the_agent_talking_does_not_cause_a_failover():
    """The 2026-08-18 call, replayed through the real wrapper: the agent speaks
    throughout, the primary answers nothing, and the secondary must NOT be
    promoted — because nothing has been shown to be wrong with the primary."""
    session = _session()
    session.tts_active = True                       # agent mid-utterance
    primary, secondary = _Silent(), _Echo()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())
    wrapper.set_agent_speaking_probe(lambda: bool(session.tts_active))

    out = [c.text async for c in wrapper.stream_transcribe(_stream(200), call_id="c1")]

    assert out == [], "transcripts appeared from a provider that answers nothing"
    assert secondary.received == 0, "failed over on the agent's own echo"
    assert primary.received == 200, "the primary should have been fed throughout"


@pytest.mark.asyncio
async def test_end_to_end_a_silent_caller_still_triggers_failover():
    """With the agent quiet, the original rescue must be unchanged."""
    session = _session()
    session.tts_active = False
    primary, secondary = _Silent(), _Echo()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())
    wrapper.set_agent_speaking_probe(lambda: bool(session.tts_active))

    out = [c.text async for c in wrapper.stream_transcribe(_stream(200), call_id="c2")]

    assert out and set(out) == {"rescued"}, "the caller was lost"
    assert secondary.received > 0


@pytest.mark.asyncio
async def test_without_a_probe_behaviour_is_exactly_as_before():
    """Backwards compatibility. Browser/ask-AI callers install no probe and must
    keep the pre-2026-08-18 behaviour rather than silently losing protection."""
    primary, secondary = _Silent(), _Echo()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    out = [c.text async for c in wrapper.stream_transcribe(_stream(200), call_id="c3")]

    assert out and set(out) == {"rescued"}


@pytest.mark.asyncio
async def test_a_broken_probe_fails_toward_the_old_behaviour_and_is_counted():
    """A probe that raises must not be able to silently disable dead-stream
    detection. It degrades to the previous behaviour AND leaves a count, so
    'the probe is broken' cannot masquerade as 'the agent never spoke'."""
    def _boom() -> bool:
        raise RuntimeError("probe exploded")

    primary, secondary = _Silent(), _Echo()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())
    wrapper.set_agent_speaking_probe(_boom)

    out = [c.text async for c in wrapper.stream_transcribe(_stream(200), call_id="c4")]

    assert out and set(out) == {"rescued"}, "detection was lost to a broken probe"
    assert wrapper._probe_errors > 0, "the failure was not counted"


@pytest.mark.asyncio
async def test_the_wiring_check_is_logged_on_every_call(caplog):
    """F1 from the pre-mortem: an uninstalled probe must be visible in one grep,
    not inferred from an absence. This is the check that would have caught the
    previous two dead guards on the first call rather than after four days."""
    primary, secondary = _Silent(), _Echo()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    with caplog.at_level("INFO"):
        [c async for c in wrapper.stream_transcribe(_stream(4), call_id="c5")]
    assert "resilient_stt_echo_guard probe=ABSENT" in caplog.text

    caplog.clear()
    wrapper.set_agent_speaking_probe(lambda: False)
    with caplog.at_level("INFO"):
        [c async for c in wrapper.stream_transcribe(_stream(4), call_id="c6")]
    assert "resilient_stt_echo_guard probe=installed" in caplog.text


@pytest.mark.asyncio
async def test_a_healthy_call_still_states_its_verdict(caplog):
    """F3: the audit must be written on calls that were fine, so 'suppressed a
    lot, counted nothing' is visible before it becomes a missed outage."""
    session = _session()
    session.tts_active = True
    primary, secondary = _Silent(), _Echo()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())
    wrapper.set_agent_speaking_probe(lambda: bool(session.tts_active))

    with caplog.at_level("INFO"):
        [c async for c in wrapper.stream_transcribe(_stream(50), call_id="c7")]

    assert "resilient_stt_audit" in caplog.text
    assert "outcome=healthy" in caplog.text
    assert "suppressed_ms=" in caplog.text
