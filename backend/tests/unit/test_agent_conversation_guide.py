"""A conversation guide supplies facts and tools without scripting each reply."""
import pytest

from app.domain.services.llm_guardrails import get_guardrails
from app.realtime.personas import RealtimePersona
from app.realtime.prompts import build_realtime_instructions
from app.services.scripts.call_state_tracker import CallState
from app.domain.services.voice_pipeline.contact_capture import ContactCaptureState, CaptureStatus
from app.services.scripts.prompt_builder import compose_system_prompt
from app.services.scripts.prompts.composer import compose_prompt
from app.services.scripts.prompts.live_state import build_live_state_block
from tests.unit.test_prompt_composer import LEAD_GEN_SLOTS, SUPPORT_SLOTS, RECEPTIONIST_SLOTS


@pytest.mark.parametrize("persona,slots", [
    ("lead_gen", LEAD_GEN_SLOTS), ("customer_support", SUPPORT_SLOTS),
    ("receptionist", RECEPTIONIST_SLOTS),
])
def test_guide_preserves_campaign_details_without_staged_scripts(persona, slots):
    guidance = "Offer a demonstration only if the caller requests one."
    text = compose_prompt(persona, "Ava", "Northwind", slots, additional_instructions=guidance)
    assert "Ava" in text and "Northwind" in text and guidance in text
    for script in ("STAGE 1", "STAGE 2", "DIAGNOSIS LOOP", "CROSS-NICHE", "AGENT:", "under twenty words"):
        assert script not in text
    assert "record_contact" in text
    assert "runtime receipts" in text
    assert len(text.split()) < 950


def test_pending_contact_is_context_not_a_compulsory_script():
    state = CallState(email="alex@example.test", email_confirmed=False)
    prompt = compose_system_prompt("BASE", state)
    assert "alex@example.test" in prompt and "awaiting_confirmation" in prompt
    assert "Say EXACTLY" not in prompt and "ACTION THIS TURN" not in prompt


@pytest.mark.parametrize("introduced,interrupted", [(False, False), (True, False), (False, True)])
def test_runtime_identity_is_state_without_a_forced_opening(introduced, interrupted):
    block = build_live_state_block(agent_name="Ava", company_name="Northwind", has_introduced=introduced,
                                  opening_interrupted=interrupted, direction="inbound")
    assert "Ava" in block and "Northwind" in block
    assert "direction=inbound" in block
    assert "under twenty words" not in block and "Turn priority:" not in block
    assert "introduction=" in block


@pytest.mark.parametrize("text", [
    "Starter costs £19 (excluding VAT).", "Support runs on weekdays (except bank holidays).",
    "Of course, take your time.", "Sure, we can explain the options.",
])
def test_cleaner_preserves_ordinary_qualifiers_and_conversation(text):
    assert get_guardrails().clean_response(text) == text


def test_artifact_cleanup_preserves_normal_output():
    guard = get_guardrails()
    assert guard.clean_response("<think>private reasoning</think>**Hello.**") == "Hello."


def test_native_guide_is_separate_and_uses_the_contact_tool_truth():
    text = build_realtime_instructions(RealtimePersona(agent_name="Ava", company_name="Northwind", campaign_guidance="Use the approved demo offer."))
    assert "Ava" in text and "Use the approved demo offer." in text
    assert "record_contact" in text and "saved" in text
    assert "ask for a clear yes or no" not in text and "backend's clarification limit" not in text
    assert "HARD RULES" not in text and "STAGE" not in text
    assert len(text.split()) < 700


def test_incomplete_contact_preserves_quote_and_status_without_inventing_value():
    capture = ContactCaptureState(kind="email", status=CaptureStatus.NEEDS_CLARIFICATION,
                                  raw_value="alex at the company", normalized_value=None)
    text = compose_system_prompt("BASE", CallState(email_capture=capture))
    assert '"caller_quote": "alex at the company"' in text
    assert '"status": "needs_clarification"' in text and '"value": null' in text
    assert "Say EXACTLY" not in text
