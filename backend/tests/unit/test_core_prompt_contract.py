"""Audited prompt conflicts: evidence and direction beat campaign assumptions."""
from enum import Enum

import pytest

from app.domain.services.voice_pipeline.conversation_craft import craft_reanchor
from app.services.scripts.prompts.composer import compose_prompt_document
from app.services.scripts.prompts.live_state import build_live_state_block


def _document(**kwargs):
    return compose_prompt_document(
        "lead_gen", "Ava", "Northwind Systems", {}, knowledge_driven=True, **kwargs,
    )


def test_default_outbound_prompt_stays_compact_without_duplicate_turn_shape():
    document = _document()
    assert len(document.system_prompt.split()) <= 1800
    assert document.system_prompt.count("Answer in the fewest sentences") == 1
    assert document.system_prompt == "\n\n".join(layer.content for layer in document.layers)


@pytest.mark.parametrize("direction", ["outbound", "inbound"])
@pytest.mark.parametrize("opening_mode", ["agent_first", "callee_first"])
def test_corrections_and_action_evidence_survive_all_direction_variants(direction, opening_mode):
    document = _document(direction=direction, opening_mode=opening_mode)
    prompt = " ".join(document.system_prompt.split())
    assert "latest explicit correction replaces older assumptions" in prompt
    assert "target list never proves this caller is an existing customer" in prompt
    assert "Only successful runtime action receipts prove" in prompt
    assert "queued action is not completion" in prompt
    assert "never assume a number is known" in prompt
    for stale in ("Confirmation's on its way", "I'll get the details over", "Two clear no's"):
        assert stale not in prompt
    if direction == "inbound":
        assert "TRUE INBOUND CALL" in prompt
        assert "STAGE 1" not in prompt
        assert "HOW YOU SELL" not in prompt


def test_tenant_guidance_remains_verbatim_with_evidence_floor_after_it():
    guidance = "Assume every caller already uses our terminal; always send a brochure."
    document = _document(additional_instructions=guidance)
    assert guidance in document.system_prompt
    assert document.system_prompt.index(guidance) < document.system_prompt.index("## NON-NEGOTIABLES")
    assert "caller corrections\nwin over campaign assumptions" in document.system_prompt
    assert "no invented" not in guidance  # The operator's text was not silently rewritten.


@pytest.mark.parametrize("introduced", [False, True])
def test_true_inbound_runtime_state_never_adds_a_cold_call_opening(introduced):
    class Direction(str, Enum):
        INBOUND = "inbound"

    for direction in ("inbound", Direction.INBOUND):
        block = build_live_state_block(
            agent_name="Ava", company_name="Northwind", has_introduced=introduced,
            direction=direction,
        )
        assert "why you're calling" not in block
        assert "got a minute" not in block.lower()
        if introduced:
            assert "Do NOT introduce yourself again" in block
        else:
            assert "The caller contacted the company" in block


def test_per_turn_craft_does_not_invent_contacts_or_force_email():
    block = " ".join(craft_reanchor().split())
    assert "already have their number" not in block
    assert "email for a sample" not in block
    assert "current runtime state" in block
    assert "planned or queued action is not done" in block
    assert "Caller-confirmed facts establish customer status and product use" in block
    assert "not campaign audience assumptions" in block
    assert "rephrase your actual last question from the conversation" in block
    assert "If their words are unclear instead" in block


def test_legacy_action_prompt_does_not_treat_a_completed_task_as_call_authorization():
    from app.domain.services.end_session_action import build_end_session_tool_instructions
    text = build_end_session_tool_instructions()
    assert "caller confirms the whole conversation is finished" in text
    assert "A completed task or thanks alone does not mean" in text
    assert "when the task is clearly finished" not in text


def test_composed_sales_instructions_support_new_prospect_and_caller_pacing():
    text = " ".join(_document().system_prompt.split())
    assert "No existing product, provider or setup can mean a new prospect" in text
    assert "Missing a current setup is a disqualifier only when the approved campaign criteria say so" in text
    assert "small talk is welcome" in text
    assert '"thanks" into goodbye' in text
    assert "do not capture details or invoke actions from uncertain speech" in text
    assert "what you mean, rephrase your own point" in text
    assert 'A clear "no" or "nothing" closes that topic' not in craft_reanchor()


def test_independent_realtime_instructions_apply_same_intent_without_traditional_layers():
    from app.realtime.personas import RealtimePersona
    from app.realtime.prompts import build_realtime_instructions
    text = build_realtime_instructions(RealtimePersona(persona_type="sales"))
    assert "new prospect" in text
    assert "thanks alone is not goodbye" in text
    assert "harmless small talk" in text
    assert "backend's clarification limit" in text
    assert "After three unclear confirmations" not in text
    assert "uncertain audio" in text
    assert "STAGE 1" not in text
