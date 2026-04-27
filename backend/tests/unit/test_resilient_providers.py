"""T1.3 — Resilient STT + TTS wrapper tests.

Both wrappers are provider-agnostic; we exercise them against
in-process fakes so CI doesn't need Deepgram / Cartesia credentials.

Coverage focuses on the failure-path invariants documented in each
module's header:

STT
  - Happy path streams primary's transcripts end-to-end.
  - Primary raise at start → failover to secondary + replay buffer.
  - Circuit open on entry → straight to secondary, no primary attempt.
  - No secondary configured → happy path still works; failure → empty.
  - Ring buffer caps replay volume.

TTS
  - Happy path streams primary.
  - Primary handshake fails BEFORE yielding → failover to secondary.
  - Primary drops MID-STREAM → truncated, re-raised (no mid-voice stitching).
  - Circuit open on entry → secondary used, primary untouched.
  - Voice-id mapping applied when the policy defines one.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Callable, Optional

import pytest

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk, TranscriptChunk
from app.domain.services.resilient_stt import (
    ReconnectPolicy,
    ResilientSTTProvider,
)
from app.domain.services.resilient_tts import (
    ResilientTTSProvider,
    TTSFailoverPolicy,
)


# ──────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────

class _FakeSTT(STTProvider):
    def __init__(self, name: str, outputs: list[str], raise_on_enter: Optional[Exception] = None):
        self._name = name
        self._outputs = outputs
        self._raise = raise_on_enter
        self.received_chunks: list[AudioChunk] = []
        self.stream_calls = 0

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
        self.stream_calls += 1
        if self._raise is not None:
            raise self._raise
        async for chunk in audio_stream:
            self.received_chunks.append(chunk)
        for text in self._outputs:
            yield TranscriptChunk(text=text, is_final=True)


class _FakeTTS(TTSProvider):
    def __init__(
        self,
        name: str,
        chunk_count: int = 3,
        raise_on_enter: Optional[Exception] = None,
        raise_after: Optional[int] = None,
    ):
        self._name = name
        self._chunks = chunk_count
        self._raise_enter = raise_on_enter
        self._raise_after = raise_after
        self.calls: list[tuple[str, str]] = []  # (text, voice_id)

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self, config: dict) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    async def get_available_voices(self) -> list[dict]:
        return [{"id": f"{self._name}-voice"}]

    async def stream_synthesize(
        self,
        text: str,
        voice_id: str,
        sample_rate: int = 16000,
        **kwargs,
    ) -> AsyncIterator[AudioChunk]:
        self.calls.append((text, voice_id))
        if self._raise_enter is not None:
            raise self._raise_enter
        for i in range(self._chunks):
            if self._raise_after is not None and i == self._raise_after:
                raise RuntimeError(f"{self._name} mid-stream drop")
            yield AudioChunk(data=bytes([i]) * 10, sample_rate=sample_rate, is_final=(i == self._chunks - 1))


async def _audio_stream(n: int = 3) -> AsyncIterator[AudioChunk]:
    for i in range(n):
        yield AudioChunk(data=bytes([i]) * 320, sample_rate=16000, is_final=False)


async def _drain(it: AsyncIterator):
    return [x async for x in it]


# ──────────────────────────────────────────────────────────────────────────
# STT wrapper
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stt_happy_path_uses_primary_only():
    primary = _FakeSTT("primary", ["hello", "world"])
    secondary = _FakeSTT("secondary", ["should-never-yield"])
    wrapper = ResilientSTTProvider(primary, secondary)

    results = await _drain(wrapper.stream_transcribe(_audio_stream()))

    assert [r.text for r in results] == ["hello", "world"]
    assert primary.stream_calls == 1
    assert secondary.stream_calls == 0
    assert len(primary.received_chunks) == 3


@pytest.mark.asyncio
async def test_stt_primary_failure_fails_over_to_secondary():
    primary = _FakeSTT("primary", [], raise_on_enter=RuntimeError("ws dropped"))
    secondary = _FakeSTT("secondary", ["recovered"])
    wrapper = ResilientSTTProvider(primary, secondary)

    results = await _drain(wrapper.stream_transcribe(_audio_stream()))

    assert [r.text for r in results] == ["recovered"]
    assert primary.stream_calls == 1
    assert secondary.stream_calls == 1


@pytest.mark.asyncio
async def test_stt_no_secondary_yields_nothing_on_failure():
    primary = _FakeSTT("primary", [], raise_on_enter=RuntimeError("ws dropped"))
    wrapper = ResilientSTTProvider(primary, secondary=None)

    results = await _drain(wrapper.stream_transcribe(_audio_stream()))

    assert results == []


@pytest.mark.asyncio
async def test_stt_replay_buffer_caps_total_duration():
    """Audio chunks accumulate during the primary attempt; on failover
    only the TAIL fits in the ring buffer. At 16kHz 16-bit mono, 320
    bytes ≈ 10ms. Cap=500ms → at most ~50 chunks resident."""
    from app.domain.services.resilient_stt import _ReplayBuffer
    buf = _ReplayBuffer(capacity_ms=500)
    # Push 100 chunks (~1000ms) — cap must drop half of them.
    for _ in range(100):
        buf.add(AudioChunk(data=b"\x00" * 320, sample_rate=16000, is_final=False))
    # Sliding window kept at roughly 500ms worth (50 chunks ± rounding).
    assert 45 <= len(buf.chunks) <= 55, f"unexpected retained={len(buf.chunks)}"
    assert buf._total_ms <= 500 + 10  # allow 1 chunk slop


@pytest.mark.asyncio
async def test_stt_circuit_open_skips_primary():
    """When the circuit is already open (enough prior failures), new
    streams go directly to secondary with no primary attempt."""
    primary = _FakeSTT("primary", [])
    secondary = _FakeSTT("secondary", ["direct-to-secondary"])
    wrapper = ResilientSTTProvider(
        primary, secondary,
        policy=ReconnectPolicy(failure_threshold=1),
    )
    # Force-open the breaker without needing real failures.
    wrapper._breaker._state = __import__(
        "app.utils.resilience", fromlist=["CircuitState"],
    ).CircuitState.OPEN
    import time as _t
    wrapper._breaker._last_failure_time = _t.monotonic()

    results = await _drain(wrapper.stream_transcribe(_audio_stream()))
    assert [r.text for r in results] == ["direct-to-secondary"]
    assert primary.stream_calls == 0


# ──────────────────────────────────────────────────────────────────────────
# TTS wrapper
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tts_happy_path_uses_primary_only():
    primary = _FakeTTS("primary", chunk_count=3)
    secondary = _FakeTTS("secondary", chunk_count=3)
    wrapper = ResilientTTSProvider(primary, secondary)

    chunks = await _drain(wrapper.stream_synthesize("hello", "voice-A"))

    assert len(chunks) == 3
    assert primary.calls == [("hello", "voice-A")]
    assert secondary.calls == []


@pytest.mark.asyncio
async def test_tts_startup_failure_fails_over_to_secondary():
    primary = _FakeTTS("primary", raise_on_enter=RuntimeError("auth failed"))
    secondary = _FakeTTS("secondary", chunk_count=2)
    wrapper = ResilientTTSProvider(primary, secondary)

    chunks = await _drain(wrapper.stream_synthesize("hi", "voice-A"))

    assert len(chunks) == 2
    assert primary.calls == [("hi", "voice-A")]
    assert secondary.calls == [("hi", "voice-A")]


@pytest.mark.asyncio
async def test_tts_mid_stream_drop_is_reraised_not_stitched():
    """After the caller has already heard PART of the utterance on the
    primary voice, swapping to a different voice mid-sentence is
    worse than truncating. The wrapper re-raises instead."""
    primary = _FakeTTS("primary", chunk_count=5, raise_after=2)
    secondary = _FakeTTS("secondary", chunk_count=5)
    wrapper = ResilientTTSProvider(primary, secondary)

    collected = []
    with pytest.raises(RuntimeError, match="mid-stream drop"):
        async for chunk in wrapper.stream_synthesize("hello", "voice-A"):
            collected.append(chunk)

    # We got the first two chunks from primary before the drop.
    assert len(collected) == 2
    # Secondary was NOT called — no voice-mixing.
    assert secondary.calls == []


@pytest.mark.asyncio
async def test_tts_voice_id_mapping_applied_on_failover():
    primary = _FakeTTS("primary", raise_on_enter=RuntimeError("down"))
    secondary = _FakeTTS("secondary", chunk_count=1)
    wrapper = ResilientTTSProvider(
        primary, secondary,
        policy=TTSFailoverPolicy(voice_id_map={"cartesia-tessa": "eleven-bella"}),
    )

    await _drain(wrapper.stream_synthesize("hi", "cartesia-tessa"))

    assert secondary.calls == [("hi", "eleven-bella")]


@pytest.mark.asyncio
async def test_tts_circuit_open_skips_primary():
    primary = _FakeTTS("primary", chunk_count=3)
    secondary = _FakeTTS("secondary", chunk_count=2)
    wrapper = ResilientTTSProvider(primary, secondary)
    wrapper._breaker._state = __import__(
        "app.utils.resilience", fromlist=["CircuitState"],
    ).CircuitState.OPEN
    import time as _t
    wrapper._breaker._last_failure_time = _t.monotonic()

    chunks = await _drain(wrapper.stream_synthesize("hi", "v"))

    assert len(chunks) == 2
    assert primary.calls == []
    assert secondary.calls == [("hi", "v")]


@pytest.mark.asyncio
async def test_tts_no_secondary_reraises_startup_failure():
    primary = _FakeTTS("primary", raise_on_enter=RuntimeError("down"))
    wrapper = ResilientTTSProvider(primary, secondary=None)

    with pytest.raises(RuntimeError, match="down"):
        async for _ in wrapper.stream_synthesize("hi", "v"):
            pass


@pytest.mark.asyncio
async def test_tts_available_voices_prefers_primary_then_falls_back():
    class _BrokenTTS(_FakeTTS):
        async def get_available_voices(self):
            raise RuntimeError("down")
    primary = _BrokenTTS("primary")
    secondary = _FakeTTS("secondary")
    wrapper = ResilientTTSProvider(primary, secondary)
    voices = await wrapper.get_available_voices()
    assert voices == [{"id": "secondary-voice"}]


@pytest.mark.asyncio
async def test_cleanup_runs_on_both_providers():
    called = {"p": 0, "s": 0}

    class _T(_FakeTTS):
        def __init__(self, name, key):
            super().__init__(name)
            self._key = key
        async def cleanup(self):
            called[self._key] += 1

    p = _T("primary", "p")
    s = _T("secondary", "s")
    wrapper = ResilientTTSProvider(p, s)
    await wrapper.cleanup()
    assert called == {"p": 1, "s": 1}
