"""Flux must NOT leak the turn-boundary probability into TranscriptChunk.confidence.

Bug (verified 2026-07): Deepgram Flux's ``end_of_turn_confidence`` is a
"speaker finished" probability, NOT a word/recognition confidence. It was being
stuffed into the generic ``TranscriptChunk.confidence`` field and then misused
by the turn-0 rejection gate (``_should_reject_turn_0``) as if it were a
recognition score — so a perfectly-recognised first utterance with a modest
end-of-turn probability could be wrongly dropped.

Fix: every Flux emit site now sets ``confidence=None``. The turn-0 gate guards
``if confidence is not None and confidence < min_confidence`` — so None makes the
confidence branch naturally inactive for Flux, while providers that DO supply a
real recognition confidence (Nova/failover, via the legacy ``Results`` path)
stay protected.

LOCAL ONLY — not committed.
"""
import asyncio

import pytest

from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.domain.models.conversation import TranscriptChunk, BargeInSignal
from app.domain.services.voice_pipeline_service import _should_reject_turn_0


# A turn-boundary probability we deliberately plant on every Flux event. If the
# fix regresses, this value would resurface in TranscriptChunk.confidence.
_EOT_PROB = 0.87


class _FakeFluxWS:
    """Minimal stand-in for the Deepgram Flux websocket.

    Yields a fixed list of Flux JSON frames, then ends the async iteration.
    ``send``/``close`` are no-ops. When the frames are exhausted it sets
    ``all_sent`` so the (empty) audio stream can finish AFTER every transcript
    has been processed — keeping stop_event from being set prematurely, which
    would otherwise short-circuit receive_transcripts before it emits.
    """

    def __init__(self, frames, all_sent: asyncio.Event):
        self._frames = list(frames)
        self._all_sent = all_sent

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._frames:
            return self._frames.pop(0)
        self._all_sent.set()
        raise StopAsyncIteration

    async def send(self, *_a, **_k):
        return None

    async def close(self):
        return None


async def _drive_flux(frames):
    """Run stream_transcribe against a pre-connected fake WS and collect chunks."""
    import json

    provider = DeepgramFluxSTTProvider()
    provider._api_key = "test-key"  # skip the initialize() guard
    call_id = "call-conf-test"

    all_sent = asyncio.Event()
    provider._pre_connections[call_id] = _FakeFluxWS(
        [json.dumps(f) for f in frames], all_sent
    )

    async def audio_stream():
        # Keep send_audio alive (parked) until every frame is delivered, so it
        # does not set stop_event before receive_transcripts drains the frames.
        await all_sent.wait()
        return
        yield  # pragma: no cover  (marks this an async generator)

    collected = []
    stream = provider.stream_transcribe(audio_stream(), call_id=call_id)
    # Hard timeout so a wiring regression fails loudly instead of hanging CI.
    async def _consume():
        async for chunk in stream:
            collected.append(chunk)

    await asyncio.wait_for(_consume(), timeout=5.0)
    return collected


@pytest.mark.asyncio
async def test_flux_emit_sites_never_leak_turn_boundary_confidence():
    """Every Flux-emitted TranscriptChunk carries confidence=None — the eager
    partial, the EndOfTurn final text, the empty EndOfTurn control marker, and
    the Update partial. The planted end_of_turn_confidence must NOT appear."""
    frames = [
        {"type": "TurnInfo", "event": "Update",
         "transcript": "hello there", "end_of_turn_confidence": _EOT_PROB},
        {"type": "TurnInfo", "event": "EagerEndOfTurn",
         "transcript": "hello there friend", "end_of_turn_confidence": _EOT_PROB},
        {"type": "TurnInfo", "event": "EndOfTurn",
         "transcript": "hello there friend", "end_of_turn_confidence": _EOT_PROB},
    ]

    chunks = await _drive_flux(frames)

    transcript_chunks = [c for c in chunks if isinstance(c, TranscriptChunk)]
    # We expect: 1 Update partial + 1 eager partial + 1 EndOfTurn final text
    # + 1 empty EndOfTurn control marker = 4 emitted chunks.
    assert len(transcript_chunks) >= 4, [
        (c.text, c.is_final, c.confidence) for c in transcript_chunks
    ]

    for c in transcript_chunks:
        assert c.confidence is None, (
            f"Flux leaked a turn-boundary value into confidence: "
            f"text={c.text!r} is_final={c.is_final} confidence={c.confidence!r}"
        )
    # And specifically the planted probability never resurfaced anywhere.
    assert _EOT_PROB not in {c.confidence for c in transcript_chunks}

    # The empty end-of-turn control marker (text="", is_final=True) is present
    # and also carries confidence=None (was hardcoded 1.0 before the fix).
    end_markers = [c for c in transcript_chunks if c.is_final and c.text == ""]
    assert end_markers, "expected an empty EndOfTurn control marker"
    assert all(c.confidence is None for c in end_markers)


def test_turn_0_gate_still_rejects_a_real_recognition_confidence():
    """The fix only makes Flux emit None — it does NOT disable the gate. A chunk
    that DOES carry a real recognition confidence (Nova/failover via the legacy
    Results path) below the floor is still rejected, while a Flux None passes."""
    # Real recognition confidence below the floor -> still gated.
    assert _should_reject_turn_0("yellow", confidence=0.3) == "low_confidence"
    # Flux now emits None here -> the confidence branch is inactive, greeting passes.
    assert _should_reject_turn_0("yellow", confidence=None) is None
