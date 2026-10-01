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
    block = craft_reanchor()
    assert "already have their number" not in block
    assert "email for a sample" not in block
    assert "current runtime\nstate" in block
    assert "planned or queued\naction is not done" in block
