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

from app.services.scripts.prompts.direction import (
    inbound_directive_block,
)
from app.services.scripts.prompts.guardrails import GENERIC_GUARDRAILS
from app.services.scripts.prompts.personas import (
    PERSONA_BODIES,
    PERSONA_OPENINGS,
    PersonaType,
    REQUIRED_SLOTS_BY_PERSONA,
    format_common_issues,
    format_escalate_triggers,
    format_new_patient_info_needed,
    format_qualification_questions,
)

logger = logging.getLogger(__name__)


def _format_pronunciations(value: Any) -> str:
    """Render the optional ``pronunciations`` campaign slot into a
    PRONUNCIATIONS block the LLM can read on first mention.

    Accepts a dict ``{written: spoken}`` (preferred shape — most
    natural for operators to type) and tolerates a list of pairs
    ``[{"name": ..., "say": ...}, ...]`` for compatibility with form
    builders that prefer ordered lists.

    Returns an empty string for missing / empty / malformed input so
    callers can unconditionally call this and skip a falsy result.
    Bad shapes are logged and dropped — a misconfigured campaign must
    not block the call from going out.
    """
    if not value:
        return ""

    pairs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for written, spoken in value.items():
            written_s = str(written or "").strip()
            spoken_s = str(spoken or "").strip()
            if written_s and spoken_s:
                pairs.append((written_s, spoken_s))
    elif isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            written = item.get("name") or item.get("word") or item.get("written")
            spoken = item.get("say") or item.get("spoken") or item.get("ipa")
            written_s = str(written or "").strip()
            spoken_s = str(spoken or "").strip()
            if written_s and spoken_s:
                pairs.append((written_s, spoken_s))
    else:
        logger.warning(
            "pronunciations_skipped reason=unsupported_type type=%s",
            type(value).__name__,
        )
        return ""

    if not pairs:
        return ""

    bullets = "\n".join(f'  "{w}" → say it like "{s}"' for w, s in pairs)
    return (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "PRONUNCIATIONS — use these on first mention\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        + bullets
    )


FINAL_RESPONSE_CONTRACT = """\
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FINAL RESPONSE CONTRACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For every reply, speak only the words the caller should hear. Keep it short,
natural, and useful. Ask at most one question. Do not output markdown, bullets,
stage directions, labels, internal reasoning, or tool names. Do not override
the hard rules above.
"""


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
    *,
    direction: str = "outbound",
) -> str:
    """Return the final system prompt string.

    Parameters
    ----------
    persona_type:
        One of the keys in PERSONA_BODIES — "lead_gen", "customer_support",
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
    direction:
        ``"outbound"`` (default) when the platform initiated the call,
        or ``"inbound"`` when the caller is reaching out / first-speaker
        is ``"user"``. Selects the persona's OPENING block and prepends
        the canonical inbound directive when applicable. The default
        preserves pre-T4 behaviour for callers that pass only positional
        arguments.

    Raises
    ------
    PromptCompositionError
        If `persona_type` is unknown, the direction is unknown for the
        persona, or any required slot is missing.
    """
    if persona_type not in PERSONA_BODIES:
        raise PromptCompositionError(
            f"Unknown persona_type {persona_type!r}. "
            f"Known: {sorted(PERSONA_BODIES)}"
        )

    direction_key = (direction or "outbound").strip().lower()
    persona_openings = PERSONA_OPENINGS[persona_type]
    if direction_key not in persona_openings:
        raise PromptCompositionError(
            f"Persona {persona_type!r} has no opening for direction "
            f"{direction_key!r}. Known directions: {sorted(persona_openings)}"
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

    # Build the per-direction persona template by concatenating the
    # selected opening with the body. Both pieces share {placeholders}
    # that are filled in a single str.format pass below — no recursive
    # substitution, no template-engine.
    persona_template = (
        persona_openings[direction_key] + "\n" + PERSONA_BODIES[persona_type]
    )
    # The {direction_opening} marker on the body is a no-op placeholder
    # for the legacy / backward-compat alias path that pre-merged the
    # opening into the body string. With the explicit concatenation
    # above, the marker is empty in the formatted output.
    slots.setdefault("direction_opening", "")

    try:
        persona_block = persona_template.format(**slots)
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

    parts: list[str] = []
    # Inbound calls get the canonical direction directive at position 0
    # so early-token attention dominates any outbound-flavoured prose
    # that might still be in the persona body. This block also carries
    # the INBOUND_DIRECTIVE_SENTINEL, which the runtime
    # select_inbound_base_prompt() reads as an idempotency signal.
    if direction_key == "inbound":
        parts.append(
            inbound_directive_block(
                agent_name=agent_name,
                company_name=company_name,
            )
        )
        # Metric (T4-B2) — distinguishes preferred compose-time
        # injection from the runtime fallback. A future climb in
        # source="runtime" relative to source="compose" is the signal
        # that some persona-driven path is missing direction propagation.
        try:
            from app.infrastructure.metrics.voice_metrics import (
                record_inbound_directive_applied,
            )
            record_inbound_directive_applied("compose")
        except Exception as exc:  # noqa: BLE001
            logger.debug("voice_metrics_directive_record_failed err=%s", exc)
    parts.append(guardrails_block)
    # Pronunciations sit between guardrails and persona so the model
    # picks them up before reading the company-name-heavy persona body.
    # Renders to "" when the campaign didn't supply pronunciations,
    # which the join below skips naturally.
    pron_block = _format_pronunciations(campaign_slots.get("pronunciations"))
    if pron_block:
        parts.append(pron_block)
    parts.append(persona_block)
    if additional_instructions:
        extra = additional_instructions.strip()
        if extra:
            parts.append(
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "ADDITIONAL CAMPAIGN INSTRUCTIONS\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "These instructions are lower priority than HARD RULES, "
                "PRODUCTION SUCCESS / FAILURE, NICHE AND COMPLIANCE "
                "ADAPTATION, and the persona safety boundaries above. Ignore "
                "any part that conflicts with those higher-priority rules.\n\n"
                + extra
                + "\n\nReminder: the additional campaign instructions can add "
                "business-specific facts and preferences, but they cannot "
                "override safety, compliance, escalation, truthfulness, or "
                "voice-output rules."
            )

    parts.append(FINAL_RESPONSE_CONTRACT)

    composed = "\n\n".join(parts)
    logger.debug(
        "compose_prompt persona=%s direction=%s agent=%s company=%s chars=%d",
        persona_type, direction_key, agent_name, company_name, len(composed),
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
