"""Knowledge-driven persona composition (vectorless-RAG creation wizard, P4).

When a campaign is created knowledge-first, compose_prompt(knowledge_driven=True)
must render a lean identity+tone prompt WITHOUT the per-persona content slots —
the substance comes from the injected knowledge base. The default path
(knowledge_driven=False) must keep raising on missing slots, unchanged.
"""
from __future__ import annotations

import pytest

from app.domain.services.campaign_prompt_service import (
    CampaignPromptValidationError,
    build_validated_script_config,
)
from app.services.scripts.prompts.composer import (
    PromptCompositionError,
    compose_prompt,
)

PERSONAS = ["lead_gen", "customer_support", "receptionist"]


@pytest.mark.parametrize("persona", PERSONAS)
def test_knowledge_driven_composes_with_no_slots(persona):
    prompt = compose_prompt(
        persona_type=persona,
        agent_name="Jamie",
        company_name="Talk-Lee",
        campaign_slots={},  # deliberately empty — the KB carries content
        knowledge_driven=True,
    )
    assert "Jamie" in prompt
    assert "Talk-Lee" in prompt
    # the lean body tells the agent to lean on the injected knowledge
    assert "knowledge" in prompt.lower()


@pytest.mark.parametrize("persona", PERSONAS)
def test_default_path_still_requires_slots(persona):
    # Regression guard: without the flag, empty slots must still fail loud.
    with pytest.raises(PromptCompositionError):
        compose_prompt(
            persona_type=persona,
            agent_name="Jamie",
            company_name="Talk-Lee",
            campaign_slots={},
        )


def test_knowledge_driven_inbound_has_directive():
    prompt = compose_prompt(
        persona_type="receptionist",
        agent_name="Sam",
        company_name="Acme",
        campaign_slots={},
        direction="inbound",
        knowledge_driven=True,
    )
    assert "Sam" in prompt and "Acme" in prompt


def test_validated_script_config_knowledge_driven_skips_slots():
    cfg = build_validated_script_config(
        persona_type="lead_gen",
        company_name="Talk-Lee",
        agent_names=["Jamie", "Alex"],
        campaign_slots={},  # no content slots
        additional_instructions="",
        knowledge_driven=True,
    )
    assert cfg["knowledge_driven"] is True
    assert cfg["company_name"] == "Talk-Lee"
    assert cfg["agent_names"] == ["Jamie", "Alex"]


def test_validated_script_config_default_false_when_omitted():
    # existing callers that don't pass the flag get the slot-based behaviour
    with pytest.raises(CampaignPromptValidationError):
        build_validated_script_config(
            persona_type="lead_gen",
            company_name="Talk-Lee",
            agent_names=["Jamie"],
            campaign_slots={},  # missing required slots → must raise
            additional_instructions="",
        )


def test_knowledge_driven_still_requires_company_and_agent():
    with pytest.raises(CampaignPromptValidationError):
        build_validated_script_config(
            persona_type="lead_gen",
            company_name="   ",  # blank company
            agent_names=["Jamie"],
            campaign_slots={},
            additional_instructions="",
            knowledge_driven=True,
        )
    with pytest.raises(CampaignPromptValidationError):
        build_validated_script_config(
            persona_type="lead_gen",
            company_name="Talk-Lee",
            agent_names=[],  # no agents
            campaign_slots={},
            additional_instructions="",
            knowledge_driven=True,
        )
