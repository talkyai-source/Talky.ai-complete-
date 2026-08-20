"""Flux goes silent mid-sentence: Nova must take over the caller's words EXACTLY ONCE.

WHY THIS EXISTS
---------------
The watchdog has been proven to fire — in production, unprompted, on call
dec0bb16 (2026-08-18 20:40:28):

    resilient_stt_stream_silent provider=deepgram-flux voiced_s=6.0
    resilient_stt_failed_over_to=deepgram-nova:nova-3 buffered_chunks=12

But "it failed over" is a weaker claim than the one that matters. A failover
that drops the caller's half-finished sentence produces an agent replying to
nothing; a failover that replays it twice produces an agent answering the same
question two ways. Both are worse than the silent stream, because both look like
the agent is broken rather than deaf.

So this pins the handover itself, chunk by chunk:

  * every chunk the replay buffer holds reaches the secondary,
  * no chunk reaches it twice,
  * order is preserved,
  * and the boundary chunk — the one whose arrival trips the watchdog — is not
    dropped in the gap between the two providers.

THE CONTROLLED FAULT
--------------------
The primary here is deaf in exactly the way Flux was: it consumes every chunk it
is given and returns no event at all. That is the failure mode both existing
safety nets were blind to, because both were transcript-derived — a provider
that returns nothing produces no transcript to be suspicious of.

WHAT THIS TEST DOES NOT CLAIM
-----------------------------
The replay buffer holds ``audio_buffer_ms`` (500ms). Caller audio older than
that was consumed by the deaf primary and is genuinely gone. That is a design
limit, stated and pinned below, not a defect — recovering it would mean buffering
the whole call. What must never happen is losing audio the buffer *did* hold.
"""
from __future__ import annotations

import struct
from typing import AsyncIterator, Callable, Optional

import pytest

from app.domain.interfaces.stt_provider import STTProvider
from app.domain.models.conversation import AudioChunk, TranscriptChunk
from app.domain.services.resilient_stt import (
    ReconnectPolicy,
    ResilientSTTProvider,
)

# 40ms @ 16kHz — the production frame size.
_SAMPLES = 640
_FRAME_MS = 40.0
# The watchdog trips after this much *voiced* audio with no event returned.
_TRIP_S = 6.0
_CHUNKS_TO_TRIP = int(_TRIP_S * 1000 / _FRAME_MS)      # 150
# _ReplayBuffer capacity, in chunks.
_BUFFER_MS = 500
_BUFFERED_CHUNKS = int(_BUFFER_MS / _FRAME_MS)          # 12


def _voiced(index: int) -> AudioChunk:
    """A frame of real speech energy carrying its own index.

    The index lives in sample 0 so every chunk is individually identifiable on
    the far side — that is what makes "lost" and "duplicated" decidable rather
    than a matter of counting.
    """
    samples = [index] + [3000 if i % 2 else -3000 for i in range(_SAMPLES - 1)]
    return AudioChunk(data=struct.pack(f"<{_SAMPLES}h", *samples),
                      sample_rate=16000)


def _index_of(chunk: AudioChunk) -> int:
    return struct.unpack_from("<h", chunk.data, 0)[0]


async def _caller_sentence(n: int) -> AsyncIterator[AudioChunk]:
    """One continuous caller utterance, n frames long."""
    for i in range(n):
        yield _voiced(i)


class _DeafPrimary(STTProvider):
    """Flux's actual failure mode: accepts audio, returns no event, never errors."""

    def __init__(self) -> None:
        self.received: list[int] = []

    async def stream_transcribe(self, audio_stream, language=None, context=None,
                                call_id=None, on_eager_end_of_turn=None,
                                **kwargs) -> AsyncIterator[TranscriptChunk]:
        async for chunk in audio_stream:
            self.received.append(_index_of(chunk))
        return
        yield  # pragma: no cover — makes this an async generator

    async def initialize(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "deepgram-flux"


class _RecordingSecondary(STTProvider):
    """Nova stand-in. Records the exact sequence of chunk indices it is fed."""

    def __init__(self) -> None:
        self.received: list[int] = []

    async def stream_transcribe(self, audio_stream, language=None, context=None,
                                call_id=None, on_eager_end_of_turn=None,
                                **kwargs) -> AsyncIterator[TranscriptChunk]:
        async for chunk in audio_stream:
            self.received.append(_index_of(chunk))
        yield TranscriptChunk(text="rescued", is_final=True)

    async def initialize(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "deepgram-nova:nova-3"


def _policy() -> ReconnectPolicy:
    return ReconnectPolicy(
        max_reconnect_attempts=1,
        audio_buffer_ms=_BUFFER_MS,
        silent_stream_voiced_seconds=_TRIP_S,
    )


async def _run(total_chunks: int):
    primary, secondary = _DeafPrimary(), _RecordingSecondary()
    wrapper = ResilientSTTProvider(primary, secondary, policy=_policy())
    out = [c.text async for c in wrapper.stream_transcribe(
        _caller_sentence(total_chunks), call_id="failover-test")]
    return primary, secondary, out


# ── the handover happens at all ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nova_takes_over_a_silent_flux_stream():
    primary, secondary, out = await _run(_CHUNKS_TO_TRIP + 40)

    assert out == ["rescued"], "the caller's sentence was never transcribed"
    assert secondary.received, "Nova was never fed any audio"
    assert len(primary.received) < _CHUNKS_TO_TRIP + 40, (
        "the deaf primary consumed the whole stream — no failover happened"
    )


@pytest.mark.asyncio
async def test_a_stream_that_never_reaches_the_threshold_stays_on_flux():
    """The load-bearing negative. Failing over on a caller who simply paused
    would swap a working engine for a cold one on every quiet moment."""
    primary, secondary, out = await _run(_CHUNKS_TO_TRIP - 10)

    assert secondary.received == [], "failed over before the threshold"
    assert len(primary.received) == _CHUNKS_TO_TRIP - 10


# ── NOTHING LOST ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_buffered_sentence_reaches_nova_intact():
    """Everything the replay buffer held must arrive, contiguously."""
    total = _CHUNKS_TO_TRIP + 40
    primary, secondary, _ = await _run(total)

    # The tail of the stream must be present, unbroken, right to the end.
    assert secondary.received[-1] == total - 1, "the live tail was truncated"
    expected_run = list(range(secondary.received[0], total))
    assert secondary.received == expected_run, (
        "Nova's audio was not a contiguous run — a chunk went missing at the "
        f"handover: {secondary.received[:20]}"
    )


@pytest.mark.asyncio
async def test_no_gap_between_the_last_flux_chunk_and_the_first_nova_chunk():
    """THE HANDOVER SEAM. The chunk that trips the watchdog is added to the
    replay buffer BEFORE the watchdog is consulted, so it must survive. If that
    order is ever reversed this test fails and a syllable is lost mid-word."""
    total = _CHUNKS_TO_TRIP + 40
    primary, secondary, _ = await _run(total)

    covered = set(primary.received) | set(secondary.received)
    missing = [i for i in range(total) if i not in covered]

    assert not missing, (
        f"{len(missing)} chunk(s) were seen by neither provider: {missing[:10]}"
    )


@pytest.mark.asyncio
async def test_nova_receives_roughly_the_buffer_worth_of_history():
    """The replayed history should be about `audio_buffer_ms`, not zero and not
    the whole call. Pinning the magnitude catches a buffer that silently stopped
    filling — which would look identical to a healthy failover in the logs."""
    total = _CHUNKS_TO_TRIP + 40
    primary, secondary, _ = await _run(total)

    live_chunks = total - _CHUNKS_TO_TRIP
    replayed = len(secondary.received) - live_chunks

    assert 1 <= replayed <= _BUFFERED_CHUNKS + 2, (
        f"replayed {replayed} chunks; expected about {_BUFFERED_CHUNKS} "
        f"({_BUFFER_MS}ms at {_FRAME_MS:.0f}ms/frame)"
    )


# ── NOTHING DUPLICATED ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_chunk_reaches_nova_twice():
    """A double-replayed sentence makes the agent answer the same question
    twice. `_ReplayBuffer.drain()` clears as it hands over, and this is what
    holds it to that."""
    total = _CHUNKS_TO_TRIP + 40
    _, secondary, _ = await _run(total)

    dupes = {i for i in secondary.received
             if secondary.received.count(i) > 1}
    assert not dupes, f"chunks delivered more than once: {sorted(dupes)[:10]}"
    assert len(secondary.received) == len(set(secondary.received))


@pytest.mark.asyncio
async def test_order_is_preserved_across_the_handover():
    """Replayed history must precede live audio. Out-of-order frames turn a
    sentence into word salad, which STT reports as low confidence rather than
    as an error — so nothing downstream would flag it."""
    _, secondary, _ = await _run(_CHUNKS_TO_TRIP + 40)

    assert secondary.received == sorted(secondary.received), (
        "replayed audio arrived after live audio"
    )


@pytest.mark.asyncio
async def test_the_transcript_is_emitted_once_not_once_per_provider():
    """Both providers ran over the same sentence. Only the one that could hear
    it may produce output."""
    _, _, out = await _run(_CHUNKS_TO_TRIP + 40)
    assert out.count("rescued") == 1


# ── the documented limit, pinned so it stays a decision ─────────────────────

@pytest.mark.asyncio
async def test_audio_older_than_the_buffer_is_gone_and_that_is_by_design():
    """Not a defect: recovering it would mean buffering the whole call. Pinned
    so that if someone later enlarges the buffer they do it deliberately, and so
    the limit is never mistaken for a bug during an incident."""
    total = _CHUNKS_TO_TRIP + 40
    primary, secondary, _ = await _run(total)

    earliest_nova_saw = secondary.received[0]
    assert earliest_nova_saw > 0, (
        "the whole call was replayed — the buffer is not bounded"
    )
    lost = earliest_nova_saw
    assert lost >= _CHUNKS_TO_TRIP - _BUFFERED_CHUNKS - 2, (
        "more was replayed than the buffer should hold"
    )
    # Everything before the buffer window was heard only by the deaf primary.
    assert set(range(lost)).issubset(set(primary.received))
