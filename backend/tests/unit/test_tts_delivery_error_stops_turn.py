"""Regression test for hangup-vs-inflight-reply, part 12b (2026-09-23).

PRODUCTION EVIDENCE
--------------------
Calls 8b3176ca and 6aaeb4dd: ``send_tts_audio`` raised ``TtsDeliveryError``
("no gateway session for call_id=...") partway through a sentence's audio.
``tts_playback.synthesize_and_send``'s old generic ``except Exception``
caught it, logged it, and returned ``interrupted=False`` — indistinguishable
from a normal completion. ``turn_streamer.py``'s
``if not tts_was_interrupted: session._spoken_sentences.append(sentence)``
then recorded the sentence as spoken even though delivery had failed. On
6aaeb4dd, ``transcript_json`` turn_index 17 was stored as a normal
``event_type: 'assistant_response', is_final: true`` row despite at most
~0.65s of a multi-second sentence having actually played.

SCOPE (renamed from test_tts_delivery_error_transcript.py on review,
2026-09-24): this file drives the REAL ``TtsPlayback.synthesize_and_send``
(not a mock of the unit under test) with a fake media gateway that raises
``TtsDeliveryError`` mid-stream, and asserts on the ``interrupted`` boolean
and ``_spoken_sentences``/silent-turn bookkeeping this method itself owns.
It does NOT prove the persisted transcript is correct — the original file
name and this module's earlier wording implied it did, which was wrong.
``interrupted=True`` alone stops the rest of the turn and keeps the sentence
out of ``_spoken_sentences``, but turn_streamer.py:1147 only substitutes
``_spoken_sentences`` into the text that gets persisted when a real
barge-in (`_barged()`) is ALSO true — a TtsDeliveryError with no barge-in
does not qualify, so 6aaeb4dd's undelivered line is still what
turn_runner.py commits via accumulate_turn. See
test_turn_streamer_tts_delivery_error_transcript_gap.py for that
reproduction (real code path, out-of-fence fix needed) and
tts_playback.py:590-611 for the corrected comment.
"""
from __future__ import annotations

import types

import pytest

from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
from app.infrastructure.telephony.asterisk_adapter import TtsDeliveryError


class _Chunk:
    def __init__(self, data: bytes):
        self.data = data


class _Provider:
    name = "fake-tts"

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def stream_synthesize(self, text, **kwargs):
        chunks = self._chunks

        async def _gen():
            for c in chunks:
                yield _Chunk(c)

        return _gen()


class _GatewayFailsOnSecondChunk:
    """Delivers the first chunk, then the channel is gone — matches
    6aaeb4dd/8b3176ca (some audio already sent before delivery fails)."""

    def __init__(self):
        self.sent: list[bytes] = []

    async def send_audio(self, call_id, raw):
        if self.sent:
            raise TtsDeliveryError(f"no gateway session for call_id={call_id[:12]}")
        self.sent.append(raw)

    async def clear_output_buffer(self, call_id):
        return {"ok": True}

    async def flush_tts_buffer(self, call_id):
        return None


class _Latency:
    def mark_tts_first_chunk(self, *a, **k): pass
    def mark_response_start(self, *a, **k): pass
    def mark_audio_start(self, *a, **k): pass
    def mark_tts_end(self, *a, **k): pass
    def mark_completed(self, *a, **k): pass
    def mark_interrupted(self, *a, **k): pass


class _Pipeline:
    def __init__(self, provider, gateway, stt_provider=None):
        self.tts_provider = provider
        self.media_gateway = gateway
        self.latency_tracker = _Latency()
        self.tts_sample_rate = 16000
        self.stt_provider = stt_provider
        self.silent_turns: list[str] = []

    def _record_silent_turn(self, call_id, reason):
        self.silent_turns.append(reason)


def _session():
    return types.SimpleNamespace(
        call_id="6aaeb4dd-0000-0000-0000-000000000017",
        tts_active=True,
        voice_id="v1",
        turn_id=17,
    )


@pytest.mark.asyncio
async def test_tts_delivery_error_mid_stream_is_not_reported_as_delivered():
    """A mid-sentence TtsDeliveryError must return interrupted=True so the
    caller's `if not tts_was_interrupted: _spoken_sentences.append(...)` gate
    excludes this sentence from `_spoken_sentences` and stops the rest of the
    turn — the same as a real barge-in already does. This does NOT by itself
    keep the sentence out of the PERSISTED transcript (see the module
    docstring's known-gap note; that needs turn_streamer.py, out of fence)."""
    gateway = _GatewayFailsOnSecondChunk()
    provider = _Provider([b"\x01\x02" * 80, b"\x03\x04" * 80])
    pipe = _Pipeline(provider, gateway)
    pb = TtsPlayback(pipe)

    interrupted = await pb.synthesize_and_send(
        _session(), "Do you need an urgent dental assessment?", None, track_latency=False
    )

    assert interrupted is True, (
        "a TtsDeliveryError must be reported the same way a barge-in is "
        "(interrupted=True), or turn_streamer records an undelivered "
        "sentence as spoken (call 6aaeb4dd, turn 17)"
    )
    assert gateway.sent, "the first chunk really did reach the gateway (partial delivery)"


@pytest.mark.asyncio
async def test_tts_delivery_error_before_any_audio_still_records_silent_turn():
    """A TtsDeliveryError with zero audio sent (8b3176ca's second attempt,
    channel already gone) must still show up in the turn_silent_reason
    metric — interrupted=True alone would otherwise make the finally
    block's own bookkeeping skip it silently."""

    class _GatewayDeadFromStart:
        async def send_audio(self, call_id, raw):
            raise TtsDeliveryError(f"no gateway session for call_id={call_id[:12]}")

        async def clear_output_buffer(self, call_id):
            return {"ok": True}

        async def flush_tts_buffer(self, call_id):
            return None

        play_pcmu_clip = None  # not callable -> _try_emergency_voice_clip no-ops

    provider = _Provider([b"\x01\x02" * 80])
    pipe = _Pipeline(provider, _GatewayDeadFromStart())
    pb = TtsPlayback(pipe)

    interrupted = await pb.synthesize_and_send(
        _session(), "Sentence two.", None, track_latency=False
    )

    assert interrupted is True
    assert pipe.silent_turns == ["tts_delivery_error"], (
        f"expected the dead-channel, zero-audio turn to be recorded as a "
        f"silent turn; got {pipe.silent_turns!r}"
    )


@pytest.mark.asyncio
async def test_normal_completion_is_unaffected():
    """Control: a clean multi-chunk synthesis with no delivery error still
    completes normally (no regression from the new except clause)."""
    gateway = _GatewayFailsOnSecondChunk()
    gateway.send_audio = None  # replaced below to never fail
    provider = _Provider([b"\x01\x02" * 80, b"\x03\x04" * 80])

    class _HealthyGateway:
        def __init__(self):
            self.sent = []

        async def send_audio(self, call_id, raw):
            self.sent.append(raw)

        async def clear_output_buffer(self, call_id):
            return {"ok": True}

        async def flush_tts_buffer(self, call_id):
            return None

    healthy = _HealthyGateway()
    pipe = _Pipeline(provider, healthy)
    pb = TtsPlayback(pipe)

    interrupted = await pb.synthesize_and_send(
        _session(), "All good.", None, track_latency=False
    )

    assert interrupted is False
    assert len(healthy.sent) == 2
    assert pipe.silent_turns == []
