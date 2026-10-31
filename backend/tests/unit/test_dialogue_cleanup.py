"""Model wording owns ordinary turns; backend authorization still owns effects."""
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import MessageRole
from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", [
    "Sorry, you've got the wrong company.",
    "Wrong number.",
    "This is a personal number, but tell me what this is about.",
])
async def test_identity_language_reaches_model_without_scripted_reply(caller):
    service = _make_service_for_disposition(["Could you tell me who you were trying to reach?"])
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = caller

    await service.handle_turn_end(session, AsyncMock())

    assert [m.content for m in session.conversation_history if m.role == MessageRole.USER] == [caller]
    assert [m.content for m in session.conversation_history if m.role == MessageRole.ASSISTANT] == [
        "Could you tell me who you were trying to reach?"
    ]
    service.media_gateway.hangup_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("spoken", ["", "I can explain the available options. "])
async def test_unauthorized_legacy_end_preserves_prose_without_retry_or_script(spoken):
    service = _make_service_for_disposition([])
    session = _make_session()
    session.campaign_id = "campaign-123"
    service._stream_llm_and_tts = AsyncMock(return_value=(
        spoken + '{"action":"end_ask_ai_session","reason":"conversation_complete","farewell":"Bye."}',
        1.0, 2.0,
    ))
    service.synthesize_and_send_audio = AsyncMock()

    result, _, _ = await service._run_turn(session, "Could you explain my options?", None, 0)

    assert result == spoken.strip()
    service._stream_llm_and_tts.assert_awaited_once()
    service.synthesize_and_send_audio.assert_not_awaited()
    service.media_gateway.hangup_call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", [True, False])
async def test_optout_result_reaches_model_before_its_continued_answer(monkeypatch, recorded):
    from app.domain.services.dialer import opt_out

    sequence = []
    model_facts = []

    async def persist(_session):
        sequence.append("persist")
        return recorded

    class Provider:
        async def stream_chat_with_timeout(self, messages, **kwargs):
            sequence.append("model")
            model_facts.extend(m.content for m in messages if m.role == MessageRole.SYSTEM)
            yield "I can explain the opening hours."

    monkeypatch.setattr(opt_out, "purge_opt_out_before_farewell", persist)
    service = _make_service_for_disposition([])
    service.llm_provider = Provider()
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.current_user_input = "Stop calling me, but first tell me your opening hours."

    await service.handle_turn_end(session, AsyncMock())

    assert sequence == ["persist", "model"]
    expected = "recorded." if recorded else "unconfirmed; removal has not been confirmed."
    assert "Caller opt-out persistence result: " + expected in model_facts
    assert session._caller_opted_out is True
    service.media_gateway.hangup_call.assert_not_awaited()
