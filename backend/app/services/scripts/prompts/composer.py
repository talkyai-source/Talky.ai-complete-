"""Layered system-prompt composer for CAMPAIGN outbound telephony calls.

Assembles the final system prompt in this order (each layer is stable or
grows less stable as you go down — this lets future Anthropic/OpenAI
prompt caching break on the boundary between layers):

  1. GENERIC_GUARDRAILS        stable across every campaign
  2. PERSONA block             one of: lead_gen | customer_support | receptionist
  3. CAMPAIGN slots            filled from the campaign's slot dict
  4. Additional instructions   optional freeform from campaign.system_prompt

The CAPTURED-slots header is prepended later, per-turn, by
`prompt_builder.compose_system_prompt()`. That layer is independent.

Provider-agnostic: the composed string is what goes into
`LLMProvider.stream_chat(system_prompt=...)`. Groq, Gemini, and any
future provider receive it unchanged — no provider touches prompt logic.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DO NOT USE FOR ASK AI.

Ask AI (the product's web-demo receptionist) has its own intentionally
short prompt at `ask_ai_session_config.ASK_AI_SYSTEM_PROMPT` and its own
`build_ask_ai_session_config()`. Do not wire this composer into that
path — the two features have different audiences (public product demo
vs real customer campaigns) and their prompts intentionally differ in
tone, length, and structure.

Call site for this composer is exactly one place:
  backend/app/domain/services/telephony_session_config.py
    → build_telephony_session_config()
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from app.services.scripts.prompts.guardrails import GENERIC_GUARDRAILS
from app.services.scripts.prompts.personas import (
    PERSONAS,
    PersonaType,
    REQUIRED_SLOTS_BY_PERSONA,
    format_common_issues,
    format_escalate_triggers,
    format_new_patient_info_needed,
    format_qualification_questions,
)

logger = logging.getLogger(__name__)


class PromptCompositionError(ValueError):
    """Raised when a persona is unknown or a required slot is missing.

    This is always a programming / configuration error — a campaign
    slipped through validation with incomplete data. Fail loud so we
    never ship half-filled {placeholders} to the LLM.
    """


def compose_prompt(
    persona_type: PersonaType,
    agent_name: str,
    company_name: str,
    campaign_slots: Mapping[str, Any],
    additional_instructions: Optional[str] = None,
) -> str:
    """Return the final system prompt string.

    Parameters
    ----------
    persona_type:
        One of the keys in PERSONAS — "lead_gen", "customer_support",
        "receptionist".
    agent_name:
        The agent's on-call name (rotates per call — see
        agent_name_rotator.pick_agent_name).
    company_name:
        The campaign's company/business name as entered by the user.
    campaign_slots:
        Persona-specific fields. Keys required for each persona live in
        REQUIRED_SLOTS_BY_PERSONA. List/dict values are auto-formatted.
    additional_instructions:
        Optional freeform text appended at the end — lets the campaign
        creator hot-patch behaviour without a code deploy.

    Raises
    ------
    PromptCompositionError
        If `persona_type` is unknown OR any required slot is missing.
    """
    if persona_type not in PERSONAS:
        raise PromptCompositionError(
            f"Unknown persona_type {persona_type!r}. "
            f"Known: {sorted(PERSONAS)}"
        )

    slots = _prepare_slots(persona_type, campaign_slots)
    slots.setdefault("agent_name", agent_name)
    slots.setdefault("company_name", company_name)

    required = REQUIRED_SLOTS_BY_PERSONA[persona_type]
    missing = [k for k in required if not slots.get(k)]
    if missing:
        raise PromptCompositionError(
            f"Missing required slots for persona {persona_type!r}: {missing}. "
            f"Provided: {sorted(slots)}"
        )

    # Fill the persona template. Any leftover {brace} raises KeyError,
    # which we re-raise as PromptCompositionError so upstream logs make
    # the cause obvious.
    try:
        persona_block = PERSONAS[persona_type].format(**slots)
    except KeyError as exc:
        raise PromptCompositionError(
            f"Persona {persona_type!r} references undefined slot: {exc}. "
            f"Provided: {sorted(slots)}"
        ) from exc

    # Guardrails header is also a template (it references {agent_name}
    # and {company_name} in the identity line).
    guardrails_block = GENERIC_GUARDRAILS.format(
        agent_name=agent_name,
        company_name=company_name,
    )

    parts = [guardrails_block, persona_block]
    if additional_instructions:
        extra = additional_instructions.strip()
        if extra:
            parts.append(
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "ADDITIONAL CAMPAIGN INSTRUCTIONS\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                + extra
            )

    composed = "\n\n".join(parts)
    logger.debug(
        "compose_prompt persona=%s agent=%s company=%s chars=%d",
        persona_type, agent_name, company_name, len(composed),
    )
    return composed


def _prepare_slots(
    persona_type: str, campaign_slots: Mapping[str, Any]
) -> dict[str, Any]:
    """Coerce list/dict slot values into their formatted string shapes so
    the persona template can substitute them with plain str.format.

    Treats the raw campaign_slots dict as read-only — returns a fresh
    dict. Non-coerced keys pass through unchanged.
    """
    slots: dict[str, Any] = dict(campaign_slots)

    if persona_type == "lead_gen":
        qq = slots.get("qualification_questions")
        if isinstance(qq, list):
            slots["qualification_questions"] = format_qualification_questions(qq)
        dq = slots.get("disqualifying_answers")
        if isinstance(dq, list):
            slots["disqualifying_answers"] = ", ".join(str(x) for x in dq)

    elif persona_type == "customer_support":
        triggers = slots.get("escalate_triggers")
        if isinstance(triggers, list):
            slots["escalate_triggers"] = format_escalate_triggers(triggers)
        issues = slots.get("common_issues")
        if isinstance(issues, list):
            slots["common_issues"] = format_common_issues(issues)
        topics = slots.get("support_topics")
        if isinstance(topics, list):
            slots["support_topics"] = ", ".join(str(t) for t in topics)

    elif persona_type == "receptionist":
        intake = slots.get("new_patient_info_needed")
        if isinstance(intake, list):
            slots["new_patient_info_needed"] = format_new_patient_info_needed(intake)
        services = slots.get("services")
        if isinstance(services, list):
            slots["services"] = ", ".join(str(s) for s in services)
        hours = slots.get("opening_hours")
        if isinstance(hours, dict):
            slots["opening_hours"] = "; ".join(
                f"{day}: {h}" for day, h in hours.items()
            )
        # Sensible defaults for optional-ish fields so empty campaigns
        # still compose cleanly.
        slots.setdefault("client_term", "patient")
        slots.setdefault("prep_info", "")
        slots.setdefault("cancellation_notice", "24 hours")
        slots.setdefault("service_details", "See website for details.")
        slots.setdefault("departments", "")

    return slots
