"""Unit tests for the layered prompt composer.

Proves three things the plan commits to:

1. The composed prompt stacks in the intended order
   (GENERIC_GUARDRAILS → PERSONA → CAMPAIGN → optional additional).
2. Every brand-free {slot} in the persona template gets filled — no
   leftover placeholders leak to the LLM.
3. Missing required slots raise PromptCompositionError loudly — no
   silent best-effort rendering of half-filled templates.
"""
from __future__ import annotations

import re

import pytest

from app.services.scripts.prompts import (
    PERSONAS,
    PromptCompositionError,
    compose_prompt,
)


LEAD_GEN_SLOTS = {
    "industry": "roofing",
    "services_description": "residential roofing",
    "pricing_info": "free estimates",
    "coverage_area": "greater Austin",
    "company_differentiator": "10-year warranty",
    "value_proposition": "replace your roof without upfront cost",
    "call_reason": "we noticed homes in your area upgrading",
    "qualification_questions": ["Are you the homeowner?", "Roof older than 10 years?"],
    "disqualifying_answers": ["renting", "brand new roof"],
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
    "common_issues": [
        {"issue": "cannot login", "solution": "send password reset"},
    ],
    "escalate_triggers": ["data breach", "legal threat"],
    "escalate_to": "technical team",
    "escalation_wait_time": "30 minutes",
}

RECEPTIONIST_SLOTS = {
    "business_type": "dental practice",
    "business_address": "123 Main St",
    "business_phone": "555-0100",
    "business_email": "hello@bright.com",
    "website": "bright.com",
    "opening_hours": {"Mon-Fri": "9-6", "Sat": "10-2"},
    "services": ["cleaning", "whitening"],
    "emergency_protocol": "same-day slots",
    "new_patient_info_needed": ["full name", "date of birth"],
}


def _no_unfilled_placeholders(text: str) -> None:
    leftover = re.findall(r"\{[a-z_][a-z_0-9]*\}", text)
    assert not leftover, f"Unfilled placeholders: {leftover}"


def test_compose_lead_gen_full():
    out = compose_prompt("lead_gen", "Alex", "Acme", LEAD_GEN_SLOTS)
    assert "HARD RULES" in out
    assert "ROLE — LEAD GENERATION" in out
    # Guardrails come before the persona block.
    assert out.index("HARD RULES") < out.index("ROLE — LEAD GENERATION")
    # Agent identity + one representative campaign slot are filled in.
    assert "Alex" in out
    assert "Acme" in out
    assert "greater Austin" in out
    _no_unfilled_placeholders(out)


def test_compose_customer_support_full():
    out = compose_prompt("customer_support", "Chris", "CloudCo", SUPPORT_SLOTS)
    assert "ROLE — CUSTOMER SUPPORT" in out
    assert "CloudCo" in out
    assert "cannot login" in out
    assert "data breach" in out
    _no_unfilled_placeholders(out)


def test_compose_receptionist_full():
    out = compose_prompt("receptionist", "Sam", "BrightSmile", RECEPTIONIST_SLOTS)
    assert "ROLE — RECEPTIONIST" in out
    assert "Mon-Fri: 9-6" in out
    assert "cleaning, whitening" in out
    _no_unfilled_placeholders(out)


def test_additional_instructions_appended_last():
    out = compose_prompt(
        "lead_gen",
        "Alex",
        "Acme",
        LEAD_GEN_SLOTS,
        additional_instructions="Always offer the warranty option first.",
    )
    assert "ADDITIONAL CAMPAIGN INSTRUCTIONS" in out
    assert out.index("ROLE — LEAD GENERATION") < out.index("ADDITIONAL CAMPAIGN INSTRUCTIONS")
    assert "warranty option first" in out


def test_unknown_persona_raises():
    with pytest.raises(PromptCompositionError, match="Unknown persona_type"):
        compose_prompt("nonsense", "Alex", "Acme", LEAD_GEN_SLOTS)


def test_missing_required_slot_raises():
    slots = dict(LEAD_GEN_SLOTS)
    slots.pop("pricing_info")
    with pytest.raises(PromptCompositionError, match="Missing required slots"):
        compose_prompt("lead_gen", "Alex", "Acme", slots)


def test_persona_registry_complete():
    # Guardrail against silently forgetting to register a persona.
    assert set(PERSONAS) == {"lead_gen", "customer_support", "receptionist"}
