"""transfer_call must not be cancelled at the generic 8 s tool limit, and a
cancelled LLM turn must not kill or orphan a running transfer (20 Nov 2026)."""
import asyncio
import json

import pytest

from app.infrastructure.llm import streaming
from app.infrastructure.llm.streaming import execute_tool_call


def _spec(name):
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


@pytest.fixture
def captured(monkeypatch):
    """Capture the timeout handed to asyncio.wait_for without sleeping."""
    seen = []

    async def fake_wait_for(aw, timeout):
        seen.append(timeout)
        return await aw

    monkeypatch.setattr(streaming.asyncio, "wait_for", fake_wait_for)
    return seen


@pytest.mark.asyncio
async def test_transfer_call_gets_long_timeout_and_returns_result(captured):
    async def runner(name, args):
        return {"ok": True}

    out = await execute_tool_call({"name": "transfer_call", "arguments": {}}, [_spec("transfer_call")], runner)
    assert json.loads(out) == {"ok": True}
    assert captured == [135.0]


@pytest.mark.asyncio
async def test_other_tools_keep_eight_seconds(captured):
    async def runner(name, args):
        return "x"

    for name in ("send_email", "schedule_callback", "end_call"):
        await execute_tool_call({"name": name, "arguments": {}}, [_spec(name)], runner)
    assert captured == [8.0, 8.0, 8.0]


@pytest.mark.asyncio
async def test_slow_non_transfer_tool_still_times_out(monkeypatch):
    async def runner(name, args):
        await asyncio.sleep(3600)

    real = asyncio.wait_for
    monkeypatch.setattr(streaming.asyncio, "wait_for", lambda aw, timeout: real(aw, timeout / 800))
    out = await execute_tool_call({"name": "send_email", "arguments": {}}, [_spec("send_email")], runner)
    assert json.loads(out)["status"] == "outcome_unknown"


def test_transfer_timeout_is_clamped_to_135():
    assert streaming.TOOL_TIMEOUT_SECONDS["transfer_call"] == 135.0
    assert streaming.DEFAULT_TOOL_TIMEOUT_SECONDS == 8.0


# ---- item 2: the transfer must outlive the LLM turn task -------------------
from types import SimpleNamespace  # noqa: E402

from app.domain.services.telephony import adapter_registry  # noqa: E402
from app.domain.services.voice_pipeline import action_execution as ax  # noqa: E402


class _FakeExecutor:
    """Same cancel/save contract as DurableActionExecutor.execute."""
    saved = []

    def __init__(self, pool):
        pass

    async def execute(self, *, executor, action, **kw):
        try:
            result = dict(await executor())
            result["action"] = action
            _FakeExecutor.saved.append(("completed" if result.get("success") else "failed", result))
            return result
        except asyncio.CancelledError:
            _FakeExecutor.saved.append(("unknown", {}))
            raise


@pytest.fixture
def rig(monkeypatch):
    _FakeExecutor.saved = []
    state = SimpleNamespace(release=asyncio.Event(), started=asyncio.Event(), cancelled=False, finished=False)
    session = SimpleNamespace(turn_id=2)
    ctx = {"call_id": "c", "lead_id": None, "campaign_id": "camp", "provider_call_id": "p",
           "brief": {"transfer_ring_seconds": 30}}

    async def pool(_s):
        return object()

    async def context(_s, _p):
        return ctx

    async def fake_transfer(*a, **kw):
        state.started.set()
        try:
            await state.release.wait()
        except asyncio.CancelledError:
            state.cancelled = True
            raise
        state.finished = True
        return {"status": "completed"}

    monkeypatch.setattr(ax, "_pool", pool)
    monkeypatch.setattr(ax, "_context", context)
    monkeypatch.setattr(ax, "_capabilities", lambda _c: {"transfer_call"})
    monkeypatch.setattr(ax, "_parameters", lambda *a: ({"destination": "+1"}, "summary"))
    monkeypatch.setattr(ax, "_confirmed", lambda *a: True)
    monkeypatch.setattr(ax, "_call_ids", lambda _s: ("tenant", "call"))
    monkeypatch.setattr(ax, "DurableActionExecutor", _FakeExecutor)
    monkeypatch.setattr(adapter_registry, "execute_transfer", fake_transfer)
    return SimpleNamespace(state=state, session=session)


def _start(rig):
    return asyncio.ensure_future(
        ax.execute_connected_voice_action(rig.session, "transfer_call", {}, "yes please"))


@pytest.mark.asyncio
async def test_cancelled_turn_does_not_cancel_ringing_transfer(rig):
    turn = _start(rig)
    await rig.state.started.wait()
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    await asyncio.sleep(0)
    assert rig.state.cancelled is False
    rig.state.release.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert rig.state.finished is True
    # the result that arrived later is recorded once, as the real outcome
    assert [s for s, _ in _FakeExecutor.saved] == ["completed"]
    assert not ax._DETACHED_TASKS


@pytest.mark.asyncio
async def test_connect_then_cancel_still_records_completed_once(rig):
    turn = _start(rig)
    await rig.state.started.wait()
    rig.state.release.set()   # handoff sets the result ...
    turn.cancel()             # ... and _on_transfer_connected cancels the turn before it resumes
    with pytest.raises(asyncio.CancelledError):
        await turn
    for _ in range(20):
        await asyncio.sleep(0)
    assert [s for s, _ in _FakeExecutor.saved] == ["completed"]
    assert _FakeExecutor.saved[0][1]["action"] == "transfer_call"
    assert not ax._DETACHED_TASKS
