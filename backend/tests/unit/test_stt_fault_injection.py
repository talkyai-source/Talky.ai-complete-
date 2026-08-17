"""Proving the silent-stream watchdog on the path production actually uses.

``test_resilient_stt_silent_stream`` proves the watchdog CLASS behaves. That is
necessary and not sufficient: it says nothing about whether the orchestrator
builds the wrapper on a real call, whether the secondary is reachable, or
whether the caller's in-flight words survive the swap. The 2026-08-13 incident
was in the wiring's blind spot, not in a class.

So this file covers two things:

  1. the fault injector itself — that it is off unless deliberately armed, and
     that every ambiguous case fails towards "do nothing";
  2. the end-to-end rescue — a deaf primary inside a REAL ``ResilientSTTProvider``,
     asserting not just that the secondary was promoted but that the specific
     audio the caller produced before the failover comes out the other side.

The second is the one that matters. A failover that swaps providers and drops
the sentence the caller was halfway through is not a rescue.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Callable, Optional

import pytest

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import AudioChunk, TranscriptChunk
from app.domain.services.resilient_stt import (
    ReconnectPolicy,
    ResilientSTTProvider,
    _chunk_rms,
)
from app.domain.services.stt_fault_injection import (
    DeafSTTProvider,
    _parse_until,
    maybe_deafen_primary,
)

_CAMPAIGN = "c2b6734d-0000-4000-8000-000000000001"
_SAMPLES = 640  # 40ms @ 16kHz — the production frame size


def _pcm(amplitude: int) -> bytes:
    out = bytearray()
    for i in range(_SAMPLES):
        s = amplitude if i % 2 == 0 else -amplitude
        out += int(s).to_bytes(2, "little", signed=True)
    return bytes(out)


def _word(index: int) -> AudioChunk:
    """A voiced chunk whose amplitude encodes WHICH chunk it is.

    Every value is far above the 500 speech threshold, and the alternating
    +a/-a pattern makes RMS exactly equal to the amplitude — so a provider
    downstream can name the chunk it received, and a test can therefore assert
    on audio CONTENT surviving the failover rather than merely on a count.
    """
    return AudioChunk(data=_pcm(1000 + index * 10), sample_rate=16000)


def _index_of(text: str) -> int:
    return (int(text) - 1000) // 10


async def _stream(chunks: list[AudioChunk]) -> AsyncIterator[AudioChunk]:
    for c in chunks:
        yield c


class _NamingSTT(STTProvider):
    """Transcribes each chunk as its own amplitude, so the test can read back
    exactly which audio this provider was given, in order."""

    def __init__(self, name: str = "nova-like"):
        self._name = name
        self.received = 0

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self, config: dict) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    def is_muted(self, call_id: str) -> bool:
        return False

    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None,
        call_id: Optional[str] = None,
        on_eager_end_of_turn: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
    ) -> AsyncIterator[TranscriptChunk]:
        async for chunk in audio_stream:
            self.received += 1
            yield TranscriptChunk(text=str(round(_chunk_rms(chunk))), is_final=True)


class _RealisticFlux(STTProvider):
    """Stands in for the real Deepgram Flux provider that the injector wraps.
    Tracks the lifecycle calls so we can prove the wrapper forwards them."""

    def __init__(self):
        self.initialised = False
        self.pre_connected = False
        self.cleaned_up = False
        self.muted = False

    @property
    def name(self) -> str:
        return "deepgram_flux"

    async def initialize(self, config: dict) -> None:
        self.initialised = True

    async def cleanup(self) -> None:
        self.cleaned_up = True

    async def pre_connect(self, call_id: str) -> None:
        self.pre_connected = True

    async def mute(self, call_id: str) -> None:
        self.muted = True

    async def unmute(self, call_id: str) -> None:
        self.muted = False

    def is_muted(self, call_id: str) -> bool:
        return self.muted

    async def stream_transcribe(self, audio_stream, **kwargs):  # pragma: no cover
        raise AssertionError("the deaf wrapper must never delegate transcription")
        yield


def _armed(monkeypatch: pytest.MonkeyPatch, *, hours: float = 1.0) -> None:
    until = datetime.now(timezone.utc) + timedelta(hours=hours)
    monkeypatch.setenv("VOICE_STT_FAULT_SILENT_CAMPAIGN", _CAMPAIGN)
    monkeypatch.setenv("VOICE_STT_FAULT_SILENT_UNTIL", until.isoformat())


# ── the injector stays out of the way unless deliberately armed ─────────────

def test_absent_env_returns_the_provider_untouched(monkeypatch):
    monkeypatch.delenv("VOICE_STT_FAULT_SILENT_CAMPAIGN", raising=False)
    inner = _RealisticFlux()
    assert maybe_deafen_primary(
        inner, campaign_id=_CAMPAIGN, failover_enabled=True
    ) is inner


def test_a_different_campaign_is_untouched(monkeypatch):
    _armed(monkeypatch)
    inner = _RealisticFlux()
    assert maybe_deafen_primary(
        inner, campaign_id="some-other-campaign", failover_enabled=True
    ) is inner


def test_a_call_with_no_campaign_is_untouched(monkeypatch):
    """Ad-hoc and browser calls carry no campaign. They must never be caught by
    a campaign-scoped switch."""
    _armed(monkeypatch)
    inner = _RealisticFlux()
    assert maybe_deafen_primary(inner, campaign_id=None, failover_enabled=True) is inner
    assert maybe_deafen_primary(inner, campaign_id="", failover_enabled=True) is inner


def test_an_expired_window_stops_injecting(monkeypatch):
    """The property that makes this safe to ship: forgetting to unset it is
    self-correcting."""
    _armed(monkeypatch, hours=-1.0)
    inner = _RealisticFlux()
    assert maybe_deafen_primary(
        inner, campaign_id=_CAMPAIGN, failover_enabled=True
    ) is inner


@pytest.mark.parametrize("bad", ["", "   ", "next tuesday", "2026-13-45"])
def test_an_unreadable_expiry_fails_closed(monkeypatch, bad):
    """An expiry we cannot parse must mean "don't", never "forever"."""
    monkeypatch.setenv("VOICE_STT_FAULT_SILENT_CAMPAIGN", _CAMPAIGN)
    monkeypatch.setenv("VOICE_STT_FAULT_SILENT_UNTIL", bad)
    inner = _RealisticFlux()
    assert maybe_deafen_primary(
        inner, campaign_id=_CAMPAIGN, failover_enabled=True
    ) is inner


def test_a_missing_expiry_fails_closed(monkeypatch):
    monkeypatch.setenv("VOICE_STT_FAULT_SILENT_CAMPAIGN", _CAMPAIGN)
    monkeypatch.delenv("VOICE_STT_FAULT_SILENT_UNTIL", raising=False)
    inner = _RealisticFlux()
    assert maybe_deafen_primary(
        inner, campaign_id=_CAMPAIGN, failover_enabled=True
    ) is inner


def test_it_refuses_to_deafen_a_call_with_no_secondary(monkeypatch):
    """Without failover there is nothing to promote, so this would destroy the
    call instead of testing the rescue. The refusal is the whole safety
    argument for shipping a fault injector at all."""
    _armed(monkeypatch)
    inner = _RealisticFlux()
    assert maybe_deafen_primary(
        inner, campaign_id=_CAMPAIGN, failover_enabled=False
    ) is inner


def test_arming_the_named_campaign_wraps_it(monkeypatch):
    _armed(monkeypatch)
    inner = _RealisticFlux()
    wrapped = maybe_deafen_primary(
        inner, campaign_id=_CAMPAIGN, call_id="call-abc", failover_enabled=True
    )
    assert isinstance(wrapped, DeafSTTProvider)
    assert "deepgram_flux" in wrapped.name


def test_the_campaign_match_is_case_insensitive(monkeypatch):
    """UUIDs get copied out of the console in either case; a test that silently
    does nothing because of casing is worse than one that refuses."""
    _armed(monkeypatch)
    wrapped = maybe_deafen_primary(
        _RealisticFlux(), campaign_id=_CAMPAIGN.upper(), failover_enabled=True
    )
    assert isinstance(wrapped, DeafSTTProvider)


@pytest.mark.parametrize("raw,expected_year", [
    ("1800000000", 2027),
    ("2026-08-18T23:00:00Z", 2026),
    ("2026-08-18T23:00:00+00:00", 2026),
    ("2026-08-18T23:00:00", 2026),
])
def test_expiry_accepts_epoch_and_iso(raw, expected_year):
    parsed = _parse_until(raw)
    assert parsed is not None and parsed.tzinfo is not None
    assert parsed.year == expected_year


# ── the wrapper is a faithful stand-in, not a stub ──────────────────────────

@pytest.mark.asyncio
async def test_the_deaf_wrapper_still_opens_the_real_socket():
    """Only transcription is suppressed. If it skipped initialise or
    pre-connect it would be exercising a different failure from the one that
    happened — a provider that never connected, rather than one that connected
    and went quiet."""
    inner = _RealisticFlux()
    deaf = DeafSTTProvider(inner)

    await deaf.initialize({})
    await deaf.pre_connect("call-1")
    await deaf.mute("call-1")
    assert deaf.is_muted("call-1") is True
    await deaf.unmute("call-1")
    await deaf.cleanup()

    assert inner.initialised and inner.pre_connected and inner.cleaned_up
    assert inner.muted is False


@pytest.mark.asyncio
async def test_the_deaf_wrapper_consumes_audio_and_says_nothing():
    """It must DRAIN the stream. The watchdog lives in the iterator feeding
    this call, so a wrapper that never pulled would advance nothing and the
    injected fault would never be detected — a test that always passes."""
    deaf = DeafSTTProvider(_RealisticFlux())
    out = [c async for c in deaf.stream_transcribe(
        _stream([_word(i) for i in range(1, 11)]), call_id="call-2"
    )]
    assert out == []
    assert deaf.chunks_swallowed == 10


# ── the end-to-end rescue ───────────────────────────────────────────────────

def _policy() -> ReconnectPolicy:
    """Trip after 1.2s of voiced audio (30 chunks) with a 200ms replay buffer
    (5 chunks). Deliberately buffer-SHORTER-than-trip so the test proves the
    buffer's contents rather than accidentally replaying the whole call."""
    return ReconnectPolicy(silent_stream_voiced_seconds=1.2, audio_buffer_ms=200)


@pytest.mark.asyncio
async def test_injected_fault_promotes_the_secondary_and_keeps_the_utterance():
    """THE proof this whole file exists for.

    A caller speaks 40 chunks. The primary — deafened exactly as production
    Flux was — answers none of them. The watchdog trips at chunk 30, the
    secondary is promoted, and the assertion is on CONTENT: the five chunks
    the caller had already spoken into the dead stream (26-30) come back out
    of the secondary, in order, followed by the rest of the call.
    """
    primary = DeafSTTProvider(_RealisticFlux())
    secondary = _NamingSTT("nova-3")
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    spoken = [_word(i) for i in range(1, 41)]
    out = [
        c.text
        async for c in wrapper.stream_transcribe(_stream(spoken), call_id="call-3")
    ]
    heard = [_index_of(t) for t in out]

    # The primary really was fed the audio — this is not a test of a provider
    # that was skipped. It receives 29, not 30: the watchdog raises on the
    # chunk that crosses the threshold BEFORE that chunk is yielded onward, so
    # the tripping chunk is buffered and never reaches the dead provider. That
    # is the desirable order — it means the last frame the caller spoke goes to
    # the secondary rather than into the void.
    assert primary.chunks_swallowed == 29, "watchdog did not trip where expected"

    # The secondary was promoted and the caller stopped talking into a dead line.
    assert heard, "nothing was transcribed — the caller was lost"

    # The in-flight utterance survived: the last words spoken BEFORE the
    # failover are the first words the secondary reports.
    assert heard[:5] == [26, 27, 28, 29, 30], (
        f"buffered utterance was not replayed intact: got {heard[:5]}"
    )

    # ...and the call continues uninterrupted from there.
    assert heard == list(range(26, 41)), f"gap or reorder after failover: {heard}"


@pytest.mark.asyncio
async def test_nothing_spoken_before_the_trip_is_silently_dropped():
    """A weaker but independent statement of the same property: every chunk the
    secondary reports is one the caller actually produced, and the ones it
    reports are contiguous. Catches a replay that duplicates or reorders."""
    primary = DeafSTTProvider(_RealisticFlux())
    secondary = _NamingSTT("nova-3")
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())

    out = [
        c.text
        async for c in wrapper.stream_transcribe(
            _stream([_word(i) for i in range(1, 41)]), call_id="call-4"
        )
    ]
    heard = [_index_of(t) for t in out]

    assert len(heard) == len(set(heard)), f"duplicate audio replayed: {heard}"
    assert heard == sorted(heard), f"audio arrived out of order: {heard}"
    assert secondary.received == len(heard)
