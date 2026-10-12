"""Deepgram TTS: the post-barge-in background prewarm must not race the next
synthesis into opening two sockets (the loser used to be orphaned with a live
heartbeat), and cleanup() must cancel an in-flight prewarm."""
from __future__ import annotations

import asyncio

import aiohttp
import pytest

from app.infrastructure.tts.deepgram_tts import DeepgramTTSProvider


class _WS:
    def __init__(self, msgs=()):
        self.closed = False
        self._msgs = list(msgs)
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._msgs:
            return self._msgs.pop(0)
        raise StopAsyncIteration


class _Session:
    def __init__(self, delay=0.05, first_msgs=()):
        self.delay = delay
        self.opened: list[_WS] = []
        self._first_msgs = first_msgs

    async def ws_connect(self, url, headers=None, heartbeat=None):
        await asyncio.sleep(self.delay)
        ws = _WS(self._first_msgs if not self.opened else ())
        self.opened.append(ws)
        return ws

    async def close(self):
        pass


def _provider(session) -> DeepgramTTSProvider:
    p = DeepgramTTSProvider()
    p._api_key = "k"
    p._session = session
    p._synthesis_lock = asyncio.Lock()
    return p


@pytest.mark.asyncio
async def test_concurrent_get_connection_opens_one_socket():
    session = _Session()
    p = _provider(session)
    a, b = await asyncio.gather(
        p._get_connection("aura-2-andromeda-en", 24000),
        p._get_connection("aura-2-andromeda-en", 24000),
    )
    assert a is b
    assert len(session.opened) == 1


@pytest.mark.asyncio
async def test_prewarm_in_flight_is_reused_by_next_synthesis_not_duplicated():
    session = _Session(delay=0.05)
    p = _provider(session)
    prewarm = asyncio.create_task(p._prewarm_after_interrupt("aura-2-andromeda-en", 24000))
    await asyncio.sleep(0.01)  # prewarm handshake now in flight
    ws = await p._get_connection("aura-2-andromeda-en", 24000)  # next synthesis
    await prewarm
    assert len(session.opened) == 1
    assert p._warm_ws is ws
    assert not any(w.closed for w in session.opened)


@pytest.mark.asyncio
async def test_cleanup_cancels_in_flight_prewarm_and_leaks_no_socket():
    # First connect returns a ws whose stream ends without Flushed (an
    # interrupted synthesis) -> the finally block schedules a background prewarm.
    session = _Session(delay=0.0, first_msgs=[])
    p = _provider(session)
    gen = p.stream_synthesize("Hello there.", "aura-2-andromeda-en", 24000)
    async for _ in gen:
        pass
    # Slow down the prewarm handshake, then clean up while it is in flight.
    session.delay = 5.0
    task = getattr(p, "_prewarm_task", None)
    assert task is not None and not task.done()
    await asyncio.sleep(0.01)
    await p.cleanup()
    assert task.done()
    assert all(w.closed for w in session.opened) and len(session.opened) == 1
