"""Real synthetic replies exposed unsolicited END_CALL after a failed email."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.end_session_action import caller_signaled_end, should_honor_end_session
from app.domain.services.voice_pipeline.action_tools import action_tools_for_turn, run_voice_action
from app.domain.services.voice_pipeline.end_call import model_end_call_allowed, strip_and_flag
from app.domain.services.voice_pipeline.identity_disposition import (
    contains_dnc, contains_explicit_goodbye,
)


NON_ASSERTED_CLOSES = [
    "I don't want to hang up; I need help", "Please don't you hang up",
    "I'm not ready to say goodbye", "I won't hang up", "I haven't said goodbye",
    "She said goodbye, but I still have a question", "You just said goodbye",
    "Goodbye is what the agent said", "Did I say goodbye?",
    "Will saying goodbye end the call?", "The word goodbye is in your script",
    "If I say goodbye, will the call end?", "I don't call it Acme anymore",
    "Goodbye, wait, I have another question", "No thanks, what is your price?",
    "No thanks to SMS, but can you explain the price?",
    "What does 'do not call me again' mean?", 'Explain "stop calling me" please',
    "'don't call me again' is a phrase", "‘don’t call me again’ is a phrase",
    "He said don't call me again", "Don't stop calling me", "Don't unsubscribe me",
    "I do not want you to stop calling me", "I will not unsubscribe",
]


@pytest.mark.parametrize('text', NON_ASSERTED_CLOSES)
async def test_mentions_and_current_requests_cannot_authorize_any_close_path(text):
    session = SimpleNamespace(conversation_history=[Message(role=MessageRole.USER, content=text)],
        captured_slots=SimpleNamespace(declined_count=2))
    assert not caller_signaled_end(text)
    assert not contains_explicit_goodbye(text)
    assert not contains_dnc(text)
    assert not model_end_call_allowed(session)
    assert not should_honor_end_session({'reason': 'conversation_complete', 'do_not_call': True}, text, 8, 2)
    assert strip_and_flag(session, 'Goodbye. [[END_CALL]]') == 'Goodbye.'
    assert not getattr(session, '_end_call_requested', False)
    assert (await run_voice_action(session, 'end_call', user_text=text))['success'] is False
    provider = SimpleNamespace(supports_tools=True, stream_chat_with_tools=AsyncMock())
    assert any(tool['function']['name'] == 'end_call'
        for tool in action_tools_for_turn(session.conversation_history, provider))


@pytest.mark.parametrize('text,dnc', [
    ('Goodbye, take care', False), ('Wait, actually goodbye', False),
    ('Can you end the call please?', False), ("I'm done, thanks", False),
    ('Please stop calling me', True), ("I'd like to unsubscribe please", True),
    ("Do not call this number again", True), ("Take me off your list", True),
    ('You said goodbye. Anyway, stop calling me.', True),
])
def test_latest_real_caller_assertion_still_authorizes_close(text, dnc):
    assert caller_signaled_end(text)
    assert contains_dnc(text) is dnc
    assert should_honor_end_session({'reason': 'user_goodbye'}, text, 1, 0)


def test_historical_declines_require_a_current_decline_not_a_new_help_request():
    action = {'reason': 'conversation_complete'}
    assert should_honor_end_session(action, "I don't want this", 5, 2)
    for text in ['Please do not hang up', 'Can you help me?', 'Yes please', 'I need help']:
        assert not should_honor_end_session(action, text, 5, 2)
        assert not model_end_call_allowed(SimpleNamespace(captured_slots=SimpleNamespace(declined_count=2)), text)


@pytest.mark.parametrize("text", [
    "Please email it to me.", "It failed, can you try another way?",
    "Don't hang up, I have another question.", "Why did you say goodbye?",
    "If I say goodbye, will the call end?", "I haven't said goodbye.",
    "No thanks to SMS, but can you explain the price?",
])
async def test_shared_tools_and_sentinel_cannot_end_without_caller_intent(text):
    session = SimpleNamespace(conversation_history=[Message(role=MessageRole.USER, content=text)])
    assert not caller_signaled_end(text)
    assert strip_and_flag(session, "I couldn't send that email. [[END_CALL]]") == "I couldn't send that email."
    assert not getattr(session, "_end_call_requested", False)
    result = await run_voice_action(session, "end_call", user_text=text)
    assert result["status"] == "caller_intent_unconfirmed"


@pytest.mark.parametrize("text", ["Goodbye.", "Bye", "Please end the call.", "Stop calling me.", "No thank you."])
def test_real_caller_end_still_arms_sentinel(text):
    session = SimpleNamespace(conversation_history=[Message(role=MessageRole.USER, content=text)])
    assert strip_and_flag(session, "Goodbye. [[END_CALL]]") == "Goodbye."
    assert session._end_call_requested is True


def test_machine_evidence_still_allows_silent_close():
    session = SimpleNamespace(_amd_voicemail=True)
    assert strip_and_flag(session, "[[END_CALL]]") == ""
    assert session._end_call_requested is True


def test_older_goodbye_cannot_authorize_new_turn_close():
    session = SimpleNamespace(conversation_history=[
        Message(role=MessageRole.USER, content="Goodbye"),
        Message(role=MessageRole.USER, content="Wait, I have another question"),
    ])
    assert not model_end_call_allowed(session)


def test_end_call_tool_uses_current_caller_not_previous_assistant_words():
    provider = SimpleNamespace(supports_tools=True, stream_chat_with_tools=AsyncMock())
    messages = [
        Message(role=MessageRole.ASSISTANT, content="Goodbye."),
        Message(role=MessageRole.USER, content="Wait, can you explain the price?"),
    ]
    assert any(tool["function"]["name"] == "end_call" for tool in action_tools_for_turn(messages, provider))
    messages.append(Message(role=MessageRole.USER, content="Goodbye."))
    assert any(tool["function"]["name"] == "end_call" for tool in action_tools_for_turn(messages, provider))


@pytest.mark.asyncio
async def test_real_turn_finisher_rejects_unsolicited_sentinel_after_failed_send(monkeypatch):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    service = _make_service_for_disposition(["I couldn't send that email. [[END_CALL]]"])
    service._shutdown_session_for_end_action = AsyncMock()
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.turn_id = 4
    session.current_user_input = "Please email it to me."
    await service.handle_turn_end(session, AsyncMock())
    service._shutdown_session_for_end_action.assert_not_awaited()
    assert not getattr(session, "_end_call_requested", False)
    assert any("couldn't send" in m.content for m in session.conversation_history if m.role == MessageRole.ASSISTANT)


@pytest.mark.asyncio
async def test_model_dnc_flag_cannot_opt_out_an_ordinary_goodbye():
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    service = _make_service_for_disposition([
        '{"action":"end_session","reason":"user_goodbye","farewell":"Goodbye.","do_not_call":true}'
    ])
    service._shutdown_session_for_end_action = AsyncMock()
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "Goodbye."
    await service.handle_turn_end(session, AsyncMock())
    service._shutdown_session_for_end_action.assert_awaited_once()
    assert not getattr(session, "_caller_opted_out", False)


@pytest.mark.parametrize('text', [
    "What does 'do not call me again' mean?", "Don't stop calling me",
    "She said goodbye, but I still have a question", "I don't want to hang up; I need help",
    "Please don't hang up", "I don't call it Acme anymore",
])
@pytest.mark.parametrize('reply', [
    'Goodbye. [[END_CALL]]',
    '{"action":"end_session","reason":"conversation_complete","farewell":"Goodbye.","do_not_call":true}',
])
async def test_real_finisher_never_opts_out_or_hangs_up_on_non_asserted_close(monkeypatch, text, reply):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    from app.services.scripts.call_state_tracker import CallState
    monkeypatch.setenv('TELEPHONY_FILLER_DELAY_MS', '0')
    service = _make_service_for_disposition([reply])
    service._shutdown_session_for_end_action = AsyncMock()
    session = _make_session()
    session.campaign_id = 'campaign-123'
    session.turn_id = 4
    session.captured_slots = CallState(declined_count=2)
    session.current_user_input = text
    await service.handle_turn_end(session, AsyncMock())
    assert not getattr(session, '_caller_opted_out', False)
    service._shutdown_session_for_end_action.assert_not_awaited()
    assert not getattr(session, '_end_call_requested', False)
    # It reached the normal conversation path instead of the pre-LLM DNC exit.
    assert bool([message for message in session.conversation_history if message.role == MessageRole.ASSISTANT]) is not reply.startswith("{")


@pytest.mark.parametrize('text,dnc', [('Please stop calling me', True), ('Goodbye, take care', False)])
async def test_real_finisher_preserves_actual_optout_and_goodbye(monkeypatch, text, dnc):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    monkeypatch.setenv('TELEPHONY_FILLER_DELAY_MS', '0')
    service = _make_service_for_disposition(['Goodbye. [[END_CALL]]'])
    service._shutdown_session_for_end_action = AsyncMock()
    session = _make_session()
    session.campaign_id = 'campaign-123'
    session.turn_id = 4
    session.current_user_input = text
    await service.handle_turn_end(session, AsyncMock())
    service._shutdown_session_for_end_action.assert_awaited_once()
    assert bool(getattr(session, '_caller_opted_out', False)) is dnc


@pytest.mark.parametrize('text', ['Goodbye. Please end this call.', 'Please hang up.', 'Please end the call.'])
@pytest.mark.parametrize('turn', [0, 4])
async def test_real_finisher_honors_explicit_caller_close_when_model_omits_control(monkeypatch, text, turn):
    from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session
    monkeypatch.setenv('TELEPHONY_FILLER_DELAY_MS', '0')
    # Observed Gemini result: a plain goodbye, no tool and no sentinel.
    service = _make_service_for_disposition(['Goodbye, take care.'])
    service._shutdown_session_for_end_action = AsyncMock()
    session = _make_session()
    session.campaign_id = 'campaign-123'
    session.turn_id = turn
    session.current_user_input = text
    await service.handle_turn_end(session, AsyncMock())
    service._shutdown_session_for_end_action.assert_awaited_once()
    assert not getattr(session, '_caller_opted_out', False)
