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


@pytest.mark.asyncio
async def test_speech_started_yields_a_barge_in_signal():
    """The regression this ticket fixes: Nova emitted nothing on SpeechStarted."""
    provider = DeepgramNovaSTTProvider()
    provider._client = _FakeClient(_FakeConn([_Msg("SpeechStarted")]))

    items = await _collect(provider, limit=1)

    assert items, "Nova yielded nothing for SpeechStarted — handle_barge_in() would never run"
    assert isinstance(items[0], BargeInSignal), (
        f"expected BargeInSignal (the shared contract Flux emits), got {type(items[0]).__name__}"
    )


@pytest.mark.asyncio
async def test_barge_in_signal_has_empty_text_not_none():
    """
    Nova's SpeechStarted is a pure VAD event with no transcript yet, so empty text
    is correct — but it must be a string, because TranscriptHandler reads it.
    """
    provider = DeepgramNovaSTTProvider()
    provider._client = _FakeClient(_FakeConn([_Msg("SpeechStarted")]))

    items = await _collect(provider, limit=1)

    assert isinstance(items[0].text, str)
    assert items[0].text == ""


@pytest.mark.asyncio
async def test_direct_callback_still_fires_alongside_the_signal():
    """Both halves are required. The signal must not have replaced the callback."""
    provider = DeepgramNovaSTTProvider()
    provider._client = _FakeClient(_FakeConn([_Msg("SpeechStarted")]))

    await _collect(provider, limit=1)

    assert provider._fired, (  # type: ignore[attr-defined]
        "on_barge_in was not called — TTS would keep playing over the caller"
    )
