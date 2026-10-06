"""Audited prompt conflicts: evidence and direction beat campaign assumptions."""
from enum import Enum

import pytest

from app.services.scripts.prompts.composer import compose_prompt_document
from app.services.scripts.prompts.live_state import build_live_state_block


def _document(**kwargs):
    return compose_prompt_document(
        "lead_gen", "Ava", "Northwind Systems", {}, knowledge_driven=True, **kwargs,
    )


def test_default_outbound_prompt_stays_compact_without_duplicate_turn_shape():
    document = _document()
    assert len(document.system_prompt.split()) < 950
    assert document.system_prompt.count("## HOW TO SPEAK") == 1
    assert "FINAL RESPONSE CONTRACT" not in document.system_prompt
    assert document.system_prompt == "\n\n".join(layer.content for layer in document.layers)


@pytest.mark.parametrize("direction", ["outbound", "inbound"])
@pytest.mark.parametrize("opening_mode", ["agent_first", "callee_first"])
def test_corrections_and_action_evidence_survive_all_direction_variants(direction, opening_mode):
    document = _document(direction=direction, opening_mode=opening_mode)
    prompt = " ".join(document.system_prompt.split())
    for rule in ("Caller corrections and current runtime evidence", "product use from a campaign list", "Only successful runtime receipts", "pending or failed work is not complete", "A known line number is context"):
        assert rule in prompt
    for stale in ("Confirmation's on its way", "I'll get the details over", "Two clear no's", "STAGE 1"):
        assert stale not in prompt
    if direction == "inbound":
        assert "TRUE INBOUND CALL" in prompt


def test_tenant_guidance_remains_verbatim_with_evidence_floor_after_it():
    guidance = "Assume every caller already uses our terminal; always send a brochure."
    document = _document(additional_instructions=guidance)
    assert guidance in document.system_prompt
    assert document.system_prompt.index(guidance) < document.system_prompt.index("## NON-NEGOTIABLES")
    assert "Caller corrections and current runtime evidence override campaign assumptions" in document.system_prompt
    assert "no invented" not in guidance  # The operator's text was not silently rewritten.


@pytest.mark.parametrize("introduced", [False, True])
def test_true_inbound_runtime_state_never_adds_a_cold_call_opening(introduced):
    class Direction(str, Enum):
        INBOUND = "inbound"
    for direction in ("inbound", Direction.INBOUND):
        block = build_live_state_block(agent_name="Ava", company_name="Northwind", has_introduced=introduced, direction=direction)
        assert "direction=inbound" in block
        assert ("introduction=delivered" if introduced else "introduction=not_delivered") in block
        assert "why you're calling" not in block and "got a minute" not in block.lower()


def test_legacy_action_prompt_does_not_treat_a_completed_task_as_call_authorization():
    from app.domain.services.end_session_action import build_end_session_tool_instructions
    text = build_end_session_tool_instructions()
    assert "caller confirms the whole conversation is finished" in text
    assert "A completed task or thanks alone does not mean" in text
    assert "when the task is clearly finished" not in text


def test_composed_sales_instructions_support_new_prospect_and_caller_pacing():
    text = " ".join(_document().system_prompt.split())
    assert "prospect without an existing setup" in text
    assert "Harmless small talk is welcome" in text
    assert "factual no does not necessarily end" in text
    assert "ask naturally when something is unclear" in text
    assert "do not run a checklist" in text


def test_independent_realtime_instructions_apply_same_intent_without_traditional_layers():
    from app.realtime.personas import RealtimePersona
    from app.realtime.prompts import build_realtime_instructions
    text = " ".join(build_realtime_instructions(RealtimePersona(persona_type="sales")).split())
    assert "new prospect" in text
    assert "factual no does not necessarily end" in text
    assert "Harmless small talk" in text
    assert "record_contact" in text and "persistence result says saved" in text
    assert "backend's clarification limit" not in text and "STAGE 1" not in text
