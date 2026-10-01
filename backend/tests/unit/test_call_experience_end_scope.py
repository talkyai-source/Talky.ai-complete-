"""A short negative reply declines the preceding question, not every topic."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.action_tools import action_tools_for_turn, run_voice_action
from app.services.scripts.call_state_tracker import CallState


CASES = [
    ("Would you like details by email?", "No thanks.", False),
    ("Can I email it", "No thanks.", False),
    ("Can I email it?", "No thanks for now.", False),
    ("Would you like details by email?", "I'm not interested.", False),
    ("Do you have an existing payment provider?", "No thanks.", False),
    ("Would you like me to end this call?", "No thanks.", False),
    ("Is there anything else I can help you with?", "No thanks.", True),
    ("Anything else I can help with", "No thanks.", True),
    ("Got a minute to talk?", "No thanks.", True),
    ("Hi, I'm calling about payment terminals.", "I'm not interested.", True),
    ("Would you like details by email?", "No thanks. Goodbye.", True),
    ("Would you like details by email?", "I'm not interested in this call.", True),
    ("Would you like details by email?", "Please stop calling me.", True),
]


def _history(prior, caller):
    return [Message(role=MessageRole.ASSISTANT, content=prior), Message(role=MessageRole.USER, content=caller)]


@pytest.mark.parametrize("prior,caller,ends", CASES)
@pytest.mark.asyncio
async def test_selector_and_action_admission_preserve_question_scope(prior, caller, ends):
    history = _history(prior, caller)
    provider = SimpleNamespace(supports_tools=True, stream_chat_with_tools=AsyncMock())
    names = [tool["function"]["name"] for tool in action_tools_for_turn(history, provider)]
    assert ("end_call" in names) is ends
    # A newly generated assistant farewell is not what this caller answered.
    session = SimpleNamespace(conversation_history=history + [Message(role=MessageRole.ASSISTANT, content="Goodbye.")])
    result = await run_voice_action(session, "end_call", {}, user_text=caller)
    assert result["success"] is ends
    assert bool(getattr(session, "_end_call_requested", False)) is ends


@pytest.mark.parametrize("prior,caller,ends", CASES)
@pytest.mark.parametrize("reply", [
    '{"action":"end_session","reason":"user_done","farewell":"Goodbye."}',
    "Understood. [[END_CALL]]",
])
@pytest.mark.asyncio
async def test_actual_turn_runner_and_finisher_preserve_question_scope(monkeypatch, prior, caller, ends, reply):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    service = _make_service_for_disposition([reply])
    service._shutdown_session_for_end_action = AsyncMock()
    session = _make_session()
    session.campaign_id = "synthetic-campaign"
    session.turn_id = 4
    session._has_introduced = True
    session.current_user_input = caller
    session.conversation_history = _history(prior, caller)[:-1]
    session.captured_slots = CallState(declined_count=3)
    await service.handle_turn_end(session, AsyncMock())
    assert bool(service._shutdown_session_for_end_action.await_count) is ends
    if not ends:
        assert not getattr(session, "_end_call_requested", False)


@pytest.mark.parametrize("prior,caller,ends", CASES)
@pytest.mark.asyncio
async def test_realtime_tool_preserves_question_at_current_caller_boundary(prior, caller, ends):
    from tests.unit.test_realtime_end_call_ownership import _fixture, _events, _end_tool
    from app.realtime.openai import RealtimeEvent
    bridge, provider, _, ended, session = _fixture()
    bridge._remember_contact_turn("assistant", prior)
    await _events(bridge, provider, RealtimeEvent(kind="caller_transcript", text=caller, is_final=True))
    # This generated turn must not change what the caller's No referred to.
    bridge._remember_contact_turn("assistant", "Goodbye.")
    await _end_tool(bridge)
    bridge._goodbye_completed.set()
    if bridge._termination_task:
        await bridge._termination_task
    assert bool(ended.await_count) is ends
    assert bool(getattr(session, "_end_call_requested", False)) is ends
    if caller == "Please stop calling me.":
        assert session._caller_opted_out is True
    await bridge.stop()


@pytest.mark.asyncio
async def test_empty_model_reply_after_email_decline_does_not_invent_goodbye(monkeypatch):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    service = _make_service_for_disposition([])
    service._shutdown_session_for_end_action = AsyncMock()
    session = _make_session()
    session.campaign_id = "synthetic-campaign"
    session._has_introduced = True
    session.current_user_input = "No thanks."
    session.conversation_history = [Message(role=MessageRole.ASSISTANT, content="Would you like details by email?")]
    await service.handle_turn_end(session, AsyncMock())
    service._shutdown_session_for_end_action.assert_not_awaited()
    assert not getattr(session, "_end_call_requested", False)
    assert not any(m.role == MessageRole.ASSISTANT and m.content == "Goodbye." for m in session.conversation_history)


@pytest.mark.asyncio
async def test_realtime_delayed_final_keeps_question_from_caller_speech_start():
    from tests.unit.test_realtime_end_call_ownership import _fixture, _events, _end_tool
    from app.realtime.openai import RealtimeEvent
    bridge, provider, _, ended, session = _fixture()
    bridge._remember_contact_turn("assistant", "Can I email it?")
    await _events(bridge, provider, RealtimeEvent(kind="interrupted"))
    bridge._remember_contact_turn("assistant", "Goodbye.")
    await _events(bridge, provider, RealtimeEvent(kind="caller_transcript", text="No thanks.", is_final=True))
    await _end_tool(bridge)
    assert bridge._previous_assistant_text == "Can I email it?"
    assert bridge._termination_task is None
    assert not session._end_call_requested
    ended.assert_not_awaited()
    await bridge.stop()
