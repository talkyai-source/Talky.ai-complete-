"""Campaign prompt validation service.

Owns the anti-bypass gate for production campaign prompts. API endpoints can
call this service before create/update so every new or edited campaign proves
that it can render through the production persona prompt composer.
"""
from __future__ import annotations

from typing import Any, List

from app.services.scripts.prompts import PromptCompositionError, compose_prompt


class CampaignPromptValidationError(ValueError):
    """Raised when campaign prompt configuration cannot safely compose."""


def build_validated_script_config(
    *,
    persona_type: str,
    company_name: str,
    agent_names: List[str],
    campaign_slots: dict,
    additional_instructions: str,
    knowledge_driven: bool = False,
) -> dict[str, Any]:
    """Build script_config only after validating the production prompt path.

    ``knowledge_driven`` (vectorless-RAG creation wizard): when True the
    campaign's content comes from its uploaded knowledge base, not per-persona
    content slots — so composition skips the required-slot check and renders a
    lean identity+tone prompt. company_name + at least one agent name are still
    required (they anchor the agent's identity). The flag is persisted in
    script_config so the per-call composer (telephony_session_config) renders
    the same lean prompt on every call.
    """
    cleaned_company = company_name.strip()
    cleaned_agents = [name.strip() for name in agent_names if name.strip()]
    if not cleaned_company:
        raise CampaignPromptValidationError("company_name is required")
    if not cleaned_agents:
        raise CampaignPromptValidationError("At least one agent name is required")

    slots = campaign_slots or {}
    try:
        compose_prompt(
            persona_type=persona_type,  # type: ignore[arg-type]
            agent_name=cleaned_agents[0],
            company_name=cleaned_company,
            campaign_slots=slots,
            additional_instructions=additional_instructions.strip(),
            knowledge_driven=knowledge_driven,
        )
    except PromptCompositionError as exc:
        raise CampaignPromptValidationError(
            f"Campaign prompt configuration is incomplete or invalid: {exc}"
        ) from exc

    return {
        "persona_type": persona_type,
        "company_name": cleaned_company,
        "agent_names": cleaned_agents,
        "campaign_slots": slots,
        "additional_instructions": additional_instructions.strip(),
        "knowledge_driven": knowledge_driven,
    }
