"""The concise guide is composed once on both supported persona paths.

The retired sentence quotas, exemplar budgets, forced silence, filler licensing
and opener choreography are no longer runtime contracts. These assertions check
prompt assembly only, not whether a model speaks briefly or follows the guide.
"""
import pytest

from app.services.scripts.prompts import compose_prompt
from app.services.scripts.prompts.guardrails import COMMUNICATION_PRINCIPLES


LEAD_GEN_SLOTS = {
    "industry": "roofing",
    "services_description": "residential roofing",
    "coverage_area": "greater Austin",
    "value_proposition": "replace your roof without upfront cost",
    "call_reason": "we noticed homes in your area upgrading",
    "qualification_questions": ["Are you the homeowner?"],
    "disqualifying_answers": ["renting"],
    "calendar_booking_type": "a free home assessment",
}

SUPPORT_SLOTS = {
    "business_hours": "M-F 9-6",
    "website": "cloudco.io",
    "support_email": "help@cloudco.io",
    "refund_policy": "30 days",
    "cancellation_policy": "anytime",
    "complaint_policy": "reviewed in 48h",
    "support_topics": ["billing", "tech"],
    "common_issues": [{"issue": "cannot login", "solution": "send password reset"}],
    "escalate_triggers": ["data breach"],
    "escalate_to": "technical team",
    "escalation_wait_time": "30 minutes",
}

RECEPTIONIST_SLOTS = {
    "business_type": "dental practice",
    "business_address": "123 Main St",
    "business_phone": "555-0100",
    "business_email": "hello@bright.com",
    "website": "bright.com",
    "opening_hours": {"Mon-Fri": "9-6"},
    "services": ["cleaning"],
    "emergency_protocol": "same-day slots",
    "new_patient_info_needed": ["full name"],
}

ALL_PERSONAS = (
    ("lead_gen", LEAD_GEN_SLOTS),
    ("customer_support", SUPPORT_SLOTS),
    ("receptionist", RECEPTIONIST_SLOTS),
)



@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
@pytest.mark.parametrize("knowledge_driven", [False, True])
def test_each_persona_path_gets_one_natural_conversation_guide(persona, slots, knowledge_driven):
    prompt = compose_prompt(persona, "Alex", "Acme", slots, knowledge_driven=knowledge_driven)
    assert prompt.count(COMMUNICATION_PRINCIPLES.strip()) == 1
    # Answer-first wording since 2026-10-08 (standard REL-1).
    assert "Be warm, clear and concise: answer what they asked first" in prompt
    assert "Use your own natural" in prompt
    assert "Ask one useful question at a time" in prompt
    assert "internal\nreasoning, tool names, markdown or stage directions" in prompt
