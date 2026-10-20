"""Caller-first outbound pickup with total silence: the agent speaks first.

Live 2026-10-20: callee picked up and said nothing for 57.6 s (RMS 6-7), the
agent waited for the caller, and only the 60 s silence hangup ended it. Owner
decision: after CALLER_FIRST_SILENT_PICKUP_S (default 4.0, 0 disables) of
mutual silence the agent speaks first. Real CallSession throughout.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession, CallState
from app.domain.services.telephony.modes import agent_first
from app.domain.services.voice_pipeline import silent_pickup


def _vs(first_speaker="user", direction=None):
    s = CallSession(
        call_id="06a6f8c9-89c9-4707-ab3b-98d12f4930fe",
        campaign_id="c2b6734d-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1", provider_call_id="talky-out-1",
        system_prompt="sp", voice_id="v1",
    )
    s.state = CallState.LISTENING
    s._first_speaker = first_speaker
    vs = SimpleNamespace(call_id=s.call_id, call_session=s, config=None)
    if direction:
        vs._call_direction = direction
    return vs


@pytest.fixture
def greeted(monkeypatch):
    calls = []

    async def fake(vs):
        calls.append(vs)

    monkeypatch.setattr(agent_first, "_send_outbound_greeting", fake)
    return calls


def _run(vs, wait_s=0.05):
    return asyncio.run(silent_pickup.run_silent_pickup(vs, wait_s=wait_s))


def test_fires_after_wait_when_silent(greeted):
    vs = _vs()
    t0 = time.monotonic()
    assert _run(vs, 0.1) is True
    assert time.monotonic() - t0 >= 0.09
    assert greeted == [vs]


def _spoke_user(vs):
    vs.call_session.conversation_history.append(Message(role=MessageRole.USER, content="hi"))


def _spoke_turn_open(vs):
    vs.call_session._caller_turn_open_since = time.monotonic()


def _spoke_partial(vs):
    vs.call_session.current_user_input = "hel"


def _spoke_onset(vs):
    vs.call_session._caller_voice_onset_at = time.monotonic()


def _agent_talking(vs):
    vs.call_session.tts_active = True


def _machine_heard(vs):
    vs.call_session._machine_screening = True     # a quiet screener/voicemail was detected


@pytest.mark.parametrize("mark", [_spoke_user, _spoke_turn_open, _spoke_partial, _spoke_onset, _agent_talking, _machine_heard])
def test_skips_when_caller_spoke(greeted, mark):
    vs = _vs()
    mark(vs)
    assert _run(vs) is False
    assert greeted == []


def test_skips_agent_first(greeted):
    assert _run(_vs(first_speaker="agent")) is False
    assert greeted == []


def test_skips_true_inbound(greeted):
    assert _run(_vs(direction="inbound")) is False
    assert greeted == []


@pytest.mark.parametrize("state", [CallState.ENDING, CallState.ENDED, CallState.ERROR])
def test_skips_ended_call(greeted, state):
    vs = _vs()
    vs.call_session.state = state
    assert _run(vs) is False
    assert greeted == []


def test_disabled_with_zero(greeted, monkeypatch):
    monkeypatch.setenv("CALLER_FIRST_SILENT_PICKUP_S", "0")
    vs = _vs()
    assert silent_pickup.schedule_silent_pickup(vs) is None
    assert greeted == []


def test_wait_is_configurable(monkeypatch):
    monkeypatch.delenv("CALLER_FIRST_SILENT_PICKUP_S", raising=False)
    assert silent_pickup.silent_pickup_wait_s() == 4.0
    monkeypatch.setenv("CALLER_FIRST_SILENT_PICKUP_S", "2.5")
    assert silent_pickup.silent_pickup_wait_s() == 2.5
    monkeypatch.setenv("CALLER_FIRST_SILENT_PICKUP_S", "junk")
    assert silent_pickup.silent_pickup_wait_s() == 4.0


def test_cancel_before_wait_plays_nothing(greeted):
    async def go():
        vs = _vs()
        task = silent_pickup.schedule_silent_pickup(vs, wait_s=5.0)
        assert task is not None
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return vs

    asyncio.run(go())
    assert greeted == []


def test_schedule_stores_task_for_teardown(greeted):
    async def go():
        vs = _vs()
        task = silent_pickup.schedule_silent_pickup(vs, wait_s=5.0)
        assert vs._greeting_task is task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())


def test_lifecycle_hook_is_wired_in_caller_first_branch():
    import inspect
    from app.domain.services.telephony import lifecycle

    src = inspect.getsource(lifecycle)
    i = src.index("caller_first_no_greeting")
    assert "schedule_silent_pickup(voice_session)" in src[i:i + 1500]
