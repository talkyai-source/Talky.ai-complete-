"""Current-turn section evidence through the actual streamer and Groq tool loop.

The model and audio are scripted. Status is read availability, not semantic
answer correctness; the model owns its wording and may stream natural preambles.
"""
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.services.scripts.knowledge.sections import build_section_catalog
from tests.unit.test_model_driven_voice_turn import setup_turn


def _ref(session, node_id="refund"):
    return next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == node_id)


@pytest.mark.parametrize("second,status", [
    ({"catalog_offset": 0}, "catalog"),
    ({"section_ids": ["unknown"]}, "unavailable"),
    ({"section_ids": []}, "unavailable"),
    ("other", "available"),
    ("oversize", "too_large"),
])
async def test_latest_read_replaces_prior_factual_evidence_in_same_turn(monkeypatch, second, status):
    steps = []
    service, session, rounds = setup_turn(monkeypatch, "When is my refund?", steps)
    rows = [dict(row) for row in session._knowledge_catalog.nodes]
    rows += [
        {"id": "other", "source_id": "other", "source_version": 1, "version": 1,
         "heading": "Billing", "content": "Invoices are issued on Friday."},
        {"id": "oversize", "source_id": "other", "source_version": 1, "version": 1,
         "heading": "Long terms", "content": "Terms. " * 2000},
    ]
    session._knowledge_catalog = build_section_catalog(rows, tenant_id="t1", campaign_id="c1",
                                                       source_policy="call_snapshot")
    arguments = {"section_ids": [_ref(session, second)]} if isinstance(second, str) else second
    steps.extend([{"section_ids": [_ref(session)]}, arguments, "Which detail do you need?"])
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == steps[-1] and len(rounds) == 3
    assert "five working days" in rounds[1][1]["extra_messages"][1]["content"]
    evidence = session._knowledge_evidence
    assert evidence["status"] == status
    assert "five working days" not in " ".join(session._knowledge_grounding)
    if status == "available":
        assert "Invoices are issued on Friday" in evidence["text"]
        assert session._knowledge_grounding
    else:
        assert evidence["passages"] == [] and session._knowledge_grounding == []
    state = session._live_structured_state
    assert state.last_tool_code == status
    assert state.last_tool_success is (status in {"available", "catalog"})


@pytest.mark.parametrize("fresh_read", [False, True])
async def test_prior_turn_read_is_not_reported_as_current_evidence(monkeypatch, fresh_read):
    steps = []
    service, session, rounds = setup_turn(monkeypatch, "When is my refund?", steps)
    read = {"section_ids": [_ref(session)]}
    steps.extend([read, "Refunds take five working days."])
    await service._stream_llm_and_tts(session)
    assert session._knowledge_evidence["status"] == "available"
    session.conversation_history.append(Message(role=MessageRole.USER, content="Please confirm that again."))
    steps.extend(([read] if fresh_read else []) + ["Let me explain."])
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == "Let me explain."
    assert len(rounds) == (4 if fresh_read else 3)
    assert session._knowledge_evidence["status"] == ("available" if fresh_read else "unavailable")
    assert bool(session._knowledge_grounding) is fresh_read
    if not fresh_read:
        assert session._knowledge_evidence["passages"] == []


@pytest.mark.parametrize("operation,status,has_facts", [
    ("read", "available", True),
    ({"catalog_offset": 0}, "catalog", False),
    ({"query": "When is my refund?"}, "unavailable", False),
    ({"section_ids": ["unknown"]}, "unavailable", False),
])
async def test_tool_state_distinguishes_navigation_read_and_failure(monkeypatch, operation, status, has_facts):
    steps = []
    service, session, rounds = setup_turn(monkeypatch, "When is my refund?", steps)
    arguments = {"section_ids": [_ref(session)]} if operation == "read" else operation
    steps.extend([arguments, "I will explain what is available."])
    await service._stream_llm_and_tts(session)
    assert len(rounds) == 2
    assert session._knowledge_evidence["status"] == status
    assert bool(session._knowledge_grounding) is has_facts
    state = session._live_structured_state
    assert state.last_tool_name == "knowledge_lookup"
    assert state.last_tool_code == status
    assert state.last_tool_success is (status in {"available", "catalog"})


async def test_no_tool_smalltalk_clears_evidence_without_changing_reply(monkeypatch):
    steps = []
    service, session, _ = setup_turn(monkeypatch, "When is my refund?", steps)
    steps.extend([{"section_ids": [_ref(session)]}, "Refunds take five working days."])
    await service._stream_llm_and_tts(session)
    session.conversation_history.append(Message(role=MessageRole.USER, content="Hello there."))
    steps.append("Hello there.")
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == "Hello there."
    assert session._knowledge_evidence == {"status": "unavailable", "passages": []}
    assert session._knowledge_grounding == []
