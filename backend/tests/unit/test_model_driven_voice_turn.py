"""Live wiring checks with scripted model output, not model-quality claims."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.domain.services.voice_pipeline.knowledge_tool import KB_TOOL_NAME
from app.infrastructure.llm.groq import GroqLLMProvider
from app.services.scripts.knowledge.sections import build_section_catalog


def setup_turn(monkeypatch, question, steps):
    provider = GroqLLMProvider()
    rounds = []

    async def model(messages, **kwargs):
        rounds.append((messages, kwargs))
        step = steps[len(rounds) - 1]
        if isinstance(step, tuple):
            yield step[0]
            step = step[1]
        if isinstance(step, dict):
            kwargs["tool_calls_sink"].append({
                "id": f"call-{len(rounds)}", "name": KB_TOOL_NAME,
                "arguments": step, "arguments_raw": json.dumps(step),
            })
        else:
            yield step

    monkeypatch.setattr(provider, "stream_chat_with_timeout", model)
    service = VoicePipelineService(stt_provider=AsyncMock(), llm_provider=provider,
                                   tts_provider=AsyncMock(), media_gateway=AsyncMock())
    service.latency_tracker = MagicMock()
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = CallSession(call_id="model-driven-test", tenant_id="t1", campaign_id="c1", lead_id="l1",
        provider_call_id="synthetic", voice_id="synthetic", system_prompt="Help the caller accurately.",
        knowledge_mode="retrieve", conversation_history=[Message(role=MessageRole.USER, content=question)])
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {}
    session._knowledge_catalog = build_section_catalog([
        {"id": "root", "source_id": "handbook", "source_version": 1, "version": 1,
         "heading": "Canada", "content": "All prices are CAD, excluding tax.", "path": "1", "depth": 1},
        {"id": "refund", "source_id": "handbook", "source_version": 1, "version": 1,
         "heading": "Refunds", "content": "Approved refunds take five working days.",
         "path": "1.1", "depth": 2, "parent_id": "root"},
    ], tenant_id="t1", campaign_id="c1", source_policy="call_snapshot")
    return service, session, rounds


async def test_model_selects_section_without_backend_question_matching(monkeypatch):
    steps = []
    question = "When will my money come back?"
    service, session, rounds = setup_turn(monkeypatch, question, steps)
    selected = next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == "refund")
    steps.extend([{"section_ids": [selected]}, "Approved refunds take five working days."])
    from app.services.scripts.knowledge import retrieval
    monkeypatch.setattr(retrieval, "retrieve_knowledge", AsyncMock(side_effect=AssertionError("No lexical search")))
    monkeypatch.setattr(retrieval, "retrieve_pinned_knowledge", MagicMock(side_effect=AssertionError("No lexical search")))
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == steps[-1]
    assert len(rounds) == 2
    assert all(messages[-1].content == question for messages, _ in rounds)
    evidence = session._knowledge_evidence
    assert evidence["status"] == "available"
    assert "CAD, excluding tax" in evidence["text"]
    assert "five working days" in evidence["text"]
    assert all("coverage" not in p for p in evidence["passages"])


@pytest.mark.parametrize("answer", [
    "The price is $19 (excluding VAT).",
    "That phone number sounds incomplete. Which digit follows the seven?",
    "You're welcome. Goodbye.",
])
async def test_model_wording_and_qualifiers_reach_speech_without_rule_rewrite(monkeypatch, answer):
    service, session, rounds = setup_turn(monkeypatch, "Thanks for your help.", [answer])
    # Qualifiers pass unrewritten; the price itself must be sourced (HAL-2,
    # test_figure_grounding.py covers the unsourced case).
    session.system_prompt += " Approved price: $19 excluding VAT."
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == answer
    assert len(rounds) == 1
    assert "Before anything else" not in rounds[0][1]["system_prompt"]
    assert not hasattr(session, "_filler_said_this_turn")


async def test_available_tools_are_not_hidden_by_keyword_guessing(monkeypatch):
    service, session, rounds = setup_turn(monkeypatch, "Could you get those over to me?", ["Which address should I use?"])
    session._voice_action_capabilities = {"send_email": "Send campaign information"}
    await service._stream_llm_and_tts(session)
    names = [spec["function"]["name"] for spec in rounds[0][1]["tools"]]
    assert "send_email" in names
    assert "transfer_call" not in names


async def test_complete_model_answer_is_not_cut_at_a_sentence_quota(monkeypatch):
    answer = "First detail. Second detail. Third detail. Fourth detail. Fifth detail. What else would help?"
    service, session, _ = setup_turn(monkeypatch, "Please explain the details.", [answer])
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == answer
    assert " ".join(session._spoken_sentences) == answer


@pytest.mark.parametrize("question", ["Can you help our shop?", "What would that mean for us?", "Hello."])
async def test_demo_facts_are_available_without_a_keyword_gate(monkeypatch, question):
    from app.domain.services.ask_ai_constants import TALKY_PRODUCT_INFO
    service, session, rounds = setup_turn(monkeypatch, question, ["What would you like to know?"])
    session.campaign_id = "ask-ai"
    await service._stream_llm_and_tts(session)
    assert TALKY_PRODUCT_INFO in rounds[0][1]["system_prompt"]
    assert rounds[0][0][-1].content == question


async def test_read_catalog_read_again_keeps_current_source_evidence(monkeypatch):
    steps = []
    service, session, rounds = setup_turn(monkeypatch, "When is my refund?", steps)
    ref = next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == "refund")
    steps.extend([{"section_ids": [ref]}, {"catalog_offset": 0},
                  {"section_ids": [ref]}, "Approved refunds take five working days."])
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == steps[-1] and len(rounds) == 4
    assert session._knowledge_evidence["status"] == "available"
    assert session._knowledge_grounding
    assert session._live_structured_state.last_tool_code == "available"


async def test_natural_preamble_can_stream_before_source_and_answer_follows_it(monkeypatch):
    steps = []
    service, session, _ = setup_turn(monkeypatch, "When is my refund?", steps)
    ref = next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == "refund")
    steps.extend([("Let me check that. ", {"section_ids": [ref]}), "Refunds take five working days."])
    delivered = []
    async def speak(_session, sentence, *_args, **_kwargs):
        delivered.append((sentence, session._knowledge_evidence["status"]))
        return False
    service.synthesize_and_send_audio = speak
    response, _, _ = await service._stream_llm_and_tts(session)
    assert delivered == [("Let me check that.", "unavailable"), ("Refunds take five working days.", "available")]
    assert response == "Let me check that. Refunds take five working days."


async def test_native_adapter_reads_same_scoped_sections_and_fences_body_once(monkeypatch):
    from app.realtime.bridge import RealtimeBridge
    _, session, _ = setup_turn(monkeypatch, "How long for a refund?", [])
    rt = type("RT", (), {"update_live_state": AsyncMock(), "send_function_result": AsyncMock()})()
    bridge = RealtimeBridge(call_id="native-sections", realtime_session=rt,
        media_gateway=object(), tenant_id="t1", campaign_id="c1")
    bridge._knowledge_catalog = session._knowledge_catalog
    ref = next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == "refund")
    arguments = {"section_ids": [ref]}
    call = type("Call", (), {"name": "knowledge_lookup", "call_id": "tool-1",
                           "parsed_arguments": lambda self: arguments})()
    await bridge._handle_function_call(call)
    output = rt.send_function_result.call_args.args[1]
    assert output["status"] == "available"
    assert output["text"].count("Approved refunds take five working days.") == 1
    assert "CAD, excluding tax" in output["text"]
    assert output["source_policy"] == "call_snapshot"
    assert "knowledge_lookup:succeeded:available" in rt.update_live_state.call_args.args[0]
    assert len(output["sources"]) == 2

    # Pagination is navigation, never a new factual result; wrong scope is refused.
    page = await bridge._lookup_knowledge({"catalog_offset": 0})
    assert page["status"] == "catalog" and not bridge._verified_knowledge
    bridge._campaign_id = "other-campaign"
    denied = await bridge._lookup_knowledge(arguments)
    assert denied["status"] == "unavailable"
    assert not denied["sources"] and not bridge._verified_knowledge
