"""ElevenLabs: closing the provider while audio is STREAMING ends the request (26 Nov 2026).

Fault: ``cleanup()`` closed the session, but a request already reading the response
body was never woken (aiohttp 3.14.4: the task stayed inside ``streams.py``, the read
timeout did not fire) and kept one of the eight process-wide voice slots. Now the
provider tracks its open responses and ``cleanup()`` closes them before the session.

These tests use the REAL aiohttp session against a local in-process server (a fake
session cannot show this fault). Every wait has a hard limit so a regression fails
in seconds instead of hanging the suite.
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from aiohttp import web

from app.infrastructure.providers.provider_concurrency import reset_guards_for_tests
from app.infrastructure.tts import elevenlabs_tts
from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider, ProviderClosedError

LIMIT = 3.0
BODY = b"\x01\x00" * 1024


class _Obs:
    finishes: list = []

    def __init__(self, *a, **k):
        self.closed = False

    def accepted(self): pass
    def first_output(self): pass
    def complete(self): pass

    def finish(self, error=None, *, outcome=None):
        if self.closed:
            return
        self.closed = True
        _Obs.finishes.append((type(error).__name__ if error else None, outcome))


class _Server:
    def __init__(self):
        self.release = asyncio.Event()
        self.started = 0
        self.mode = "hang"            # "hang": headers + bytes, then wait; "complete": whole body

    async def handler(self, request):
        await request.read()
        resp = web.StreamResponse(status=200)
        resp.content_type = "application/octet-stream"
        await resp.prepare(request)
        await resp.write(BODY)
        self.started += 1
        if self.mode == "hang":
            try:
                await asyncio.wait_for(self.release.wait(), 30)
            except (asyncio.TimeoutError, asyncio.CancelledError, ConnectionError):
                pass
            return resp
        await resp.write_eof()
        return resp


@pytest.fixture
async def env(monkeypatch):
    reset_guards_for_tests()
    _Obs.finishes = []
    lease_failures = []
    monkeypatch.setattr(elevenlabs_tts, "ProviderAttempt", _Obs)
    lease_class = elevenlabs_tts._SingleKeyLease
    monkeypatch.setattr(lease_class, "report_failure",
                        lambda self, retryable=True: lease_failures.append(retryable))
    monkeypatch.delenv("ELEVENLABS_API_KEYS", raising=False)
    server = _Server()
    app = web.Application()
    app.router.add_post("/v1/text-to-speech/{voice}/stream", server.handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    provider = ElevenLabsTTSProvider()
    await provider.initialize({"api_key": "k-test", "voice_id": "v", "sample_rate": 8000})
    provider.API_BASE_URL = f"http://127.0.0.1:{port}"
    yield provider, server, lease_failures
    server.release.set()
    await provider.cleanup()
    await runner.cleanup()
    reset_guards_for_tests()


def _start(provider):
    """Start one synthesis; ``first`` is set when its first audio chunk arrived."""
    first = asyncio.Event()
    got = []

    async def run():
        async for chunk in provider.stream_synthesize("Hello there.", "v", sample_rate=8000):
            got.append(chunk.data)
            first.set()

    return asyncio.ensure_future(run()), first, got


@pytest.mark.asyncio
async def test_a_synthesis_streaming_when_the_provider_is_closed_ends_with_provider_closed(env, caplog):
    provider, server, lease_failures = env
    caplog.set_level(logging.DEBUG)
    task, first, _ = _start(provider)
    await asyncio.wait_for(first.wait(), LIMIT)
    assert provider._guard.in_flight == 1
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await asyncio.wait_for(provider.cleanup(), LIMIT)
    with pytest.raises(ProviderClosedError):
        await asyncio.wait_for(task, 1.0)
    assert loop.time() - t0 < 1.0
    assert provider._guard.in_flight == 0
    assert _Obs.finishes == [(None, "cancelled")]
    assert lease_failures == []
    assert "retry" not in caplog.text.lower()


@pytest.mark.asyncio
async def test_two_syntheses_streaming_at_once_both_end(env):
    provider, server, lease_failures = env
    t1, f1, _ = _start(provider)
    t2, f2, _ = _start(provider)
    await asyncio.wait_for(asyncio.gather(f1.wait(), f2.wait()), LIMIT)
    assert provider._guard.in_flight == 2
    await asyncio.wait_for(provider.cleanup(), LIMIT)
    done, pending = await asyncio.wait({t1, t2}, timeout=1.0)
    assert not pending
    for t in (t1, t2):
        assert isinstance(t.exception(), ProviderClosedError)
    assert provider._guard.in_flight == 0
    assert _Obs.finishes == [(None, "cancelled")] * 2
    assert lease_failures == []


@pytest.mark.asyncio
async def test_a_synthesis_that_completed_before_cleanup_is_untouched(env):
    provider, server, lease_failures = env
    server.mode = "complete"
    task, _, got = _start(provider)
    await asyncio.wait_for(task, LIMIT)
    assert b"".join(got) == BODY
    assert provider._inflight_responses == set()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await asyncio.wait_for(provider.cleanup(), LIMIT)
    assert loop.time() - t0 < 1.0
    assert provider._inflight_responses == set()
    assert provider._guard.in_flight == 0
    assert lease_failures == []


@pytest.mark.asyncio
async def test_cleanup_twice_is_harmless(env):
    provider, server, _ = env
    task, first, _ = _start(provider)
    await asyncio.wait_for(first.wait(), LIMIT)
    await asyncio.wait_for(provider.cleanup(), LIMIT)
    await asyncio.wait_for(provider.cleanup(), LIMIT)
    with pytest.raises(ProviderClosedError):
        await asyncio.wait_for(task, 1.0)


@pytest.mark.asyncio
async def test_nothing_stays_tracked_after_many_normal_syntheses(env):
    provider, server, _ = env
    server.mode = "complete"
    for _ in range(50):
        chunks = [c async for c in provider.stream_synthesize("Hi.", "v", sample_rate=8000)]
        assert b"".join(c.data for c in chunks) == BODY
    assert provider._inflight_responses == set()
    assert provider._guard.in_flight == 0


@pytest.mark.asyncio
async def test_a_warm_up_reading_its_reply_when_the_provider_is_closed_ends_quietly(env, caplog):
    """The warm-up request before a call's first sentence is woken too, and a closure
    of our own is neither a provider failure nor a warning."""
    provider, server, lease_failures = env
    caplog.set_level(logging.DEBUG)
    task = asyncio.ensure_future(provider.connect_for_call("call-1"))
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started == 1
    for _ in range(300):                         # until the client is reading the body
        if provider._inflight_responses:
            break
        await asyncio.sleep(0.01)
    assert len(provider._inflight_responses) == 1
    await asyncio.wait_for(provider.cleanup(), LIMIT)
    await asyncio.wait_for(task, 1.0)            # it returns: a warm-up never raises
    assert provider._inflight_responses == set()
    assert _Obs.finishes == [(None, "cancelled")]
    assert "connect_for_call failed" not in caplog.text


@pytest.mark.asyncio
async def test_cleanup_closes_the_session_even_when_closing_a_reply_fails(env):
    provider, server, lease_failures = env

    closed = []

    class _Reply:
        def __init__(self, broken):
            self.broken = broken

        def close(self):
            if self.broken:
                raise RuntimeError("boom")
            closed.append(self)

    session = provider._session
    replies = [_Reply(True), _Reply(False), _Reply(True), _Reply(False)]
    provider._inflight_responses.update(replies)
    await asyncio.wait_for(provider.cleanup(), LIMIT)      # it does not raise
    provider._inflight_responses.clear()
    assert provider._session is None
    assert session.closed
    assert len(closed) == 2                                 # the good ones were still closed
