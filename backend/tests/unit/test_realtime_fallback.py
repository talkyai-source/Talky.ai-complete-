"""Fix 14 — mid-call realtime → cascaded fallback.

When the OpenAI Realtime session's websocket drops WHILE the call is still up,
the call must fall back to the cascaded pipeline instead of dying into dead
air. Detection lives in RealtimeBridge.run(); recovery wiring lives in the
telephony lifecycle layer.

Coverage:
  (a) simulated mid-call socket death → on_connection_lost invoked exactly once,
      the bridge arms the fallback (does NOT end as a normal 'call over').
  (b) normal end (socket still open / task cancelled) → callback NOT invoked.
  (c) recovery: the callback rebuilds the cascaded pipeline, swaps the task in,
      and the dying realtime task's done-callback does NOT double-tear-down.
  (d) REALTIME_FALLBACK_ENABLED=false → no recovery (today's behaviour).
  (e) a second connection-loss after fallback → no second attempt.
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

from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge
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


# ---------------------------------------------------------------------------
# Lifecycle recovery tests
# ---------------------------------------------------------------------------

class _FakeOrchestrator:
    def __init__(self, task):
        self._task = task
        self.calls = 0
        self.last_session = None

    async def start_cascaded_fallback(self, session):
        self.calls += 1
        self.last_session = session
        return self._task


def _patch_lifecycle(monkeypatch, *, orchestrator, session, teardown_counter):
    monkeypatch.setattr(lifecycle, "_get_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(
        lifecycle, "_state",
        lambda: SimpleNamespace(get_voice_session=lambda cid: session),
    )

    async def _fake_force_end(cid):
        teardown_counter["n"] += 1

    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", _fake_force_end)

    def _fake_track_task(coro):
        # Consume the coroutine so pytest doesn't warn; run it to completion.
        return asyncio.get_event_loop().create_task(coro)

    monkeypatch.setattr(lifecycle, "_track_task", _fake_track_task)


# ---------------------------------------------------------------------------
# (c) recovery swaps the pipeline in with no double teardown
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recovery_swaps_pipeline_and_no_double_teardown(monkeypatch):
    monkeypatch.setenv("REALTIME_FALLBACK_ENABLED", "true")

    old_task = asyncio.create_task(asyncio.sleep(0))
    await old_task  # completed, not cancelled, no exception
    new_task = asyncio.create_task(asyncio.sleep(0))

    vs = SimpleNamespace(
        call_id="call-uuid-1",
        pipeline_task=old_task,
        realtime_bridge=object(),
    )
    orch = _FakeOrchestrator(new_task)
    teardown = {"n": 0}
    _patch_lifecycle(monkeypatch, orchestrator=orch, session=vs, teardown_counter=teardown)

    await lifecycle._on_realtime_connection_lost("pbx-call-1", vs)

    assert orch.calls == 1
    assert vs.pipeline_task is new_task, "the cascaded task must be swapped in"
    assert vs._realtime_fallback_attempted is True
    assert teardown["n"] == 0, "successful fallback must NOT force-end the call"

    # The dying realtime task's done-callback fires with the OLD (superseded)
    # task — it must NOT trigger teardown now that the fallback has taken over.
    lifecycle._pipeline_done_cb(old_task, "pbx-call-1")
    assert teardown["n"] == 0, "a superseded task must never double-tear-down"

    await new_task


@pytest.mark.asyncio
async def test_recovery_falls_through_to_teardown_when_build_fails(monkeypatch):
    monkeypatch.setenv("REALTIME_FALLBACK_ENABLED", "true")

    vs = SimpleNamespace(
        call_id="call-uuid-1", pipeline_task=None, realtime_bridge=object(),
    )
    orch = _FakeOrchestrator(None)  # rebuild produced no pipeline
    teardown = {"n": 0}
    _patch_lifecycle(monkeypatch, orchestrator=orch, session=vs, teardown_counter=teardown)

    await lifecycle._on_realtime_connection_lost("pbx-call-1", vs)
    await asyncio.sleep(0)  # let the tracked teardown task run

    assert orch.calls == 1
    assert teardown["n"] == 1, "a failed rebuild ends the call — same as today"


# ---------------------------------------------------------------------------
# (d) config gate: disabled → no recovery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disabled_gate_does_no_recovery(monkeypatch):
    monkeypatch.setenv("REALTIME_FALLBACK_ENABLED", "false")

    assert lifecycle._realtime_fallback_enabled() is False

    vs = SimpleNamespace(
        call_id="call-uuid-1", pipeline_task=None, realtime_bridge=object(),
    )
    orch = _FakeOrchestrator(asyncio.create_task(asyncio.sleep(0)))
    teardown = {"n": 0}
    _patch_lifecycle(monkeypatch, orchestrator=orch, session=vs, teardown_counter=teardown)

    await lifecycle._on_realtime_connection_lost("pbx-call-1", vs)

    assert orch.calls == 0, "no cascaded rebuild when the gate is off"
    assert teardown["n"] == 0, "disabled = today's behaviour, not a teardown"
    await orch._task


def test_gate_default_is_enabled(monkeypatch):
    monkeypatch.delenv("REALTIME_FALLBACK_ENABLED", raising=False)
    assert lifecycle._realtime_fallback_enabled() is True


# ---------------------------------------------------------------------------
# (e) second connection-loss after fallback → no second attempt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_connection_loss_does_not_reattempt(monkeypatch):
    monkeypatch.setenv("REALTIME_FALLBACK_ENABLED", "true")

    new_task = asyncio.create_task(asyncio.sleep(0))
    vs = SimpleNamespace(
        call_id="call-uuid-1", pipeline_task=None, realtime_bridge=object(),
    )
    orch = _FakeOrchestrator(new_task)
    teardown = {"n": 0}
    _patch_lifecycle(monkeypatch, orchestrator=orch, session=vs, teardown_counter=teardown)

    await lifecycle._on_realtime_connection_lost("pbx-call-1", vs)
    await lifecycle._on_realtime_connection_lost("pbx-call-1", vs)

    assert orch.calls == 1, "fallback is attempted at most once per call"
    await new_task
