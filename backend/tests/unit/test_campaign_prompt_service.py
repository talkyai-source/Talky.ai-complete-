"""Tests for production campaign prompt validation."""
from __future__ import annotations

import pytest

from app.domain.services.campaign_prompt_service import (
    CampaignPromptValidationError,
    build_validated_script_config,
)


LEAD_GEN_SLOTS = {
    "industry": "roofing",
    "services_description": "residential roofing",
    "pricing_info": "free estimates",
    "coverage_area": "greater Austin",
    "company_differentiator": "10-year warranty",
    "value_proposition": "replace your roof without upfront cost",
    "call_reason": "we noticed homes in your area upgrading",
    "qualification_questions": ["Are you the homeowner?"],
    "disqualifying_answers": ["renting"],
    "calendar_booking_type": "a home assessment",
}


def test_build_validated_script_config_returns_clean_script_config():
    out = build_validated_script_config(
        persona_type="lead_gen",
        company_name="  Acme Roofing  ",
        agent_names=[" Alex ", "Sam"],
        campaign_slots=LEAD_GEN_SLOTS,
        additional_instructions="  Ask about storm damage.  ",
    )

    assert out["persona_type"] == "lead_gen"
    assert out["company_name"] == "Acme Roofing"
    assert out["agent_names"] == ["Alex", "Sam"]
    assert out["campaign_slots"] == LEAD_GEN_SLOTS
    assert out["additional_instructions"] == "Ask about storm damage."


def test_build_validated_script_config_rejects_missing_company():
    with pytest.raises(CampaignPromptValidationError, match="company_name"):
        build_validated_script_config(
            persona_type="lead_gen",
            company_name=" ",
            agent_names=["Alex"],
            campaign_slots=LEAD_GEN_SLOTS,
            additional_instructions="",
        )


def test_build_validated_script_config_rejects_missing_agent_names():
    with pytest.raises(CampaignPromptValidationError, match="agent name"):
        build_validated_script_config(
            persona_type="lead_gen",
            company_name="Acme",
            agent_names=[" "],
            campaign_slots=LEAD_GEN_SLOTS,
            additional_instructions="",
        )


def test_build_validated_script_config_rejects_incomplete_persona_slots():
    slots = dict(LEAD_GEN_SLOTS)
    slots.pop("pricing_info")

    with pytest.raises(CampaignPromptValidationError, match="incomplete or invalid"):
        build_validated_script_config(
            persona_type="lead_gen",
            company_name="Acme",
            agent_names=["Alex"],
            campaign_slots=slots,
            additional_instructions="",
        )
