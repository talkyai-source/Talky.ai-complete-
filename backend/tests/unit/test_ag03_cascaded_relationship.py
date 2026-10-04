"""Traditional speech admission uses the same bounded relationship evidence."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService


@pytest.mark.asyncio
@pytest.mark.parametrize("latest,expected_block", [
    ("What would signing up involve?", True),
    ('The script says "I am your customer".', True),
    ("Actually, I am your customer.", False),
])
async def test_prior_explicit_relationship_survives_pruned_cascaded_history(monkeypatch, latest, expected_block):
    from app.domain.services.voice_pipeline.live_structured_state import (
        LiveConversationState, evidence_from_transcript, reduce_live_state,
    )

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    state = reduce_live_state(LiveConversationState(), evidence_from_transcript(
        role="user", text="I am not your customer.", turn_id="caller-1",
    ))
    for turn in range(2, 32):
        state = reduce_live_state(state, evidence_from_transcript(
            role="user", text="Tell me about your services.", turn_id=f"caller-{turn}",
        ))

    class GeneratedText:
        supports_tools = False

        async def stream_chat_with_timeout(self, *args, **kwargs):
            yield "You are an existing customer."

    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=GeneratedText(),
        tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = CallSession(
        call_id="ag03-cascaded", tenant_id="tenant-a", campaign_id="campaign-a",
        lead_id="synthetic-lead", provider_call_id="synthetic-provider", voice_id="synthetic-voice",
        system_prompt="Audience: existing customers. Respect caller corrections.",
        conversation_history=[Message(role=MessageRole.USER, content=latest)],
    )
    session.turn_id = 32
    session._live_structured_state = state
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {}
    response, _, _ = await service._stream_llm_and_tts(session)
    submitted = [call.args[1] for call in service.synthesize_and_send_audio.await_args_list]
    if expected_block:
        assert "You are an existing customer." not in submitted
        assert submitted == ["Thanks for correcting me. I won't assume you're a customer."]
    else:
        assert submitted == ["You are an existing customer."]
    assert response == " ".join(submitted)
    assert session._spoken_sentences == submitted
