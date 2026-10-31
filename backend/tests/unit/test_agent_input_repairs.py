"""Actual turn admission/closing paths with synthetic model and media ports."""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline import turn_ender
from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session


@pytest.fixture
def turn(monkeypatch):
    # These cases exercise orchestration, never a provider or persistence service.
    monkeypatch.setattr(turn_ender, "get_container", lambda: SimpleNamespace(is_initialized=False))
    service = _make_service_for_disposition([])
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _make_session()
    session.campaign_id = "campaign-input-test"
    session._line_phone_checked = True
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {}
    observed = []

    class Model:
        async def stream_chat_with_timeout(self, messages, **_kwargs):
            observed.append([message for message in messages if message.role == MessageRole.USER][-1].content)
            yield "I understand your answer."

    service.llm_provider = Model()
    return service, session, observed


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,previous", [
    ("Yes.", "Please confirm that address is correct."),
    ("Okay.", "I can explain those options."),
    ("Sure.", "Tell me if you want to continue."),
])
async def test_idle_final_short_answer_reaches_model(turn, reply, previous):
    service, session, observed = turn
    session.conversation_history = [
        Message(role=MessageRole.USER, content="Hello there."),
        Message(role=MessageRole.ASSISTANT, content=previous),
    ]
    session.current_user_input = reply
    assert not session.tts_active

    await service.handle_turn_end(session, AsyncMock())

    assert observed == [reply]
    assert session.conversation_history[-1].content == "I understand your answer."


@pytest.mark.asyncio
async def test_actual_overlap_still_suppresses_listener_acknowledgement(turn):
    service, session, observed = turn
    session.conversation_history = [
        Message(role=MessageRole.USER, content="Please explain."),
        Message(role=MessageRole.ASSISTANT, content="These are the available options."),
    ]
    session.tts_active = True
    session.current_user_input = "Okay."

    await service.handle_turn_end(session, AsyncMock())

    assert observed == []
    service.synthesize_and_send_audio.assert_not_awaited()
    assert session._last_backchannel_monotonic > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,confidence", [("7", 0.99), ("42", 0.99), ("-12.5", 0.9), ("+44 20 1234 5678", 0.9), ("42", None)])
async def test_numeric_first_answer_reaches_model(turn, reply, confidence):
    service, session, observed = turn
    session.conversation_history = [Message(role=MessageRole.ASSISTANT, content="Please tell me the number.")]
    session.current_user_input = reply

    await service.handle_turn_end(session, AsyncMock(), confidence=confidence)

    assert observed == [reply]
    assert session.conversation_history[-1].content == "I understand your answer."


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,confidence", [("42", 0.1), ("...", 0.99), ("?!", 0.99), ("L.", 0.99)])
async def test_recognition_floor_still_rejects_noise_or_low_confidence(turn, reply, confidence):
    service, session, observed = turn
    session.current_user_input = reply

    await service.handle_turn_end(session, AsyncMock(), confidence=confidence)

    assert observed == []
    service.synthesize_and_send_audio.assert_awaited_once()
    assert session.conversation_history[-1].role == MessageRole.ASSISTANT


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", ["Okay...", "Thank you", "Bye bye."])
async def test_fresh_caller_speech_revokes_actual_pending_close(turn, partial):
    service, session, _ = turn
    session.conversation_history = [Message(role=MessageRole.USER, content="Please explain the options.")]
    session.current_user_input = "Goodbye, please end this call."
    service._shutdown_session_for_end_action = AsyncMock()

    async def reply(current, _websocket):
        # A new utterance begins while the response to the prior goodbye is in flight.
        current.current_user_input = partial
        current._caller_speaking = True
        current._caller_speaking_since = time.monotonic()
        return "Goodbye.", 1.0, 1.0

    service._stream_llm_and_tts = AsyncMock(side_effect=reply)
    await service.handle_turn_end(session, AsyncMock())

    service._stream_llm_and_tts.assert_awaited_once()
    service._shutdown_session_for_end_action.assert_not_awaited()
    assert session._end_call_requested is False


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_speaking", [False, True])
async def test_quiet_or_stale_floor_still_allows_authorized_close(turn, stale_speaking):
    service, session, _ = turn
    session.conversation_history = [Message(role=MessageRole.USER, content="Please explain the options.")]
    session.current_user_input = "Goodbye, please end this call."
    service._shutdown_session_for_end_action = AsyncMock()

    async def reply(current, _websocket):
        current.current_user_input = "Okay."
        current._caller_speaking = stale_speaking
        current._caller_speaking_since = time.monotonic() - 60
        return "Goodbye.", 1.0, 1.0

    service._stream_llm_and_tts = AsyncMock(side_effect=reply)
    websocket = AsyncMock()
    await service.handle_turn_end(session, websocket)

    service._shutdown_session_for_end_action.assert_awaited_once_with(session, websocket, "agent_end_call", "")
