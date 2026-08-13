"""The STT stream that is dead but never errors.

2026-08-13, production: on 2 of 36 answered calls Deepgram Flux was
pre-connected, was handed 400+ audio chunks, and returned ZERO transcript
events — not one StartOfTurn — for the whole call. The caller was talking at
RMS 3504 (peak 28988) the entire time. Nothing failed over, because every
failover trigger in ``ResilientSTTProvider`` was an *exception* and a socket
that answers nothing raises nothing.

The gap is epistemic, not mechanical: from the wrapper's point of view a dead
provider and a silent room produce the identical observation — no transcripts.
Only acoustics separate them, so the wrapper now counts VOICED audio going in
against transcript events coming back.

These tests pin the four properties that make that safe to run on every call:
it fires when speech goes unanswered, it never fires on a quiet caller, it
never fires on our own TTS echo while STT is muted, and it survives a provider
that swallows the exception instead of propagating it.
"""
from typing import AsyncIterator, Callable, Optional

import pytest

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import AudioChunk, TranscriptChunk
from app.domain.services.resilient_stt import (
    ReconnectPolicy,
    ResilientSTTProvider,
    _chunk_rms,
    _SilentStreamWatchdog,
)

# 640 samples @16kHz = 40ms per chunk, matching the production frame size.
_SAMPLES = 640


def _pcm(amplitude: int) -> bytes:
    """16-bit mono PCM whose RMS is exactly ``amplitude``."""
    out = bytearray()
    for i in range(_SAMPLES):
        s = amplitude if i % 2 == 0 else -amplitude
        out += int(s).to_bytes(2, "little", signed=True)
    return bytes(out)


def _voiced() -> AudioChunk:
    """Well above the 500 speech threshold — a person talking."""
    return AudioChunk(data=_pcm(3000), sample_rate=16000)


def _quiet() -> AudioChunk:
    """Room tone. The 2026-08-13 silent calls logged rms=7..14 between words."""
    return AudioChunk(data=_pcm(10), sample_rate=16000)


async def _stream(chunks: list[AudioChunk]) -> AsyncIterator[AudioChunk]:
    for c in chunks:
        yield c


class _SilentSTT(STTProvider):
    """Connects, accepts every frame, and never says anything. This is the
    production failure verbatim."""

    def __init__(self, name: str = "silent"):
        self._name = name
        self.received = 0
        self.muted = False

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self, config: dict) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    def is_muted(self, call_id: str) -> bool:
        return self.muted

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
    ) -> AsyncIterator[TranscriptChunk]:
        async for _ in audio_stream:
            self.received += 1
        return
        yield  # pragma: no cover — makes this an async generator


class _SwallowingSilentSTT(_SilentSTT):
    """Same, but eats any exception raised by the audio iterator and ends its
    stream cleanly. Without the post-loop ``watchdog.tripped`` re-check this
    provider would look like a normal end-of-call and fail over to nothing."""

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
    ) -> AsyncIterator[TranscriptChunk]:
        try:
            async for _ in audio_stream:
                self.received += 1
        except Exception:
            pass
        return
        yield  # pragma: no cover


class _EchoSTT(STTProvider):
    """A healthy STREAMING provider: answers as it goes, like the real ones.

    Both Deepgram engines emit within a few hundred ms of the first word —
    Flux sends StartOfTurn on the first word, and today's production logs show
    2,939 transcript events across 40 calls (~73 per call). Emitting every
    ``every`` chunks models that; it matters because a provider that answers is
    what continuously re-arms the watchdog.
    """

    def __init__(self, name: str = "secondary", every: int = 5):
        self._name = name
        self._every = every
        self.received = 0

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self, config: dict) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
    ) -> AsyncIterator[TranscriptChunk]:
        async for _ in audio_stream:
            self.received += 1
            if self.received % self._every == 0:
                yield TranscriptChunk(text="rescued", is_final=True)


class _BatchSTT(_EchoSTT):
    """Swallows the whole utterance and answers only at the end.

    No provider we run behaves this way, and the watchdog DOES trip on it —
    see ``test_a_batch_provider_trips_the_watchdog_by_design``.
    """

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
    ) -> AsyncIterator[TranscriptChunk]:
        async for _ in audio_stream:
            self.received += 1
        yield TranscriptChunk(text="batched", is_final=True)


def _policy(voiced_seconds: float = 0.4) -> ReconnectPolicy:
    """0.4s of voiced audio = 10 chunks, so the tests stay fast. Production
    uses 6.0s; the mechanism under test is identical."""
    return ReconnectPolicy(silent_stream_voiced_seconds=voiced_seconds)


# ── the pure watchdog ────────────────────────────────────────────────────────

def test_voiced_audio_accumulates_until_it_trips():
    wd = _SilentStreamWatchdog(voiced_seconds=0.4)  # 10 x 40ms chunks
    for _ in range(9):
        assert wd.observe_audio(_voiced()) is False
    assert wd.observe_audio(_voiced()) is True
    assert wd.tripped is True


def test_a_quiet_caller_never_trips_it():
    """The whole point: silence must stay indistinguishable from silence. A
    wall-clock timer here would fail over on every thoughtful pause."""
    wd = _SilentStreamWatchdog(voiced_seconds=0.4)
    for _ in range(500):  # 20 seconds of room tone
        assert wd.observe_audio(_quiet()) is False
    assert wd.voiced_ms == 0.0


def test_muted_audio_is_not_counted():
    """While the agent speaks, STT is muted and drops frames — but on a 2-wire
    line those frames still carry our own TTS at full volume. Counting them
    would trip the watchdog on every talkative agent."""
    wd = _SilentStreamWatchdog(voiced_seconds=0.4)
    for _ in range(500):
        assert wd.observe_audio(_voiced(), muted=True) is False
    assert wd.voiced_ms == 0.0


def test_a_transcript_re_arms_the_counter():
    """Detects a stream that dies at minute nine, not just one that was never
    alive — turn 0 is only the case we happened to observe."""
    wd = _SilentStreamWatchdog(voiced_seconds=0.4)
    for _ in range(9):
        wd.observe_audio(_voiced())
    wd.observe_transcript()
    assert wd.voiced_ms == 0.0
    for _ in range(9):
        assert wd.observe_audio(_voiced()) is False


def test_zero_seconds_disables_the_watchdog():
    wd = _SilentStreamWatchdog(voiced_seconds=0.0)
    for _ in range(500):
        assert wd.observe_audio(_voiced()) is False


def test_unreadable_chunks_fail_safe_to_zero_energy():
    """A malformed chunk must never be able to cause a failover on its own."""
    assert _chunk_rms(AudioChunk(data=b"", sample_rate=16000)) == 0.0
    assert _chunk_rms(AudioChunk(data=b"\x01", sample_rate=16000)) == 0.0


def test_rms_estimate_tracks_true_amplitude():
    assert _chunk_rms(AudioChunk(data=_pcm(3000), sample_rate=16000)) == pytest.approx(3000, rel=0.01)
    assert _chunk_rms(AudioChunk(data=_pcm(10), sample_rate=16000)) == pytest.approx(10, rel=0.01)


# ── wired into the wrapper ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_silent_primary_fails_over_and_the_caller_is_heard():
    """The 2026-08-13 call, replayed: speech in, nothing back, and now the
    secondary picks it up instead of the caller talking into a dead line."""
    primary, secondary = _SilentSTT(), _EchoSTT()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    out = [
        c.text
        async for c in wrapper.stream_transcribe(
            _stream([_voiced()] * 40), call_id="call-1"
        )
    ]

    assert out and set(out) == {"rescued"}
    assert secondary.received > 0, "secondary never received the replayed audio"


@pytest.mark.asyncio
async def test_failover_replays_the_buffered_utterance():
    """The words spoken while the primary was dying must reach the secondary —
    otherwise we swap providers and still lose the sentence."""
    primary, secondary = _SilentSTT(), _EchoSTT()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    async for _ in wrapper.stream_transcribe(
        _stream([_voiced()] * 40), call_id="call-2"
    ):
        pass

    assert secondary.received > 0


@pytest.mark.asyncio
async def test_provider_that_swallows_the_exception_still_fails_over():
    """Covers the post-loop re-check. A provider that catches everything from
    its audio iterator ends cleanly, which is indistinguishable from a normal
    hangup — the exact silent-return this watchdog exists to prevent."""
    primary, secondary = _SwallowingSilentSTT(), _EchoSTT()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    out = [
        c.text
        async for c in wrapper.stream_transcribe(
            _stream([_voiced()] * 40), call_id="call-3"
        )
    ]

    assert out and set(out) == {"rescued"}


@pytest.mark.asyncio
async def test_quiet_call_does_not_fail_over():
    """A caller who says nothing is not a fault. Failing over here would double
    STT spend on every unanswered call."""
    primary, secondary = _SilentSTT(), _EchoSTT()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    out = [
        c.text
        async for c in wrapper.stream_transcribe(
            _stream([_quiet()] * 400), call_id="call-4"
        )
    ]

    assert out == []
    assert secondary.received == 0, "failed over on a silent caller"


@pytest.mark.asyncio
async def test_muted_primary_does_not_fail_over():
    """Agent mid-sentence: STT muted, our own audio on the line. Must not be
    read as an unanswered caller."""
    primary, secondary = _SilentSTT(), _EchoSTT()
    primary.muted = True
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    out = [
        c.text
        async for c in wrapper.stream_transcribe(
            _stream([_voiced()] * 400), call_id="call-5"
        )
    ]

    assert out == []
    assert secondary.received == 0, "failed over on our own TTS echo"


@pytest.mark.asyncio
async def test_working_primary_is_never_disturbed():
    """A provider that answers keeps re-arming the counter, so a long call can
    never accumulate its way into a spurious failover. 400 chunks is 16s of
    continuous speech — far past the trip threshold — and it must survive."""
    primary, secondary = _EchoSTT("primary"), _EchoSTT("secondary")
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    out = [
        c.text
        async for c in wrapper.stream_transcribe(
            _stream([_voiced()] * 400), call_id="call-6"
        )
    ]

    assert len(out) == 80, "healthy primary should have streamed throughout"
    assert secondary.received == 0, "failed over on a provider that was answering"


@pytest.mark.asyncio
async def test_a_batch_provider_trips_the_watchdog_by_design():
    """A DELIBERATE limitation, pinned so it is a decision and not a surprise.

    The watchdog cannot distinguish "dead" from "streaming provider that has
    not answered yet", so a provider that holds the whole utterance and answers
    only at the end WILL be failed over once its silence passes the threshold.

    We accept that because neither engine we run behaves this way — both emit
    within a few hundred ms — and because the two outcomes are wildly
    asymmetric: the cost of a false trip is one extra socket plus a replayed
    buffer (degraded, bounded), while the cost of not tripping is the entire
    call, which is what 2026-08-13 actually cost us. If a batch-style provider
    is ever added, raise ``silent_stream_voiced_seconds`` for it or set it to 0.
    """
    primary, secondary = _BatchSTT("batch-primary"), _EchoSTT("secondary")
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    out = [
        c.text
        async for c in wrapper.stream_transcribe(
            _stream([_voiced()] * 40), call_id="call-7"
        )
    ]

    assert "batched" not in out
    assert secondary.received > 0
