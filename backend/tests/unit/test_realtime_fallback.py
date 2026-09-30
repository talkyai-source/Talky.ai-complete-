"""Realtime connection-loss notification and orderly bridge teardown.

The shared lifecycle reports failures; no alternative voice engine is started.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

# Resolve a pre-existing circular-import ordering quirk in this tree
# (app.core.security.tenant_isolation <-> app.api.v1.dependencies): importing
# dependencies first lets BOTH modules initialise fully before `lifecycle`
# pulls in call_service → tenant_isolation. In the full suite an earlier test
# already does this; the guarded import makes THIS file collectable when run
# first / in isolation too. (Tests may import app.api — only DOMAIN modules
# may not; see tests/unit/test_no_domain_api_imports.py.)
try:  # pragma: no cover - import-ordering shim
    import app.api.v1.dependencies  # noqa: F401
except Exception:  # noqa: BLE001
    pass

from app.realtime.bridge import RealtimeBridge
from app.domain.services.telephony import lifecycle


# ---------------------------------------------------------------------------
# Fakes for the bridge-level detection tests
# ---------------------------------------------------------------------------

class _FakeGateway:
    def __init__(self):
        self._q = asyncio.Queue()
        self.realtime_output = None

    def get_audio_queue(self, call_id):
        return self._q

    def set_realtime_output(self, call_id, value):
        self.realtime_output = value


class _FakeRT:
    """Fake OpenAIRealtimeSession. ``closed_value`` controls whether the socket
    reads as dead (an unexpected mid-call drop) or still open (a normal end)."""

    def __init__(self, *, closed_value: bool):
        self._closed_value = closed_value
        self.close_calls = 0

    def closed(self):
        return self._closed_value

    async def events(self):
        # An empty async generator: the model pump drains nothing and ends,
        # mirroring a receive loop that has already exited.
        if False:
            yield None
        return

    async def trigger_greeting(self):
        pass

    async def close(self):
        self.close_calls += 1


def _make_bridge(rt, *, session_active=True):
    return RealtimeBridge(
        call_id="call-uuid-1",
        realtime_session=rt,
        media_gateway=_FakeGateway(),
        internal_sample_rate=8000,
        session_active=(lambda: session_active),
    )


# ---------------------------------------------------------------------------
# (a) mid-call socket death → callback fires exactly once
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_socket_death_invokes_callback_exactly_once():
    calls = {"n": 0}

    async def _on_lost():
        calls["n"] += 1

    rt = _FakeRT(closed_value=True)  # socket already dead
    bridge = _make_bridge(rt, session_active=True)
    bridge.set_on_connection_lost(_on_lost)

    await asyncio.wait_for(bridge.run(), timeout=2.0)

    assert calls["n"] == 1, "connection-loss callback must fire exactly once"
    assert bridge._connection_lost is True
    assert bridge._connection_lost_fired is True
    # The bridge still cleaned up its own socket (idempotent stop()).
    assert rt.close_calls >= 1


@pytest.mark.asyncio
async def test_socket_death_callback_error_never_crashes_bridge():
    def _boom():
        raise RuntimeError("callback blew up")

    rt = _FakeRT(closed_value=True)
    bridge = _make_bridge(rt, session_active=True)
    bridge.set_on_connection_lost(_boom)

    # Must NOT raise — a callback error is swallowed.
    await asyncio.wait_for(bridge.run(), timeout=2.0)
    assert bridge._connection_lost_fired is True


# ---------------------------------------------------------------------------
# (b) normal end → callback NOT invoked
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_end_socket_open_does_not_invoke_callback():
    calls = {"n": 0}

    async def _on_lost():
        calls["n"] += 1

    rt = _FakeRT(closed_value=False)  # socket still open: not a drop
    bridge = _make_bridge(rt, session_active=True)
    bridge.set_on_connection_lost(_on_lost)

    await asyncio.wait_for(bridge.run(), timeout=2.0)

    assert calls["n"] == 0, "no fallback when the socket did not die"
    assert bridge._connection_lost is False


@pytest.mark.asyncio
async def test_normal_end_when_session_inactive_does_not_invoke_callback():
    # Socket dead BUT the call is no longer active (a hangup raced the drop) —
    # the normal teardown owns this, so the fallback must not fire.
    calls = {"n": 0}

    async def _on_lost():
        calls["n"] += 1

    rt = _FakeRT(closed_value=True)
    bridge = _make_bridge(rt, session_active=False)
    bridge.set_on_connection_lost(_on_lost)

    await asyncio.wait_for(bridge.run(), timeout=2.0)

    assert calls["n"] == 0
    assert bridge._connection_lost is False


@pytest.mark.asyncio
async def test_cancelled_run_does_not_invoke_callback():
    # A caller hangup cancels the pipeline task → CancelledError → the
    # connection-loss detection is skipped entirely.
    calls = {"n": 0}

    async def _on_lost():
        calls["n"] += 1

    class _BlockingRT(_FakeRT):
        async def events(self):
            await asyncio.sleep(60)  # keep the model pump alive until cancelled
            if False:
                yield None

    rt = _BlockingRT(closed_value=False)
    bridge = _make_bridge(rt, session_active=True)
    bridge.set_on_connection_lost(_on_lost)

    task = asyncio.create_task(bridge.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls["n"] == 0, "a cancelled (normal-hangup) run never falls back"
