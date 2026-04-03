import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.models.conversation import Message, MessageRole
from app.domain.models.conversation_state import ConversationContext, ConversationState
from app.domain.models.session import CallSession, CallState
from app.domain.services.voice_pipeline_service import VoicePipelineService


def _make_session() -> CallSession:
    session = CallSession(
        call_id="call-123",
        campaign_id="demo",
        lead_id="lead-123",
        provider_call_id="provider-123",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-123",
        conversation_state=ConversationState.GREETING,
        conversation_context=ConversationContext(),
        agent_config=AgentConfig(
            goal=AgentGoal.INFORMATION_GATHERING,
            business_type="voice ai platform",
            agent_name="Assistant",
            company_name="Talky.ai",
            rules=ConversationRule(),
            flow=ConversationFlow(),
            response_max_sentences=2,
        ),
    )
    session.barge_in_event = asyncio.Event()
    return session


def test_pricing_questions_get_extended_sentence_budget_for_custom_prompt_sessions():
    service = VoicePipelineService(
        stt_provider=MagicMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )

    limit = service._response_max_sentences_for_turn(
        _make_session(),
        "Can you explain all your plans and pricing?",
        has_custom_prompt=True,
    )

    assert limit == 4


def test_non_pricing_questions_keep_default_sentence_budget():
    service = VoicePipelineService(
        stt_provider=MagicMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )

    limit = service._response_max_sentences_for_turn(
        _make_session(),
        "What does Talky do?",
        has_custom_prompt=True,
    )

    assert limit == 2


class _StreamingLLMProvider:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.asyncio
async def test_get_llm_response_strips_reasoning_and_keeps_full_pricing_answer():
    service = VoicePipelineService(
        stt_provider=MagicMock(),
        llm_provider=_StreamingLLMProvider([
            "<think>I should outline the pricing plan first.</think>",
            "\n### Plans\n",
            "1. **Basic** is $29 per month. ",
            "2. **Professional** is $79 per month. ",
            "3. **Enterprise** is custom pricing.",
        ]),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    session.system_prompt = "Use plain spoken text only."
    session.conversation_history = [
        Message(role=MessageRole.USER, content="What are your packages and pricing?")
    ]

    response = await service.get_llm_response(
        session,
        "What are your packages and pricing?",
    )

    assert "<think>" not in response
    assert "**" not in response
    assert "#" not in response
    assert "1." not in response
    assert "2." not in response
    assert "3." not in response
    assert "outline the pricing plan first" not in response
    assert "Basic" in response
    assert "Professional" in response
    assert "Enterprise" in response


@pytest.mark.asyncio
async def test_synthesize_and_send_audio_clears_buffer_on_barge_in_without_muting():
    stt_provider = AsyncMock()
    media_gateway = AsyncMock()
    media_gateway.start_playback_tracking = MagicMock()
    tts_provider = AsyncMock()

    service = VoicePipelineService(
        stt_provider=stt_provider,
        llm_provider=AsyncMock(),
        tts_provider=tts_provider,
        media_gateway=media_gateway,
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.mark_tts_end = MagicMock()

    session = _make_session()
    session.turn_id = 3

    async def _interrupting_stream(*args, **kwargs):
        service._barge_in_events[session.call_id].set()
        yield MagicMock(data=b"\x00" * 3200)

    tts_provider.stream_synthesize = _interrupting_stream

    websocket = AsyncMock()

    await service.synthesize_and_send_audio(session, "Hello there.", websocket)

    stt_provider.mute.assert_not_awaited()
    stt_provider.unmute.assert_not_awaited()
    media_gateway.clear_output_buffer.assert_awaited_once_with(session.call_id)
    media_gateway.flush_audio_buffer.assert_not_awaited()
    websocket.send_json.assert_awaited_with({"type": "tts_interrupted", "reason": "barge_in"})


@pytest.mark.asyncio
async def test_synthesize_and_send_audio_skips_stale_reply_when_barge_in_is_pending():
    stt_provider = AsyncMock()
    media_gateway = AsyncMock()
    media_gateway.start_playback_tracking = MagicMock()
    tts_provider = AsyncMock()

    service = VoicePipelineService(
        stt_provider=stt_provider,
        llm_provider=AsyncMock(),
        tts_provider=tts_provider,
        media_gateway=media_gateway,
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.mark_tts_end = MagicMock()

    session = _make_session()
    session.turn_id = 4
    session.barge_in_event.set()

    websocket = AsyncMock()

    await service.synthesize_and_send_audio(session, "Outdated answer.", websocket)

    tts_provider.stream_synthesize.assert_not_called()
    media_gateway.clear_output_buffer.assert_awaited_once_with(session.call_id)
    media_gateway.flush_audio_buffer.assert_not_awaited()
    websocket.send_json.assert_awaited_with({"type": "tts_interrupted", "reason": "barge_in"})
    assert session.barge_in_event.is_set() is False


@pytest.mark.asyncio
async def test_handle_barge_in_cancels_active_turn_task_immediately():
    media_gateway = AsyncMock()
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=media_gateway,
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()

    session = _make_session()
    session.tts_active = True
    websocket = AsyncMock()
    cancelled = asyncio.Event()

    async def _running_turn():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    turn_task = asyncio.create_task(_running_turn())
    service._register_active_turn_task(session.call_id, turn_task)
    await asyncio.sleep(0)

    await service.handle_barge_in(session, websocket)
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    with pytest.raises(asyncio.CancelledError):
        await turn_task

    assert session.llm_active is False
    assert session.tts_active is False
    assert session.state == CallState.LISTENING
    media_gateway.clear_output_buffer.assert_awaited_once_with(session.call_id)
    sent_payload = websocket.send_json.await_args.args[0]
    assert sent_payload["type"] == "barge_in"
    assert sent_payload["message"] == "User started speaking, stopping TTS"
    assert isinstance(sent_payload["timestamp"], str)


@pytest.mark.asyncio
async def test_run_turn_commits_partial_assistant_reply_on_barge_in():
    """Barge-in with a non-empty LLM response: both user and assistant are committed
    to preserve user→assistant alternation in history (prevents consecutive user messages)."""
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
        mute_during_tts=False,
    )
    service.latency_tracker = MagicMock()
    service.latency_tracker.get_metrics.return_value = None
    service.get_llm_response = AsyncMock(return_value="Hello there.")
    service.synthesize_and_send_audio = AsyncMock(return_value=True)
    service.transcript_service = MagicMock()
    service.transcript_service.flush_to_database = AsyncMock()

    session = _make_session()
    websocket = AsyncMock()

    await service._run_turn(session, "Tell me about Talky.", websocket, turn_id=2)

    assert [message.role for message in session.conversation_history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    service.transcript_service.accumulate_turn.assert_called_once()
