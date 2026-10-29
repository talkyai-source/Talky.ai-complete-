"""What the last lookup read stays visible for a follow-up question.

Test call f5dcac8e (2026-10-08), confirmed by replaying it offline: the agent
read the Lahore-Islamabad section for the fare (it also lists "Departures every
hour from 5:00 am to 1:00 am"), then answered "What are the timings right now?"
with "I can't confirm those". Tool results do not survive into the next turn,
and the model looked up a different section.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.knowledge_tool import (
    RECENT_KNOWLEDGE_MAX_CHARS, knowledge_system_addendum, remember_recent_knowledge,
    run_knowledge_lookup,
)
from app.services.scripts.knowledge.sections import build_section_catalog
from tests.unit.test_model_driven_voice_turn import setup_turn


def _session(text="Refunds take five working days."):
    catalog = build_section_catalog([
        {"id": "refund", "source_id": "policy", "source_version": 1, "version": "1",
         "heading": "Refunds", "content": text},
    ], tenant_id="t1", campaign_id="c1", source_policy="call_snapshot")
    return SimpleNamespace(tenant_id="t1", campaign_id="c1", call_id="test",
                           _knowledge_catalog=catalog)


async def _read_and_remember(session):
    selected = session._knowledge_catalog.nodes[0]["section_id"]
    await run_knowledge_lookup(session, {"section_ids": [selected]})
    assert session._knowledge_evidence["status"] == "available"
    remember_recent_knowledge(session, session._knowledge_evidence)
    return selected


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


async def test_it_fades_after_two_turns_without_a_lookup_and_is_replaced_by_a_new_read():
    session = _session()
    selected = await _read_and_remember(session)
    assert session._recent_knowledge["section_ids"] == [selected]
    remember_recent_knowledge(session, {"status": "unavailable", "passages": []})
    assert session._recent_knowledge["turns_left"] == 1
    remember_recent_knowledge(session, {"status": "unavailable", "passages": []})
    assert session._recent_knowledge is None
    await _read_and_remember(session)
    assert session._recent_knowledge["turns_left"] == 2


async def test_large_source_never_carries_a_policy_prefix_without_its_exclusions():
    policy = "Refunds are available. " + "Details. " * 300 + "Refunds exclude prepaid annual plans."
    assert len(policy) > RECENT_KNOWLEDGE_MAX_CHARS
    session = _session(policy)
    selected = await _read_and_remember(session)
    prompt = knowledge_system_addendum(session)

    assert session._recent_knowledge["text"] == ""
    assert session._recent_knowledge["reread_required"] is True
    assert selected in prompt and "read the needed source again" in prompt
    assert "Refunds are available." not in prompt
    assert "Refunds exclude prepaid annual plans." not in prompt
    # The snapshot still serves the complete policy, including its conditions.
    await run_knowledge_lookup(session, {"section_ids": [selected]})
    assert policy in session._knowledge_evidence["text"]


@pytest.mark.parametrize("change", ["tenant", "campaign", "source_revision"])
async def test_memory_and_previous_read_do_not_cross_scope_or_snapshot_changes(change):
    session = _session("Confidential policy from the old snapshot.")
    await _read_and_remember(session)
    old_evidence = session._knowledge_evidence
    if change == "tenant":
        session.tenant_id = "t2"
    elif change == "campaign":
        session.campaign_id = "c2"
    else:
        row = dict(session._knowledge_catalog.nodes[0], version="2", source_version=2,
                   content="Replacement policy.")
        session._knowledge_catalog = build_section_catalog([row], tenant_id="t1", campaign_id="c1",
                                                           source_policy="call_snapshot")
    assert "Confidential policy" not in knowledge_system_addendum(session)
    # Resetting current-turn evidence must not relabel an old read as new facts.
    remember_recent_knowledge(session, old_evidence)
    assert session._recent_knowledge is None


@pytest.mark.parametrize("argument", ["section_ids", "catalog_parent", "catalog_offset", "source_offset"])
async def test_lookup_logs_reject_private_model_arguments(caplog, argument):
    from app.core.log_redact import install_pii_log_redaction

    install_pii_log_redaction()
    private = "My private health concern is cobalt syndrome"
    arguments = {argument: [private] if argument == "section_ids" else private}
    if argument == "source_offset":
        arguments["section_ids"] = [private]
    session = _session()
    with caplog.at_level("INFO", logger="app.domain.services.voice_pipeline.knowledge_tool"):
        await run_knowledge_lookup(session, arguments)
    assert private not in caplog.text
    assert "knowledge_lookup" in caplog.text and "reason=" in caplog.text
    assert session._knowledge_evidence["status"] == "unavailable"


async def test_lookup_diagnostics_preserve_valid_scoped_navigation(caplog):
    session = _session()
    selected = session._knowledge_catalog.nodes[0]["section_id"]
    with caplog.at_level("INFO", logger="app.domain.services.voice_pipeline.knowledge_tool"):
        await run_knowledge_lookup(session, {"section_ids": [selected]})
    assert selected in caplog.text and "status=available" in caplog.text


async def test_nothing_is_carried_on_the_first_turn(monkeypatch):
    steps = []
    service, session, rounds = setup_turn(monkeypatch, "Hello?", steps)
    steps.append("Hi, how can I help?")
    await service._stream_llm_and_tts(session)
    assert "recent_company_knowledge" not in rounds[0][1]["system_prompt"]
    assert "recent_company_knowledge" not in knowledge_system_addendum(session)
