"""
TKT-008 parity, divergence #8 — Nova must emit a BargeInSignal, like Flux.

Barge-in needs two things to happen:

  1. the direct ``on_barge_in`` callback, which stops TTS playback; and
  2. a ``BargeInSignal`` on the transcript stream, which ``TranscriptHandler``
     routes to ``handle_barge_in()`` — the path that cancels the in-flight LLM
     task, rolls back speculative conversation history, and annotates the last
     assistant turn "[interrupted by caller]".

Flux emitted both. Nova emitted only (1), so under Nova — whether selected in AI
Options or promoted mid-call by the resilient failover wrapper — the agent went
quiet on interruption but **kept generating**, and the stored history kept text
the caller never heard.

The requirement these tests defend: downstream turn logic must not have to know
which provider produced a chunk.
"""

from __future__ import annotations

import asyncio
import sys
import types
from enum import Enum
from typing import Any, AsyncIterator

import pytest

from app.domain.models.conversation import AudioChunk, BargeInSignal
from app.infrastructure.stt.deepgram_nova import DeepgramNovaSTTProvider


class _EventType(str, Enum):
    """Stand-in for deepgram.core.events.EventType."""

    MESSAGE = "message"
    ERROR = "error"
    OPEN = "open"
    CLOSE = "close"


@pytest.fixture(autouse=True)
def _stub_deepgram_sdk(monkeypatch):
    """
    The deepgram SDK is not installed in every environment, and these tests do not
    need it — the provider is driven through a fake client. `stream_transcribe`
    imports `EventType` lazily, so a module stub is enough.
    """
    if "deepgram.core.events" in sys.modules:
        yield
        return

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


class _Msg:
    """Minimal stand-in for a Deepgram SDK message."""

    def __init__(self, mtype: str) -> None:
        self.type = mtype


class _Results(_Msg):
    """An interim Results message carrying ``text``."""

    def __init__(self, text: str, is_final: bool = False) -> None:
        super().__init__("Results")
        alt = types.SimpleNamespace(transcript=text, confidence=0.9)
        self.channel = types.SimpleNamespace(alternatives=[alt])
        self.is_final = is_final
        self.speech_final = False


class _FakeConn:
    """Records handlers, then replays a scripted SpeechStarted on start_listening."""

    def __init__(self, script: list[Any]) -> None:
        self._handlers: dict[Any, Any] = {}
        self._script = script

    def on(self, event_type: Any, handler: Any) -> None:
        self._handlers[event_type] = handler

    async def start_listening(self) -> None:
        from deepgram.core.events import EventType

        handler = self._handlers.get(EventType.MESSAGE)
        for msg in self._script:
            if handler is not None:
                handler(msg)
            await asyncio.sleep(0)
        # Idle so the generator's teardown grace path can run.
        await asyncio.sleep(3600)

    async def send_media(self, data: bytes) -> None:
        return None


class _FakeConnectCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *a: Any) -> bool:
        return False


class _FakeClient:
    def __init__(self, conn: _FakeConn) -> None:
        outer = self

        class _V1:
            def connect(self, **kwargs: Any) -> _FakeConnectCtx:
                outer.connect_kwargs = kwargs
                return _FakeConnectCtx(conn)

        class _Listen:
            v1 = _V1()

        self.listen = _Listen()
        self.connect_kwargs: dict[str, Any] = {}


async def _one_chunk() -> AsyncIterator[AudioChunk]:
    yield AudioChunk(data=b"\x00" * 320, timestamp=0.0)


async def _collect(provider: DeepgramNovaSTTProvider, limit: int = 1, timeout: float = 5.0) -> list:
    """Pull up to `limit` items off the provider's stream, then stop."""
    out: list = []

    async def _run() -> None:
        async for item in provider.stream_transcribe(_one_chunk(), on_barge_in=lambda: fired.append(True)):
            out.append(item)
            if len(out) >= limit:
                return

    fired: list = []
    try:
        await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    provider._fired = fired  # type: ignore[attr-defined]
    return out


# 2026-09-28 (call d3a21591): a bare SpeechStarted used to fire barge-in at
# once. Nova takes over on loud lines, its VAD fires on the noise, and ten
# replies were cut off in 90 s with no caller words behind them. SpeechStarted
# now arms the barge-in and the caller's first real words fire it. The parity
# requirement of TKT-008 still holds: when barge-in fires, BOTH the direct
# callback and the BargeInSignal happen, exactly as with Flux.


@pytest.mark.asyncio
async def test_real_words_after_speech_started_yield_a_barge_in_signal():
    provider = DeepgramNovaSTTProvider()
    provider._client = _FakeClient(
        _FakeConn([_Msg("SpeechStarted"), _Results("can you tell me the price")])
    )

    items = await _collect(provider, limit=1)

    assert items and isinstance(items[0], BargeInSignal), items
    assert items[0].text == "can you tell me the price"


@pytest.mark.asyncio
async def test_direct_callback_still_fires_alongside_the_signal():
    """Both halves are required. The signal must not have replaced the callback."""
    provider = DeepgramNovaSTTProvider()
    provider._client = _FakeClient(
        _FakeConn([_Msg("SpeechStarted"), _Results("wait a second please")])
    )

    await _collect(provider, limit=1)

    assert provider._fired, (  # type: ignore[attr-defined]
        "on_barge_in was not called — TTS would keep playing over the caller"
    )


@pytest.mark.asyncio
async def test_speech_started_with_no_words_does_not_interrupt():
    """Line noise: VAD fires, no words ever come. The agent keeps talking."""
    provider = DeepgramNovaSTTProvider()
    provider._client = _FakeClient(_FakeConn([_Msg("SpeechStarted")]))

    items = await _collect(provider, limit=1, timeout=1.0)

    assert not any(isinstance(i, BargeInSignal) for i in items), items
    assert not provider._fired  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_backchannel_does_not_interrupt_but_a_hard_interrupt_does():
    provider = DeepgramNovaSTTProvider()
    provider._client = _FakeClient(
        _FakeConn([_Msg("SpeechStarted"), _Results("yeah")])
    )
    items = await _collect(provider, limit=2, timeout=1.0)
    assert not any(isinstance(i, BargeInSignal) for i in items), items

    provider = DeepgramNovaSTTProvider()
    provider._client = _FakeClient(_FakeConn([_Msg("SpeechStarted"), _Results("stop")]))
    items = await _collect(provider, limit=1)
    assert items and isinstance(items[0], BargeInSignal), items


@pytest.mark.asyncio
async def test_words_without_a_speech_started_do_not_barge_in():
    """Unchanged: an ordinary interim transcript is not a barge-in."""
    provider = DeepgramNovaSTTProvider()
    provider._client = _FakeClient(_FakeConn([_Results("hello there")]))
    items = await _collect(provider, limit=1, timeout=1.0)
    assert not any(isinstance(i, BargeInSignal) for i in items), items
