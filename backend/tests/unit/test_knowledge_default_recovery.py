"""Actual default streamer recovery; model choices and audio are synthetic.

These controls establish routing and bounded execution, not semantic quality.
The unchanged original questions still need actual-model answer review.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline import turn_streamer
from app.domain.services.voice_pipeline.knowledge_tool import KB_TOOL_NAME
from app.domain.services.voice_pipeline import knowledge_tool
from app.services.scripts.knowledge import retrieval
from tests.unit.test_ag02_semantic_qualification import no_network as network_fixture
from tests.unit.test_knowledge_tool_evidence_lifecycle import _pipeline, _speak


NODE = {"id": "starter", "version": 3, "source_id": "handbook", "source_version": 2,
        "heading": "Starter monthly price", "content": "Starter costs $20 per month."}
ANSWER = "Starter costs $20 per month."


@pytest.fixture(autouse=True)
async def no_network(monkeypatch):
    await network_fixture.__wrapped__(monkeypatch)


def setup_recovery(monkeypatch, original, *, nodes=None, mode="retrieve"):
    service, session, _ = _pipeline(monkeypatch, query=original, nodes=nodes or [dict(NODE)])
    monkeypatch.delenv("VOICE_KB_MODE", raising=False)
    session.knowledge_mode = mode
    plan = {"queries": ["Starter monthly price"], "answer": ANSWER, "rounds": [],
            "messages": [], "use_tool": True}

    async def generated(messages, **kwargs):
        plan["rounds"].append(kwargs)
        plan["messages"].append([(m.role, m.content) for m in messages])
        sink = kwargs.get("tool_calls_sink")
        if sink is not None and plan["use_tool"]:
            if plan.get("preamble"):
                yield plan["preamble"]
            for index, query in enumerate(plan["queries"]):
                sink.append({"id": f"recovery-{index}", "name": KB_TOOL_NAME,
                             "arguments": {"query": query},
                             "arguments_raw": json.dumps({"query": query})})
            sink.extend(plan.get("action_calls", []))
            return
        yield plan["answer"]

    monkeypatch.setattr(service.llm_provider, "stream_chat_with_timeout", generated)
    return service, session, plan


@pytest.mark.parametrize("original,status", [
    ("What is the recurring charge for Starter?", "weak_match"),
    ("What is the recurring charge?", "no_match"),
])
async def test_default_failed_literal_search_gets_existing_model_lookup(monkeypatch, original, status):
    service, session, plan = setup_recovery(monkeypatch, original)
    await turn_streamer._knowledge_block_for_turn(session, session.conversation_history)
    assert session._knowledge_evidence["status"] == status
    assert await _speak(service, session) == [ANSWER]
    assert len(plan["rounds"]) == 2
    assert all(messages[-1] == (MessageRole.USER, original) for messages in plan["messages"])
    prompt = plan["rounds"][0]["system_prompt"]
    assert "lookup_company_knowledge" in prompt
    assert "one recovery lookup" in prompt.lower()
    assert turn_streamer.KNOWLEDGE_NO_MATCH_NOTE.strip() not in prompt
    assert turn_streamer.KNOWLEDGE_WEAK_MATCH_HEADER.strip() not in prompt
    evidence = session._knowledge_evidence
    assert evidence["status"] == "matched"
    assert evidence["passages"][0]["source_id"] == "handbook"
    assert evidence["passages"][0]["source_version"] == 2


@pytest.mark.parametrize("case", ["matched", "backchannel", "inline"])
async def test_existing_non_recovery_path_stays_one_model_round(monkeypatch, case):
    query = "Starter monthly price" if case == "matched" else "okay" if case == "backchannel" else "Recurring charge?"
    service, session, plan = setup_recovery(monkeypatch, query, mode="inline" if case == "inline" else "retrieve")
    if case == "inline":
        session.system_prompt += f"\n<company_knowledge>{ANSWER}</company_knowledge>"
    elif case == "backchannel":
        plan["answer"] = "Thank you."
    assert await _speak(service, session) == [plan["answer"]]
    assert len(plan["rounds"]) == 1
    assert "tools" not in plan["rounds"][0]


async def test_recovery_withholds_prelookup_claim_before_speech(monkeypatch):
    service, session, plan = setup_recovery(monkeypatch, "What is the recurring charge for Starter?")
    plan["preamble"] = "Starter costs $999 per month. "
    assert await _speak(service, session) == [ANSWER]
    assert len(plan["rounds"]) == 2
    assert "$999" not in " ".join(session._spoken_sentences)


async def test_wrong_intent_rewrite_is_not_original_question_proof(monkeypatch):
    original = "What is the charge for Pulse in Farport, not Starter?"
    service, session, plan = setup_recovery(monkeypatch, original)
    # This intentionally wrong synthetic query CAN match a source. That does
    # not qualify an answer to Pulse/Farport or remove the original negation.
    await _speak(service, session)
    assert len(plan["rounds"]) == 2
    assert session._knowledge_evidence["status"] == "matched"
    assert "Pulse" not in session._knowledge_evidence["text"]
    assert all(messages[-1] == (MessageRole.USER, original) for messages in plan["messages"])
    prompt = plan["rounds"][0]["system_prompt"]
    assert "named products, locations, timing, negation" in prompt
    assert "source actually answers the original question" in prompt
    assert "semantic_quality_pass" not in session._knowledge_evidence


async def test_original_prior_caller_context_survives_recovery(monkeypatch):
    service, session, plan = setup_recovery(monkeypatch, "What is the recurring charge?")
    session.conversation_history.insert(0, Message(role=MessageRole.USER, content="I mean Starter, not Pulse."))
    await _speak(service, session)
    assert len(plan["rounds"]) == 2
    assert all(messages[0] == (MessageRole.USER, "I mean Starter, not Pulse.") for messages in plan["messages"])


@pytest.mark.parametrize("failure", ["missing_container", "error", "timeout"])
async def test_unavailable_initial_lookup_does_not_start_model_recovery(monkeypatch, failure):
    service, session, plan = setup_recovery(monkeypatch, "What is the recurring charge?")
    session._knowledge_snapshot_nodes = None
    monkeypatch.setattr("app.core.container.get_container", lambda: SimpleNamespace(
        is_initialized=failure != "missing_container", db_client=SimpleNamespace(pool=object())))
    lookup = AsyncMock(side_effect=TimeoutError() if failure == "timeout" else RuntimeError("synthetic failure"))
    monkeypatch.setattr(retrieval, "retrieve_knowledge", lookup)
    await _speak(service, session)
    assert len(plan["rounds"]) == 1
    assert "tools" not in plan["rounds"][0]
    assert session._knowledge_evidence["status"] == "unavailable"
    assert lookup.await_count == (0 if failure == "missing_container" else 1)


@pytest.mark.parametrize("case", ["unsupported", "none"])
async def test_ineligible_session_or_provider_keeps_plain_path(monkeypatch, case):
    service, session, plan = setup_recovery(monkeypatch, "What is the recurring charge?")
    if case == "unsupported":
        monkeypatch.setattr(type(service.llm_provider), "supports_tools", False)
    else:
        session.knowledge_mode = "none"
    await _speak(service, session)
    assert len(plan["rounds"]) == 1
    assert "tools" not in plan["rounds"][0]


@pytest.mark.parametrize("status,query", [
    ("no_match", "quasar nebula"),
    ("weak_match", "Starter international warranty insurance"),
    ("unavailable", "Starter monthly price"),
])
async def test_failed_recovery_revokes_prior_facts(monkeypatch, status, query):
    service, session, plan = setup_recovery(monkeypatch, "What is the recurring charge?")
    session._knowledge_grounding = [ANSWER]
    session._knowledge_evidence = {"status": "matched", "passages": [{"text": ANSWER}]}
    plan["queries"] = [query]
    if status == "unavailable":
        actual = retrieval.retrieve_pinned_knowledge
        count = 0

        def failing_after_raw(*args, **kwargs):
            nonlocal count
            count += 1
            if count > 1:
                raise RuntimeError("synthetic recovery failure")
            return actual(*args, **kwargs)

        monkeypatch.setattr(retrieval, "retrieve_pinned_knowledge", failing_after_raw)
    speech = await _speak(service, session)
    assert len(plan["rounds"]) == 2
    assert speech == ["I can't confirm that figure from the information available."]
    assert session._knowledge_evidence["status"] == status
    assert session._knowledge_grounding == []
    assert session._live_structured_state.last_tool_success is False


@pytest.mark.parametrize("second_query", ["Enterprise price", "Starter monthly price"])
async def test_only_one_actual_recovery_lookup_per_turn(monkeypatch, second_query):
    service, session, plan = setup_recovery(monkeypatch, "What is the recurring charge?")
    actual = retrieval.retrieve_pinned_knowledge
    queries = []

    def counted(nodes, query, **kwargs):
        queries.append(query)
        return actual(nodes, query, **kwargs)

    monkeypatch.setattr(retrieval, "retrieve_pinned_knowledge", counted)
    plan["queries"] = ["Starter monthly price", second_query]
    await _speak(service, session)
    assert queries == ["What is the recurring charge?", "Starter monthly price"]
    results = [m["content"] for m in plan["rounds"][1]["extra_messages"] if m["role"] == "tool"]
    assert len(results) == 2 and "$20" in results[0]
    if second_query == "Starter monthly price":
        assert results[0] == results[1]
        assert session._live_structured_state.last_tool_success is True
    else:
        assert "No additional knowledge lookup was performed" in results[1]
        assert session._knowledge_evidence == {"status": "unavailable", "passages": [], "reason": "recovery_limit"}
        assert session._knowledge_grounding == []
        assert session._live_structured_state.last_tool_success is False
        assert session._live_structured_state.last_tool_code == "unavailable"
        assert "$20" not in " ".join(session._spoken_sentences)


async def test_shared_recovery_helper_reuses_copied_evidence(monkeypatch):
    service, session, _ = setup_recovery(monkeypatch, "What is the recurring charge?")
    await turn_streamer._knowledge_block_for_turn(session, session.conversation_history)
    recovery = knowledge_tool.knowledge_recovery_for(session, service.llm_provider)
    result = await recovery.lookup("Starter monthly price")
    session._knowledge_evidence["passages"][0]["text"] = "Corrupted caller-owned evidence"
    session._knowledge_grounding.append("Corrupted grounding")
    assert await recovery.lookup(" Starter monthly price ") == result
    assert session._knowledge_evidence["passages"][0]["text"].endswith(ANSWER)
    assert session._knowledge_grounding == [session._knowledge_evidence["passages"][0]["text"]]


async def test_recovery_limit_is_per_turn_not_per_call(monkeypatch):
    service, session, plan = setup_recovery(monkeypatch, "What is the recurring charge?")
    assert await _speak(service, session) == [ANSWER]
    plan["rounds"].clear()
    session.conversation_history.append(Message(role=MessageRole.USER, content="What is the recurring charge again?"))
    assert await _speak(service, session) == [ANSWER]
    assert len(plan["rounds"]) == 2


async def test_recovery_keeps_authorized_action_tool_and_dispatch(monkeypatch):
    service, session, plan = setup_recovery(monkeypatch, "Email me the recurring charge details.")
    session._voice_action_capabilities = {"send_email": "Synthetic existing capability"}
    plan["action_calls"] = [{"id": "action-1", "name": "send_email", "arguments": {"purpose": "details"},
                             "arguments_raw": '{"purpose":"details"}'}]
    action = AsyncMock(return_value={"action": "send_email", "success": False,
                                    "confirmation_allowed": False, "status": "needs_confirmation"})
    monkeypatch.setattr(turn_streamer, "run_voice_action", action)
    await _speak(service, session)
    names = [spec["function"]["name"] for spec in plan["rounds"][0]["tools"]]
    assert names == [KB_TOOL_NAME, "send_email"]
    action.assert_awaited_once()
    assert action.await_args.args[1:3] == ("send_email", {"purpose": "details"})
    assert action.await_args.kwargs["user_text"] == "Email me the recurring charge details."


async def test_ambiguous_recovery_can_clarify_without_lookup(monkeypatch):
    service, session, plan = setup_recovery(monkeypatch, "What is the recurring charge?")
    plan.update(use_tool=False, answer="Which plan do you mean?")
    assert await _speak(service, session) == ["Which plan do you mean?"]
    assert len(plan["rounds"]) == 1
    assert session._knowledge_grounding == []
    assert session._knowledge_evidence["status"] == "no_match"


async def test_existing_topic_map_remains_navigation_only_during_recovery(monkeypatch):
    service, session, plan = setup_recovery(monkeypatch, "What is the recurring charge?", mode="map_retrieve")
    from app.services.scripts.knowledge.session_inject import _MAP_HEADER, _bake_inline_knowledge
    assert _bake_inline_knowledge(session, "Starter monthly price — subscription topic", _MAP_HEADER, session.campaign_id, "map_retrieve")
    await _speak(service, session)
    prompt = plan["rounds"][0]["system_prompt"]
    assert "topic hints, not verified answers" in prompt
    assert "topic hints only for navigation" in prompt
    assert "subscription topic" in prompt
    assert "subscription topic" not in " ".join(session._knowledge_grounding)


async def test_default_matched_source_does_not_claim_original_intent_verified(monkeypatch):
    original = "Not Starter: what is the Starter price for Pulse?"
    service, session, plan = setup_recovery(monkeypatch, original)
    await _speak(service, session)
    assert len(plan["rounds"]) == 1
    assert session._knowledge_evidence["status"] == "matched"
    prompt = plan["rounds"][0]["system_prompt"]
    assert "official answers for this caller's question" not in prompt
    assert "retrieved source passages" in prompt
    assert "A search match alone is not an answer" in prompt
    assert "named products, locations, timing, negation, relationships and conditions" in prompt
    assert plan["messages"][0][-1] == (MessageRole.USER, original)
