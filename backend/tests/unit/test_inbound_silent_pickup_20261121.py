"""A true incoming call in caller-first mode where the caller says nothing.

Live 2026-11-20: two callers rang a caller-first campaign, the call was admitted
and answered, nobody spoke, and both hung up after ~20 s. After
INBOUND_SILENT_PICKUP_S (default 2.0, 0 disables) of silence the agent now greets
once. Outbound (CALLER_FIRST_SILENT_PICKUP_S, 4.0) is unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from app.domain.models.agent_config import AgentConfig
from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession, CallState
from app.domain.services.telephony.modes import agent_first
from app.domain.services.voice_pipeline import silent_pickup


def _session(first_speaker="user"):
    s = CallSession(
        call_id="b03611e1-1c3a-4707-ab3b-98d12f4930fe",
        campaign_id="d72a4d5c-8992-4038-aaf5-b54a885e7abe",
        lead_id="lead-1", provider_call_id="talky-in-1",
        system_prompt="sp", voice_id="v1",
    )
    s.state = CallState.LISTENING
    s._first_speaker = first_speaker
    s.agent_config = AgentConfig.model_construct(
        agent_name="Sarah jones", company_name="Demo Company"
    )
    return s


def _inbound_vs(greeting=None, action="agent", pipeline=True):
    s = _session()
    return SimpleNamespace(
        call_id=s.call_id, call_session=s, config=None,
        _call_direction="inbound", _inbound_admission={"call_id": s.call_id},
        _inbound_selected_action=action,
        _pinned_inbound_greeting=greeting,
        pipeline=object() if pipeline else None,
        pipeline_task=object(),  # set right after prepare_inbound_recording returns
        realtime_bridge=None,
    )


@pytest.fixture
def spoken(monkeypatch):
    calls = []

    async def fake(vs):
        calls.append(getattr(vs, "_pinned_inbound_greeting", None))

    monkeypatch.setattr(agent_first, "_send_outbound_greeting", fake)
    monkeypatch.setattr(silent_pickup, "_INBOUND_SETTLE_S", 0.01)
    monkeypatch.setattr(silent_pickup, "_INBOUND_READY_POLL_S", 0.01)
    monkeypatch.delenv("INBOUND_SILENT_PICKUP_S", raising=False)
    monkeypatch.delenv("CALLER_FIRST_SILENT_PICKUP_S", raising=False)
    return calls


def _run(vs, wait_s=0.05):
    return asyncio.run(silent_pickup.run_silent_pickup(vs, wait_s=wait_s))


def test_incoming_silence_greets_once_with_saved_greeting(spoken, caplog):
    vs = _inbound_vs(greeting="Welcome to Demo Company, Sarah speaking.")
    with caplog.at_level(logging.INFO):
        assert _run(vs) is True
    assert spoken == ["Welcome to Demo Company, Sarah speaking."]
    assert "inbound_silent_pickup call=b03611e1-1c3" in caplog.text


def test_incoming_without_saved_greeting_says_the_answering_sentence(spoken):
    vs = _inbound_vs(greeting=None)
    assert _run(vs) is True
    assert spoken == [
        "Hello, thank you for calling Demo Company. "
        "This is Sarah jones, an AI assistant. How can I help you?"
    ]
    assert "Who am I speaking with" not in spoken[0]


def test_incoming_without_saved_greeting_or_agent_name(spoken):
    vs = _inbound_vs(greeting="   ")
    vs.call_session.agent_config = AgentConfig.model_construct(agent_name="", company_name="Demo Company")
    assert _run(vs) is True
    assert spoken == [
        "Hello, thank you for calling Demo Company. This is an AI assistant. How can I help you?"
    ]


def test_scheduled_incoming_default_wait_is_two_seconds(monkeypatch):
    monkeypatch.delenv("INBOUND_SILENT_PICKUP_S", raising=False)
    assert silent_pickup.inbound_silent_pickup_wait_s() == 2.0
    monkeypatch.setenv("INBOUND_SILENT_PICKUP_S", "3.5")
    assert silent_pickup.inbound_silent_pickup_wait_s() == 3.5
    monkeypatch.setenv("INBOUND_SILENT_PICKUP_S", "junk")
    assert silent_pickup.inbound_silent_pickup_wait_s() == 2.0
    monkeypatch.setenv("INBOUND_SILENT_PICKUP_S", "0")
    assert silent_pickup.inbound_silent_pickup_wait_s() == 0.0


def test_schedule_uses_inbound_wait_for_incoming_and_outbound_wait_otherwise(monkeypatch, spoken):
    monkeypatch.setenv("INBOUND_SILENT_PICKUP_S", "7")
    monkeypatch.setenv("CALLER_FIRST_SILENT_PICKUP_S", "9")
    seen = []

    async def fake_run(vs, wait):
        seen.append(wait)

    monkeypatch.setattr(silent_pickup, "run_silent_pickup", fake_run)

    async def go():
        t1 = silent_pickup.schedule_silent_pickup(_inbound_vs())
        out = SimpleNamespace(call_id="x", call_session=_session(), config=None)
        t2 = silent_pickup.schedule_silent_pickup(out)
        await asyncio.gather(t1, t2)

    asyncio.run(go())
    assert seen == [7.0, 9.0]


def test_zero_disables_incoming(spoken, monkeypatch):
    monkeypatch.setenv("INBOUND_SILENT_PICKUP_S", "0")
    assert silent_pickup.schedule_silent_pickup(_inbound_vs()) is None
    assert spoken == []


def test_caller_spoke_before_wait_ended_no_greeting(spoken, caplog):
    vs = _inbound_vs()
    vs.call_session.conversation_history.append(Message(role=MessageRole.USER, content="hello"))
    with caplog.at_level(logging.INFO):
        assert _run(vs) is False
    assert spoken == []
    assert "inbound_silent_pickup_skipped call=b03611e1-1c3 reason=user_message" in caplog.text


@pytest.mark.parametrize("state", [CallState.ENDING, CallState.ENDED, CallState.ERROR])
def test_ended_call_no_greeting(spoken, state):
    vs = _inbound_vs()
    vs.call_session.state = state
    assert _run(vs) is False
    assert spoken == []


def test_llm_already_active_no_greeting(spoken, caplog):
    vs = _inbound_vs()
    vs.call_session.llm_active = True
    with caplog.at_level(logging.INFO):
        assert _run(vs) is False
    assert spoken == []
    assert "reason=llm_active" in caplog.text


def test_greeting_already_playing_no_greeting(spoken):
    vs = _inbound_vs()
    vs.call_session._instant_opener_in_flight = True
    assert _run(vs) is False
    assert spoken == []


def test_realtime_session_no_greeting(spoken):
    vs = _inbound_vs(pipeline=False)
    assert _run(vs) is False
    vs2 = _inbound_vs()
    vs2.realtime_bridge = object()
    assert _run(vs2) is False
    assert spoken == []


def test_voicemail_action_no_greeting(spoken):
    vs = _inbound_vs(action="voicemail")
    assert _run(vs) is False
    assert spoken == []


def test_agent_first_incoming_is_not_touched(spoken):
    vs = _inbound_vs()
    vs.call_session._first_speaker = "agent"
    assert _run(vs) is False
    assert spoken == []


def test_waits_for_recording_decision_before_greeting(spoken, monkeypatch):
    """pipeline_task is created right after prepare_inbound_recording returns;
    the greeting must not run (and so cannot pre-empt the notice) before it."""
    monkeypatch.setattr(silent_pickup, "_INBOUND_READY_POLL_S", 0.01)
    vs = _inbound_vs()
    vs.pipeline_task = None

    async def go():
        task = asyncio.create_task(silent_pickup.run_silent_pickup(vs, 0.02))
        await asyncio.sleep(0.15)
        assert spoken == []          # still waiting for the recording step
        vs.pipeline_task = object()  # prepare_inbound_recording returned
        return await task

    assert asyncio.run(go()) is True
    assert len(spoken) == 1


def _notice_then_pipeline(vs, *, caller_speaks_at=None, notice_s=0.15, wait=0.1):
    """Recorded call: the notice plays for notice_s, then the pipeline starts.
    Returns (greeted_after_pipeline_start_s or None)."""
    vs.pipeline_task = None
    vs.call_session._tts_delivery_status = "submitted"  # set only by the notice

    async def go():
        task = asyncio.create_task(silent_pickup.run_silent_pickup(vs, wait))
        await asyncio.sleep(notice_s)
        t_pipeline = time.monotonic()
        vs.pipeline_task = object()
        if caller_speaks_at is not None:
            await asyncio.sleep(caller_speaks_at)
            vs.call_session._caller_voice_onset_at = time.monotonic()  # buffered speech processed
        result = await task
        return result, t_pipeline

    return asyncio.run(go())


def test_recorded_call_caller_spoke_during_notice_no_greeting(spoken):
    vs = _inbound_vs()
    result, _ = _notice_then_pipeline(vs, caller_speaks_at=0.03)
    assert result is False
    assert spoken == []


def test_recorded_call_silence_greets_a_full_wait_after_pipeline_start(spoken, monkeypatch):
    stamps = []

    async def fake(vs):
        stamps.append(time.monotonic())
        spoken.append("greeted")

    monkeypatch.setattr(agent_first, "_send_outbound_greeting", fake)
    vs = _inbound_vs()
    result, t_pipeline = _notice_then_pipeline(vs, notice_s=0.15, wait=0.1)
    assert result is True
    assert stamps[0] - t_pipeline >= 0.095  # not immediately after the notice


def test_unrecorded_call_greets_about_the_wait_after_answer(spoken, monkeypatch):
    monkeypatch.setattr(silent_pickup, "_INBOUND_SETTLE_S", 0.02)
    vs = _inbound_vs()  # no notice: _tts_delivery_status stays None, pipeline already up
    t0 = time.monotonic()
    assert _run(vs, 0.2) is True
    assert 0.19 <= time.monotonic() - t0 < 0.35  # wait + settle, not wait + wait


def test_never_greets_when_recording_step_never_finishes(spoken, monkeypatch):
    monkeypatch.setattr(silent_pickup, "_INBOUND_READY_POLL_S", 0.01)
    monkeypatch.setattr(silent_pickup, "_INBOUND_READY_MAX_S", 0.05)
    vs = _inbound_vs()
    vs.pipeline_task = None
    assert _run(vs) is False
    assert spoken == []


def test_outbound_still_waits_four_seconds_and_uses_outbound_greeting(monkeypatch, spoken):
    monkeypatch.delenv("CALLER_FIRST_SILENT_PICKUP_S", raising=False)
    monkeypatch.setenv("INBOUND_SILENT_PICKUP_S", "0")  # must not affect outbound
    assert silent_pickup.silent_pickup_wait_s() == 4.0
    seen = []

    async def fake_run(vs, wait):
        seen.append(wait)

    monkeypatch.setattr(silent_pickup, "run_silent_pickup", fake_run)

    async def go():
        out = SimpleNamespace(call_id="x", call_session=_session(), config=None)
        await silent_pickup.schedule_silent_pickup(out)

    asyncio.run(go())
    assert seen == [4.0]

    monkeypatch.undo()
    monkeypatch.setattr(agent_first, "_send_outbound_greeting", lambda vs: _record(vs, spoken))
    out = SimpleNamespace(call_id="x", call_session=_session(), config=None)
    assert _run(out) is True
    assert spoken == ["outbound-path"]  # the unmodified outbound greeting entry point


async def _record(vs, spoken):
    spoken.append("outbound-path")
