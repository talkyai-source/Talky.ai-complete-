"""What the last lookup read stays visible for a follow-up question.

Test call f5dcac8e (2026-10-08), confirmed by replaying it offline: the agent
read the Lahore-Islamabad section for the fare (it also lists "Departures every
hour from 5:00 am to 1:00 am"), then answered "What are the timings right now?"
with "I can't confirm those". Tool results do not survive into the next turn,
and the model looked up a different section.
"""
from __future__ import annotations

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.knowledge_tool import (
    RECENT_KNOWLEDGE_MAX_CHARS, knowledge_system_addendum, remember_recent_knowledge,
)
from tests.unit.test_model_driven_voice_turn import setup_turn


async def test_a_follow_up_sees_the_section_read_on_the_previous_turn(monkeypatch):
    steps = []
    service, session, rounds = setup_turn(monkeypatch, "When is my refund?", steps)
    refund = next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == "refund")
    steps.extend([{"section_ids": [refund]}, "Approved refunds take five working days."])
    response, _, _ = await service._stream_llm_and_tts(session)
    session.conversation_history.append(Message(role=MessageRole.ASSISTANT, content=response))

    session.conversation_history.append(Message(role=MessageRole.USER, content="Is that counted from today?"))
    steps.append("From the approval, so five working days after that.")
    await service._stream_llm_and_tts(session)
    follow_up_prompt = rounds[-1][1]["system_prompt"]
    assert "recent_company_knowledge" in follow_up_prompt
    assert "five working days" in follow_up_prompt
    # Answered from what was read, without another lookup round.
    assert len(rounds) == 3


def test_it_fades_after_two_turns_without_a_lookup_and_is_replaced_by_a_new_read():
    session = type("S", (), {})()
    read = {"status": "available", "text": "Refunds take five working days.",
            "passages": [{"section_id": "k1_2", "text": "Refunds take five working days."}]}
    remember_recent_knowledge(session, read)
    assert session._recent_knowledge["section_ids"] == ["k1_2"]
    remember_recent_knowledge(session, {"status": "unavailable", "passages": []})
    assert session._recent_knowledge["turns_left"] == 1
    remember_recent_knowledge(session, {"status": "unavailable", "passages": []})
    assert session._recent_knowledge is None
    long_read = {"status": "available", "text": "x" * (RECENT_KNOWLEDGE_MAX_CHARS + 500), "passages": []}
    remember_recent_knowledge(session, long_read)
    assert len(session._recent_knowledge["text"]) == RECENT_KNOWLEDGE_MAX_CHARS


async def test_nothing_is_carried_on_the_first_turn(monkeypatch):
    steps = []
    service, session, rounds = setup_turn(monkeypatch, "Hello?", steps)
    steps.append("Hi, how can I help?")
    await service._stream_llm_and_tts(session)
    assert "recent_company_knowledge" not in rounds[0][1]["system_prompt"]
    assert "recent_company_knowledge" not in knowledge_system_addendum(session)
