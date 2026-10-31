"""Exercise real adapters across transport failure boundaries (A1-A4)."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from app.domain.models.conversation import AudioChunk, TranscriptChunk
from app.domain.services.resilient_stt import ResilientSTTProvider
from app.domain.services.resilient_tts import ResilientTTSProvider, TTSFailoverPolicy
from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
from app.domain.services.voice_pipeline.audio_ingest import AudioIngest
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider
from app.infrastructure.tts.cartesia import CartesiaTTSProvider
from app.infrastructure.tts.elevenlabs_tts import ElevenLabsPartialAudioError


class _Secondary:
    name = "secondary"

    def __init__(self):
        self.received = []

    async def stream_transcribe(self, audio_stream, **kwargs):
        async for chunk in audio_stream:
            self.received.append(chunk.data)
        yield TranscriptChunk(text="recovered", is_final=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["receive", "send", "fatal", "eof"])
async def test_flux_failure_promotes_secondary_and_preserves_waiting_input(monkeypatch, failure):
    waiting = asyncio.Event()
    release = asyncio.Event()
    source_closed = asyncio.Event()

    async def audio():
        try:
            yield AudioChunk(data=bytes(1280), sample_rate=16000)
            waiting.set()
            await release.wait()
            yield AudioChunk(data=b"\x01\x00" * 640, sample_rate=16000)
        finally:
            source_closed.set()

    class Socket:
        closed = False
        sends = 0
        fatal_sent = False

        async def send(self, payload):
            self.sends += 1
            if failure == "send" and self.sends == 2:
                release.set()
                raise ConnectionClosedError(Close(1011, "synthetic failure"), None)

        async def close(self):
            self.closed = True

        def __aiter__(self):
            return self

        async def __anext__(self):
            if failure == "send":
                await asyncio.Event().wait()
            await waiting.wait()
            # Let recovery start while __anext__ of the call-owned audio
            # iterator is suspended. Only the secondary may release it.
            if failure == "fatal":
                if self.fatal_sent:
                    await asyncio.Event().wait()
                self.fatal_sent = True
                return json.dumps({"type": "Error", "code": "INTERNAL_SERVER_ERROR"})
            if failure == "eof":
                raise StopAsyncIteration
            raise ConnectionClosedError(Close(1011, "synthetic failure"), None)

    socket = Socket()
    opens = []

    async def connect(*args, **kwargs):
        opens.append(True)
        return socket

    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.websockets.connect", connect)
    primary = DeepgramFluxSTTProvider()
    await primary.initialize({"api_key": "offline-test", "eot_timeout_ms": 1000})

    class ReleasingSecondary(_Secondary):
        async def stream_transcribe(self, stream, **kwargs):
            assert not source_closed.is_set(), "cancelling the sender killed caller input"
            release.set()
            async for item in super().stream_transcribe(stream, **kwargs):
                yield item

    secondary = ReleasingSecondary()
    wrapper = ResilientSTTProvider(primary, secondary)
    out = await asyncio.wait_for(
        _collect(wrapper.stream_transcribe(audio(), call_id="offline-audit")), 2
    )
    assert [item.text for item in out] == ["recovered"]
    assert secondary.received[-1] == b"\x01\x00" * 640
    assert len(opens) == 1, "only the resilience wrapper owns recovery"
    assert socket.closed and source_closed.is_set()


async def _collect(iterator):
    return [item async for item in iterator]


def _playback(provider):
    sent = []

    async def send_audio(call_id, data):
        sent.append(data)

    pipeline = SimpleNamespace(
        tts_provider=provider,
        tts_sample_rate=16000,
        stt_provider=None,
        media_gateway=SimpleNamespace(send_audio=send_audio),
        latency_tracker=MagicMock(),
        _record_silent_turn=MagicMock(),
    )
    session = SimpleNamespace(call_id="offline-tts", voice_id="voice", turn_id=1)
    return TtsPlayback(pipeline), pipeline, session, sent


@pytest.mark.asyncio
async def test_partial_provider_error_stops_turn_and_does_not_claim_full_sentence():
    class Provider:
        async def stream_synthesize(self, *args, **kwargs):
            yield AudioChunk(data=b"\x01\x00" * 80, sample_rate=16000)
            raise ElevenLabsPartialAudioError("synthetic partial failure")

    playback, pipeline, session, sent = _playback(Provider())
    stopped = await playback.synthesize_and_send(session, "Unfinished answer.", track_latency=False)
    assert stopped and session._tts_delivery_failed
    assert session._tts_failure_reason == "tts_provider_error"
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_terminal_no_audio_timeout_is_visible_and_runs_emergency(monkeypatch):
    class Provider:
        attempts = 0

        async def stream_synthesize(self, *args, **kwargs):
            self.attempts += 1
            await asyncio.Event().wait()
            yield

    monkeypatch.setattr("app.domain.services.voice_pipeline.tts_playback._TTS_INTER_CHUNK_TIMEOUT_S", .01, raising=False)
    provider = Provider()
    playback, pipeline, session, sent = _playback(provider)
    emergencies = []

    async def emergency(*args):
        emergencies.append(True)
        return True

    monkeypatch.setattr(playback, "_try_emergency_voice_clip", emergency)
    stopped = await playback.synthesize_and_send(session, "Unheard answer.", track_latency=False)
    assert stopped and session._tts_delivery_failed
    assert session._tts_failure_reason == "tts_timeout"
    assert provider.attempts == 2 and not sent
    assert emergencies == [True]
    pipeline._record_silent_turn.assert_called_once_with(session.call_id, "tts_timeout")


@pytest.mark.asyncio
async def test_cartesia_socket_eof_without_own_done_is_failure():
    class Socket:
        closed = True

        async def send_str(self, payload):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    with pytest.raises(RuntimeError, match="completion"):
        await _collect(CartesiaTTSProvider()._stream_over_ws(Socket(), {"context_id": "audit"}, 16000))


@pytest.mark.asyncio
async def test_stt_fallback_does_not_replay_committed_turn():
    class Primary:
        name = "primary"

        async def stream_transcribe(self, audio, **kwargs):
            await anext(audio)
            yield TranscriptChunk(text="First turn.", is_final=True)
            yield TranscriptChunk(text="", is_final=True)
            await anext(audio)
            raise RuntimeError("drop during next turn")

    async def audio():
        for i in (1, 2, 3):
            yield AudioChunk(data=bytes([i]) * 320, sample_rate=16000)

    secondary = _Secondary()
    result = await _collect(ResilientSTTProvider(Primary(), secondary).stream_transcribe(audio()))
    assert [chunk.text for chunk in result] == ["First turn.", "", "recovered"]
    assert secondary.received == [bytes([2]) * 320, bytes([3]) * 320]


@pytest.mark.asyncio
async def test_stt_cancellation_closes_sender_socket_and_call_input(monkeypatch):
    reading = asyncio.Event()
    input_closed = asyncio.Event()

    async def audio():
        try:
            reading.set()
            await asyncio.Event().wait()
            yield
        finally:
            input_closed.set()

    class Socket:
        closed = False

        async def send(self, payload):
            pass

        async def close(self):
            self.closed = True

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

    socket = Socket()

    async def connect(*a, **k):
        return socket

    monkeypatch.setattr("app.infrastructure.stt.deepgram_flux.websockets.connect", connect)
    primary = DeepgramFluxSTTProvider()
    await primary.initialize({"api_key": "offline-test"})
    secondary = _Secondary()
    task = asyncio.create_task(_collect(ResilientSTTProvider(primary, secondary).stream_transcribe(audio())))
    await reading.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert input_closed.is_set() and socket.closed
    assert secondary.received == []


@pytest.mark.asyncio
async def test_tts_barge_in_closes_abandoned_provider_iterator():
    closed = asyncio.Event()
    barge = asyncio.Event()

    class Provider:
        async def stream_synthesize(self, *a, **k):
            try:
                yield AudioChunk(data=bytes(320), sample_rate=16000)
                await asyncio.Event().wait()
            finally:
                closed.set()

    playback, pipeline, session, _ = _playback(Provider())

    async def send_audio(*args):
        barge.set()

    pipeline.media_gateway.send_audio = send_audio
    stopped = await playback.synthesize_and_send(session, "Interrupted.", barge_in_event=barge, track_latency=False)
    assert stopped and closed.is_set()
    assert not getattr(session, "_tts_delivery_failed", False)


@pytest.mark.asyncio
async def test_tts_wrapper_deadline_reaches_secondary_without_outer_retry(monkeypatch):
    class Primary:
        name = "elevenlabs"
        calls = 0

        async def stream_synthesize(self, *args, **kwargs):
            self.calls += 1
            await asyncio.Event().wait()
            yield

    class Secondary:
        name = "cartesia"
        calls = 0

        async def stream_synthesize(self, text, voice_id, sample_rate, **kwargs):
            self.calls += 1
            assert voice_id == "secondary-voice"
            yield AudioChunk(data=bytes(640), sample_rate=sample_rate)

    primary, secondary = Primary(), Secondary()
    wrapper = ResilientTTSProvider(primary, secondary, TTSFailoverPolicy(voice_id_map={"voice": "secondary-voice"}))
    wrapper.startup_attempt_timeout_seconds = .01
    playback, pipeline, session, sent = _playback(wrapper)
    assert await playback.synthesize_and_send(session, "Recovered.", track_latency=False) is False
    assert primary.calls == secondary.calls == 1
    assert len(sent) == 1 and session._tts_delivery_status == "submitted"


@pytest.mark.asyncio
async def test_cross_vendor_tts_never_sends_unmapped_voice():
    class Primary:
        name = "cartesia"

        async def stream_synthesize(self, *a, **k):
            raise RuntimeError("primary unavailable")
            yield

    class Secondary:
        name = "elevenlabs"
        called = False

        async def stream_synthesize(self, *a, **k):
            self.called = True
            yield AudioChunk(data=bytes(320), sample_rate=16000)

    secondary = Secondary()
    wrapper = ResilientTTSProvider(Primary(), secondary)
    with pytest.raises(RuntimeError, match="voice mapping"):
        await _collect(wrapper.stream_synthesize("test", "wrong-vendor-voice"))
    assert secondary.called is False


@pytest.mark.asyncio
async def test_tts_fallback_pcm_conversion_preserves_split_sample():
    import numpy as np

    class Primary:
        name = "cartesia"

        async def stream_synthesize(self, *a, **k):
            raise RuntimeError("primary unavailable")
            yield

    samples = np.array([1000, -2000, 3000], dtype=np.int16)
    data = samples.tobytes()

    class Secondary:
        name = "elevenlabs"

        async def stream_synthesize(self, *a, **k):
            for part in (data[:3], data[3:]):
                yield AudioChunk(data=part, sample_rate=16000)

    wrapper = ResilientTTSProvider(Primary(), Secondary(), TTSFailoverPolicy(voice_id_map={"a": "b"}))
    chunks = await _collect(wrapper.stream_synthesize("test", "a"))
    actual = np.frombuffer(b"".join(chunk.data for chunk in chunks), dtype=np.float32)
    np.testing.assert_allclose(actual, samples.astype(np.float32) / 32768)


@pytest.mark.asyncio
async def test_stt_recovery_clears_stale_partial_then_asks_once_at_turn_end():
    from unittest.mock import AsyncMock

    session = SimpleNamespace(
        call_id="offline-recovery", current_user_input="untrusted stale fragment",
        conversation_history=[], turn_id=2,
        _last_transcript_confidence=.9, _last_transcript_alternatives=("stale",),
    )
    pipeline = SimpleNamespace(
        _barge_in_events={}, synthesize_and_send_audio=AsyncMock(return_value=False),
        transcript_service=SimpleNamespace(accumulate_turn=MagicMock()),
    )
    ingest = AudioIngest(pipeline)
    reset = TranscriptChunk(text="", metadata={"stt_recovery": "reset"})
    repeat = TranscriptChunk(text="", metadata={"stt_recovery": "repeat_required"})
    assert await ingest._handle_stt_recovery(session, reset)
    assert session.current_user_input == "" and session._last_transcript_alternatives == ()
    pipeline.synthesize_and_send_audio.assert_not_called()
    assert await ingest._handle_stt_recovery(session, repeat)
    assert await ingest._handle_stt_recovery(session, repeat)
    pipeline.synthesize_and_send_audio.assert_awaited_once()
    pipeline.transcript_service.accumulate_turn.assert_called_once()
    assert len(session.conversation_history) == 1
    assert pipeline.transcript_service.accumulate_turn.call_args.kwargs["metadata"]["delivery_status"] == "submitted"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,evidence,matching,certified", [
    ("completed", "transport_played", True, True),
    ("completed", "transport_played", False, False),
    ("transmitted", "transmitted", True, False),
    ("unknown", "unknown", True, False),
    ("interrupted", "unknown", True, False),
])
async def test_only_matching_playout_receipt_certifies_spoken_sentence(status, evidence, matching, certified):
    class Provider:
        async def stream_synthesize(self, *args, **kwargs):
            yield AudioChunk(data=bytes(320), sample_rate=16000)
    playback, pipeline, session, _ = _playback(Provider())
    session._tts_playout_completed = True  # Previous sentence cannot leak through.
    started = []
    async def begin(call_id, uid):
        started.append(uid)
        assert session._tts_playout_completed is False
    async def finish(call_id, uid):
        assert started == [uid]
        return {"utterance_id": uid if matching else "other", "status": status, "evidence": evidence}
    pipeline.media_gateway.begin_playback = begin
    pipeline.media_gateway.finish_playback = finish
    stopped = await playback.synthesize_and_send(session, "Please confirm.", track_latency=False)
    assert session._tts_playout_completed is certified
    assert stopped is (status == "interrupted")
