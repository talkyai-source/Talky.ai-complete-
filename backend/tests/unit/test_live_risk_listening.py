"""Live-risk listening fixes (audit 2026-10-20): listen-1 .. listen-5."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline.transcript_handler import TranscriptHandler
from app.domain.services.voice_pipeline.turn_ender import TurnEnder

CONFIRM = "Just to confirm, your email is sam at gmail dot com, is that right?"


def real_session(**over) -> CallSession:
    s = CallSession(
        call_id="aaaaaaaa-6b90-4c3e-9a1a-000000000000",
        campaign_id="790ca2db-0000-0000-0000-000000000000",
        lead_id="lead-1",
        provider_call_id="talky-out-1",
        system_prompt="sp",
        voice_id="v1",
    )
    s.state = CallState.LISTENING
    for k, v in over.items():
        setattr(s, k, v)
    return s


class _Chunk:
    def __init__(self, text="", is_final=True, confidence=None, metadata=None):
        self.text, self.is_final = text, is_final
        self.confidence, self.metadata = confidence, metadata or {}


class _STT:
    @staticmethod
    def detect_turn_end(chunk) -> bool:
        return bool(chunk.is_final) and not chunk.text


class _Tracker:
    def get_metrics(self, call_id): return None
    def start_turn(self, *a, **k): pass
    def mark_listening_start(self, *a, **k): pass
    def mark_stt_first_transcript(self, *a, **k): pass


class _TranscriptService:
    def bind_call_identity(self, *a, **k): pass
    def accumulate_turn(self, *a, **k): pass


class _Pipeline:
    def __init__(self):
        self.stt_provider, self.latency_tracker = _STT(), _Tracker()
        self.transcript_service = _TranscriptService()
        self._utterance_seq, self._pending_llm_tasks, self._barge_in_events = {}, {}, {}
        self.turn_calls = []

    def _is_repetitive_transcript(self, text): return False
    async def synthesize_and_send_audio(self, *a, **k): return False
    async def handle_barge_in(self, *a, **k): pass
    async def _cancel_turn_task(self, *a, **k): pass

    async def handle_turn_end(self, session, websocket=None, **k):
        self.turn_calls.append(k)


def _hist(*pairs):
    return [Message(role=r, content=c) for r, c in pairs]


A, U = MessageRole.ASSISTANT, MessageRole.USER


async def _run_turn(session, text):
    p = _Pipeline()
    try:
        await TurnEnder(p).handle(session, None, user_text=text)
    except AttributeError:
        pass  # past the echo guard the minimal stub pipeline has no LLM wiring


# ---- listen-1: self-echo guard must not eat a read-back / correction -------

@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Yes, sam at gmail dot com.", "No, it's sam at gmail dot com."])
async def test_listen1_readback_after_agent_finished_is_not_stripped(caplog, text):
    s = real_session()
    s.conversation_history = _hist((A, "Hi"), (U, "Hello"), (A, CONFIRM))
    s.tts_active = False
    s._last_tts_chunk_at = time.monotonic() - 10.0
    with caplog.at_level("INFO"):
        await _run_turn(s, text)
    assert "turn_skipped_self_echo" not in caplog.text
    assert "self_echo_stripped" not in caplog.text


@pytest.mark.asyncio
async def test_listen1_echo_while_agent_audio_is_live_is_still_skipped(caplog):
    s = real_session()
    s.conversation_history = _hist((A, "Hi"), (U, "Hello"), (A, CONFIRM))
    s.tts_active = True
    with caplog.at_level("INFO"):
        await _run_turn(s, "Yes, sam at gmail dot com.")
    assert "turn_skipped_self_echo" in caplog.text


@pytest.mark.asyncio
async def test_listen1_echo_just_after_last_chunk_is_still_skipped(caplog):
    s = real_session()
    s.conversation_history = _hist((A, "Hi"), (U, "Hello"), (A, CONFIRM))
    s.tts_active = False
    s._last_tts_chunk_at = time.monotonic() - 0.3
    with caplog.at_level("INFO"):
        await _run_turn(s, "sam at gmail dot com is that right")
    assert "turn_skipped_self_echo" in caplog.text


# ---- listen-2: backchannel filter in the transcript handler ----------------

async def _deliver(session, text):
    p = _Pipeline()
    h = TranscriptHandler(p)
    await h.handle(session, _Chunk(text=text, is_final=True))
    await h.handle(session, _Chunk(text="", is_final=True))
    for _ in range(3):
        tasks = [t for t in p._pending_llm_tasks.values() if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0)
    return p


@pytest.mark.asyncio
async def test_listen2_yes_to_agent_question_over_agent_speech_reaches_a_turn():
    s = real_session(current_user_input="Yes.")
    s.tts_active = True
    s.conversation_history = _hist((A, "Is this John? I'm calling from X about your quote."))
    s.conversation_history = _hist((A, "Hi"), (U, "Hello"), (A, "Are you the homeowner? I have a quote."))
    p = await _deliver(s, "Yes.")
    assert p.turn_calls, "a yes answering a question was swallowed"


@pytest.mark.asyncio
async def test_listen2_first_utterance_yes_over_agent_speech_reaches_a_turn():
    s = real_session(current_user_input="Yes.")
    s.tts_active = True
    s.conversation_history = _hist((A, "Is this John? I'm calling from X about your quote."))
    p = await _deliver(s, "Yes.")
    assert p.turn_calls


@pytest.mark.asyncio
async def test_listen2_plain_mhm_over_a_statement_is_still_suppressed():
    s = real_session(current_user_input="mhm")
    s.tts_active = True
    s.conversation_history = _hist((A, "Hi"), (U, "Hello"), (A, "We offer roofing and gutters."))
    p = await _deliver(s, "mhm")
    assert not p.turn_calls


# ---- listen-3: Nova must not end "cleanly" when its socket died -------------

import sys  # noqa: E402
import types  # noqa: E402
from enum import Enum  # noqa: E402

from app.domain.models.conversation import AudioChunk  # noqa: E402


class _EventType(str, Enum):
    MESSAGE = "message"
    ERROR = "error"
    OPEN = "open"
    CLOSE = "close"


@pytest.fixture
def _deepgram_stub(monkeypatch):
    if "deepgram.core.events" not in sys.modules:
        pkg = types.ModuleType("deepgram")
        core = types.ModuleType("deepgram.core")
        events = types.ModuleType("deepgram.core.events")
        events.EventType = _EventType
        core.events = events
        pkg.core = core
        pkg.AsyncDeepgramClient = object
        monkeypatch.setitem(sys.modules, "deepgram", pkg)
        monkeypatch.setitem(sys.modules, "deepgram.core", core)
        monkeypatch.setitem(sys.modules, "deepgram.core.events", events)
    yield


class _NovaConn:
    def __init__(self, send_error=None):
        self._send_error = send_error

    def on(self, *a): pass

    async def start_listening(self):
        await asyncio.sleep(3600)

    async def send_media(self, data):
        if self._send_error:
            raise self._send_error


class _NovaClient:
    def __init__(self, conn):
        class _Ctx:
            async def __aenter__(_s): return conn
            async def __aexit__(_s, *a): return False

        class _V1:
            def connect(_s, **kw): return _Ctx()

        class _Listen:
            v1 = _V1()

        self.listen = _Listen()


async def _one_chunk():
    yield AudioChunk(data=b"\x00" * 320, timestamp=0.0)


async def _drain_nova(conn):
    from app.infrastructure.stt.deepgram_nova import DeepgramNovaSTTProvider

    p = DeepgramNovaSTTProvider()
    p._client = _NovaClient(conn)
    return [c async for c in p.stream_transcribe(_one_chunk())]


@pytest.mark.asyncio
async def test_listen3_nova_socket_death_mid_audio_raises(_deepgram_stub):
    with pytest.raises(RuntimeError, match="socket"):
        await asyncio.wait_for(_drain_nova(_NovaConn(ConnectionError("closed"))), 10)


@pytest.mark.asyncio
async def test_listen3_nova_clean_audio_end_still_returns_quietly(_deepgram_stub):
    assert await asyncio.wait_for(_drain_nova(_NovaConn()), 10) == []


# ---- listen-4: a prewarmed Flux socket that died is not adopted -------------

class _FluxWS:
    def __init__(self, close_code=None):
        self.close_code = close_code
        self.sent = 0
        self.closed_called = False

    async def send(self, data): self.sent += 1
    async def close(self): self.closed_called = True
    def __aiter__(self): return self
    async def __anext__(self): raise StopAsyncIteration


class _Slot:
    async def __aexit__(self, *a): return None


@pytest.mark.asyncio
async def test_listen4_dead_prewarmed_flux_socket_is_replaced_before_first_audio():
    from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider, _FluxConnection

    p = DeepgramFluxSTTProvider()
    p._api_key = "k"
    dead = _FluxConnection(_FluxWS(close_code=1006), _Slot())
    fresh = _FluxConnection(_FluxWS(), _Slot())
    p._pre_connections["c1"] = dead

    async def _open(call_id):
        p._connections.add(fresh)
        return fresh

    p._open_connection = _open

    async def _audio():
        return
        yield  # pragma: no cover

    try:
        async for _ in p.stream_transcribe(_audio(), call_id="c1"):
            pass
    except Exception:
        pass
    assert dead.ws.sent == 0, "audio was sent into a socket the server had already closed"
    assert fresh.ws.sent >= 1, "no fresh socket was opened to replace the dead one"


# ---- listen-5: a failed audio feed is a terminal STT failure ----------------

class _ConsumingSTT:
    async def stream_transcribe(self, audio_stream, call_id=None, on_barge_in=None, **kw):
        async for _ in audio_stream:
            pass
        return
        yield  # pragma: no cover


def _ingest_pipeline(queue):
    from unittest.mock import MagicMock

    p = MagicMock()
    p.media_gateway.get_audio_queue.return_value = queue
    p.stt_provider = _ConsumingSTT()
    p._barge_in_events, p._barge_in_epoch = {}, {}
    return p


@pytest.mark.asyncio
async def test_listen5_missing_media_queue_while_stt_active_is_terminal():
    from app.domain.services.voice_pipeline.audio_ingest import AudioIngest, TerminalSTTError

    s = real_session()
    s.stt_active = True
    with pytest.raises(TerminalSTTError, match="audio feed"):
        await AudioIngest(_ingest_pipeline(None)).process(s)


@pytest.mark.asyncio
async def test_listen5_normal_shutdown_with_stt_inactive_does_not_raise():
    from app.domain.services.voice_pipeline.audio_ingest import AudioIngest

    s = real_session()
    s.stt_active = False  # ordinary teardown: the loop never runs
    await AudioIngest(_ingest_pipeline(asyncio.Queue())).process(s)
