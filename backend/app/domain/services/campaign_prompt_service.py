"""Campaign prompt validation service.

Owns the anti-bypass gate for production campaign prompts. API endpoints can
call this service before create/update so every new or edited campaign proves
that it can render through the production persona prompt composer.
"""
from __future__ import annotations

import logging
from typing import Any, List

from app.services.scripts.prompts import PromptCompositionError, compose_prompt
from app.services.scripts.prompts.guardrails import scan_instruction_conflicts
from app.services.scripts.prompts.prompt_safety import (
    MAX_AGENT_NAME,
    MAX_COMPANY_NAME,
    MAX_SLOT_VALUE,
    sanitize_tenant_text,
    too_long,
)

logger = logging.getLogger(__name__)


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
    # Reject absurd lengths at the boundary (token-budget / cost / latency
    # protection), then defensively sanitise what we keep so a stray
    # {placeholder} or control char can't reach the system prompt.
    if too_long(company_name, max_len=MAX_COMPANY_NAME):
        raise CampaignPromptValidationError(
            f"company_name is too long (max {MAX_COMPANY_NAME} characters)"
        )
    # additional_instructions (the campaign Goal) is intentionally uncapped — no
    # length rejection. It's still sanitised below and bounded by the compliance
    # floor at runtime.
    for name in agent_names:
        if too_long(name, max_len=MAX_AGENT_NAME):
            raise CampaignPromptValidationError(
                f"agent name is too long (max {MAX_AGENT_NAME} characters)"
            )

    cleaned_company = sanitize_tenant_text(company_name, max_len=MAX_COMPANY_NAME)
    cleaned_agents = [
        sanitize_tenant_text(name, max_len=MAX_AGENT_NAME)
        for name in agent_names
        if name and name.strip()
    ]
    if not cleaned_company:
        raise CampaignPromptValidationError("company_name is required")
    if not cleaned_agents:
        raise CampaignPromptValidationError("At least one agent name is required")

    # Shallow-sanitise string slot values (operator-supplied) the same way.
    # List/dict slots are formatted later by the composer's _prepare_slots.
    raw_slots = campaign_slots or {}
    slots = {
        k: (sanitize_tenant_text(v, max_len=MAX_SLOT_VALUE) if isinstance(v, str) else v)
        for k, v in raw_slots.items()
    }
    # Uncapped: sanitise (control chars / braces / whitespace) but don't truncate.
    cleaned_instructions = sanitize_tenant_text(additional_instructions)

    # Non-blocking safety advisory: respect the author's content, but warn if it
    # tries to override an invariant (e.g. scripting an AI-denial). The runtime
    # compliance floor enforces the invariant regardless; this just informs.
    conflict_warnings = scan_instruction_conflicts(cleaned_instructions)
    for w in conflict_warnings:
        logger.warning("campaign_instruction_conflict company=%s: %s", cleaned_company, w)
    try:
        compose_prompt(
            persona_type=persona_type,  # type: ignore[arg-type]
            agent_name=cleaned_agents[0],
            company_name=cleaned_company,
            campaign_slots=slots,
            additional_instructions=cleaned_instructions,
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
        "additional_instructions": cleaned_instructions,
        "knowledge_driven": knowledge_driven,
    }
