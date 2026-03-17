from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.models.conversation import Message, MessageRole
from app.domain.models.conversation_state import ConversationContext, ConversationState
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService


def _make_session() -> CallSession:
    return CallSession(
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
