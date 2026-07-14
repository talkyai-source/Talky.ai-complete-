"""
Unit tests for the Cartesia barge-in / context-isolation fix.

Bug this locks in: the persistent per-call WebSocket multiplexes multiple
generations by `context_id`. A generation a barge-in abandons mid-stream
(tts_playback.py stops consuming without calling `.aclose()`) could leave
`data`/`done` frames for its OLD context still arriving after the NEXT
generation starts reading the same socket — `_stream_over_ws` never checked
`context_id`, so stale audio/`done` could contaminate or prematurely end
the following turn.

Three paths are locked in:
  1. A frame tagged with a different context_id is discarded, never
     yielded/treated as completion for the current generation.
  2. Abandoning (closing) a generation mid-stream sends a Cartesia
     `{"context_id": ..., "cancel": true}` control frame for ITS context —
     and a naturally-completed (`done`) generation sends no such frame.
  3. `stream_synthesize` never deadlocks/stalls indefinitely behind a lock
     a barge-in-abandoned generation is still (nominally) holding — it
     forces a fresh lock and best-effort cancels the stale context.
"""
import asyncio
import base64
import json

import aiohttp
import numpy as np
import pytest

from app.infrastructure.providers.provider_concurrency import reset_guards_for_tests
from app.infrastructure.tts.cartesia import CartesiaTTSProvider


def _audio_frame(context_id: str, value: int) -> str:
    pcm = np.array([value] * 4, dtype=np.int16).tobytes()
    return json.dumps({
        "context_id": context_id,
        "data": base64.b64encode(pcm).decode(),
    })


def _done_frame(context_id: str) -> str:
    return json.dumps({"context_id": context_id, "done": True})


class _FakeWSMessage:
    def __init__(self, msg_type, data=None):
        self.type = msg_type
        self.data = data


class _FakeWS:
    """Minimal fake of aiohttp.ClientWebSocketResponse for _stream_over_ws."""

    def __init__(self, messages):
        self._messages = list(messages)
        self._i = 0
        self.sent = []  # parsed JSON of every send_str call
        self.closed = False

    async def send_str(self, s):
        self.sent.append(json.loads(s))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._messages):
            raise StopAsyncIteration
        m = self._messages[self._i]
        self._i += 1
        return m

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_guards():
    reset_guards_for_tests()
    yield
    reset_guards_for_tests()


@pytest.mark.asyncio
async def test_stale_context_frames_are_discarded_not_yielded():
    """Q3/Q4: a frame from a DIFFERENT context_id is never treated as this
    generation's audio, and a naturally-`done` generation sends no cancel."""
    current_ctx = "current-ctx"
    stale_ctx = "stale-ctx-from-abandoned-turn"
    messages = [
        _FakeWSMessage(aiohttp.WSMsgType.TEXT, _audio_frame(stale_ctx, 99)),
        _FakeWSMessage(aiohttp.WSMsgType.TEXT, _audio_frame(current_ctx, 7)),
        _FakeWSMessage(aiohttp.WSMsgType.TEXT, _done_frame(current_ctx)),
    ]
    ws = _FakeWS(messages)
    payload = {"context_id": current_ctx, "transcript": "hi"}

    provider = CartesiaTTSProvider()
    chunks = []
    async for chunk in provider._stream_over_ws(ws, payload, 24000):
        chunks.append(chunk)

    assert len(chunks) == 1, "the stale-context frame must be discarded, not yielded"
    arr = np.frombuffer(chunks[0].data, dtype=np.float32)
    assert np.isclose(arr[0], 7 / 32768.0), "the surviving chunk must be from OUR context"
    cancels = [m for m in ws.sent if m.get("cancel") is True]
    assert cancels == [], "a clean `done` completion must not send a cancel frame"


@pytest.mark.asyncio
async def test_abandoned_generation_cancels_its_own_context():
    """Q3: closing a generation mid-stream (what eventually happens to a
    barge-in-abandoned generator via aclose()/GC) must cancel ITS context."""
    ctx = "abandoned-ctx"
    messages = [
        _FakeWSMessage(aiohttp.WSMsgType.TEXT, _audio_frame(ctx, 1)),
        _FakeWSMessage(aiohttp.WSMsgType.TEXT, _audio_frame(ctx, 2)),
        _FakeWSMessage(aiohttp.WSMsgType.TEXT, _done_frame(ctx)),
    ]
    ws = _FakeWS(messages)
    payload = {"context_id": ctx, "transcript": "hi"}

    provider = CartesiaTTSProvider()
    gen = provider._stream_over_ws(ws, payload, 24000)
    first = await gen.__anext__()
    assert first is not None

    # Simulate barge-in: the caller (tts_playback.py) stops consuming and
    # the generator is closed without ever reaching its own `done` frame.
    await gen.aclose()

    cancels = [m for m in ws.sent if m.get("cancel") is True]
    assert len(cancels) == 1, "abandoning mid-stream must send exactly one cancel"
    assert cancels[0]["context_id"] == ctx


@pytest.mark.asyncio
async def test_stream_synthesize_does_not_deadlock_behind_a_stale_lock(monkeypatch):
    """Q4: the NEXT generation for a call must not be stuck indefinitely
    behind a lock a barge-in-abandoned generation still (nominally) holds."""
    import app.infrastructure.tts.cartesia as cartesia_mod

    monkeypatch.setattr(cartesia_mod, "_WS_LOCK_ACQUIRE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(cartesia_mod.os, "urandom", lambda n: b"\xab" * n)

    provider = CartesiaTTSProvider()
    provider._session = object()  # not used: WS is pre-seeded below
    call_id = "call-under-test"

    # Simulate the abandoned generation: it still (nominally) holds the
    # call's lock and was last known to be working on "old-ctx".
    stuck_lock = asyncio.Lock()
    await stuck_lock.acquire()
    provider._call_ws_locks[call_id] = stuck_lock
    provider._call_active_context[call_id] = "old-ctx"

    new_ctx = (b"\xab" * 8).hex()
    ws = _FakeWS([
        _FakeWSMessage(aiohttp.WSMsgType.TEXT, _audio_frame(new_ctx, 5)),
        _FakeWSMessage(aiohttp.WSMsgType.TEXT, _done_frame(new_ctx)),
    ])
    provider._call_ws[call_id] = ws  # the same persistent WS the old generation used

    out = []
    await asyncio.wait_for(
        _collect(
            provider.stream_synthesize(
                "hi", "voice-1", sample_rate=24000, call_id=call_id
            ),
            out,
        ),
        timeout=1.0,
    )

    assert len(out) == 1, "the new generation must complete normally, not hang"
    assert provider._call_ws_locks[call_id] is not stuck_lock, "must have forced a fresh lock"
    cancels = [m for m in ws.sent if m.get("cancel") is True]
    assert any(c["context_id"] == "old-ctx" for c in cancels), (
        "forcing a fresh lock must best-effort cancel the stale context"
    )


async def _collect(aiter, out):
    async for item in aiter:
        out.append(item)
