"""The model's own introduction of the agent says it is an AI (23 Nov 2026).

Driven through the real streaming turn: what is handed to speech, the last
assistant entry of the conversation history and the content given to the
transcript all carry the same amended text.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import MessageRole
from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session

_AI = ", an AI assistant from"


async def _run(chunks, *, name="grace", direction=None):
    service = _make_service_for_disposition(chunks)
    service.transcript_service = MagicMock()
    service.transcript_service.flush_to_database = AsyncMock()
    session = _make_session()
    session.call_id = "7ad487e3-52a6-4b0e-9d0b-6f1c2a8e9b11"
    session.agent_config.agent_name = name
    session.agent_config.company_name = "All State Estimation"
    if direction:
        session._call_direction = direction
    service._barge_in_events[session.call_id] = session.barge_in_event
    submitted = []

    async def synthesize(_session, text, *_args, **_kwargs):
        submitted.append(text)
        return False

    service.synthesize_and_send_audio = synthesize
    await service._run_turn(session, "Hello?", AsyncMock(), turn_id=2)
    assistant = [m for m in session.conversation_history if m.role == MessageRole.ASSISTANT]
    transcript = service.transcript_service.accumulate_turn.call_args.kwargs["content"]
    return submitted, assistant[-1].content, transcript


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", [None, "inbound", "outbound"])
async def test_a_model_introduction_cut_across_chunks_says_it_is_an_ai(direction):
    chunks = ["Hi there, this is Gr", "ace from All State", " Estimation. Who am I speaking with?"]
    submitted, history, transcript = await _run(chunks, direction=direction)
    assert submitted[0] == "Hi there, this is Grace, an AI assistant from All State Estimation."
    assert _AI in " ".join(submitted)
    assert _AI in history
    assert _AI in transcript


@pytest.mark.asyncio
async def test_a_second_sentence_that_introduces_the_agent_is_amended():
    submitted, history, transcript = await _run(["Hi there. ", "This is Grace from All State Estimation."])
    assert submitted == ["Hi there.", "This is Grace, an AI assistant from All State Estimation."]
    assert _AI in history and _AI in transcript


@pytest.mark.asyncio
async def test_a_reply_that_already_said_it_is_an_ai_is_left_alone():
    submitted, history, transcript = await _run(
        ["I'm an AI assistant. ", "This is Grace from All State Estimation."])
    assert submitted == ["I'm an AI assistant.", "This is Grace from All State Estimation."]
    assert _AI not in history and _AI not in transcript


@pytest.mark.asyncio
async def test_a_caller_who_shares_the_agents_name_is_spoken_unchanged():
    reply = "Got it, it's Grace. And what's the best email for you?"
    submitted, history, transcript = await _run([reply])
    assert submitted == ["Got it, it's Grace.", "And what's the best email for you?"]
    assert history == reply and transcript == reply


@pytest.mark.asyncio
async def test_an_earlier_product_mention_does_not_stop_the_introduction_being_amended():
    submitted, history, transcript = await _run(
        ["We use an AI takeoff tool. ", "This is Grace from All State Estimation."])
    assert submitted == ["We use an AI takeoff tool.",
                         "This is Grace, an AI assistant from All State Estimation."]
    assert _AI in history and _AI in transcript
