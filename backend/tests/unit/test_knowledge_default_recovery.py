"""Replacement contract for the retired lexical default/reformulation fallback.

Section selection is the primary model tool path in every enabled saved mode.
No scripted model output here constitutes answer-quality qualification.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.voice_pipeline import turn_streamer
from app.domain.services.voice_pipeline.knowledge_tool import KB_TOOL_NAME
from app.domain.models.conversation import Message, MessageRole
from tests.unit.test_model_driven_voice_turn import setup_turn


@pytest.mark.parametrize("mode", ["inline", "map_retrieve", "retrieve"])
@pytest.mark.parametrize("flag", [None, "inject", "tool"])
async def test_model_section_selection_is_primary_without_literal_search(monkeypatch, mode, flag):
    if flag is None:
        monkeypatch.delenv("VOICE_KB_MODE", raising=False)
    else:
        monkeypatch.setenv("VOICE_KB_MODE", flag)
    live = AsyncMock(side_effect=AssertionError("No live lexical search"))
    pinned = MagicMock(side_effect=AssertionError("No snapshot keyword search"))
    monkeypatch.setattr("app.services.scripts.knowledge.retrieval.retrieve_knowledge", live)
    monkeypatch.setattr("app.services.scripts.knowledge.retrieval.retrieve_pinned_knowledge", pinned)
    steps = []
    question = "When will my money come back?"
    service, session, rounds = setup_turn(monkeypatch, question, steps)
    session.knowledge_mode = mode
    ref = next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == "refund")
    steps.extend([{"section_ids": [ref]}, "Approved refunds take five working days."])
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == steps[-1] and len(rounds) == 2
    assert KB_TOOL_NAME in [tool["function"]["name"] for tool in rounds[0][1]["tools"]]
    assert all(messages[-1].content == question for messages, _ in rounds)
    assert session._knowledge_evidence["status"] == "available"
    live.assert_not_awaited()
    pinned.assert_not_called()


async def test_model_can_clarify_without_reading_or_claiming_evidence(monkeypatch):
    service, session, rounds = setup_turn(monkeypatch, "What about that one?", ["Which product do you mean?"])
    session.conversation_history.insert(0, Message(role=MessageRole.USER, content="Tell me about the Canadian plan."))
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == "Which product do you mean?" and len(rounds) == 1
    assert "Canadian plan" in rounds[0][0][0].content
    assert session._knowledge_evidence == {"status": "unavailable", "passages": []}


async def test_section_tools_keep_existing_action_dispatch_boundary(monkeypatch):
    question = "Email me those details."
    service, session, rounds = setup_turn(monkeypatch, question, [])
    session._voice_action_capabilities = {"send_email": "Existing capability"}
    calls = []
    async def model(messages, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            kwargs["tool_calls_sink"].append({"id": "email-1", "name": "send_email",
                "arguments": {"purpose": "details"}, "arguments_raw": '{"purpose":"details"}'})
        else:
            yield "Please confirm the recipient."
    monkeypatch.setattr(service.llm_provider, "stream_chat_with_timeout", model)
    action = AsyncMock(return_value={"action": "send_email", "success": False,
                                    "confirmation_allowed": False, "status": "needs_confirmation"})
    monkeypatch.setattr(turn_streamer, "run_voice_action", action)
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == "Please confirm the recipient." and len(calls) == 2
    names = [tool["function"]["name"] for tool in calls[0]["tools"]]
    assert KB_TOOL_NAME in names and "send_email" in names
    action.assert_awaited_once()
    assert action.await_args.args[1:3] == ("send_email", {"purpose": "details"})
    assert action.await_args.kwargs["user_text"] == question
    assert session._live_structured_state.last_tool_code == "needs_confirmation"
    assert session._live_structured_state.last_tool_success is False
