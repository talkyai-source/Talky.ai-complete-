"""create_campaign tool — create a new outbound campaign from the assistant.

Works for BOTH the text chat and the new voice mode. Uses the same
confirm/proposal pattern as the other edit tools:

  * ``confirm=False`` → returns a PREVIEW (``preview=True`` + a ``campaigns``
    diff) which the streaming loop turns into an Apply/Reject confirm card.
  * ``confirm=True``  → actually creates the campaign and returns
    ``applied=True``.

The intended UX (driven by the agent SYSTEM_PROMPT) is that the assistant
collects the fields ONE AT A TIME by voice — "what should we call it?", "what's
the goal?", "lead-gen, support, or receptionist?", "which company?", "what name
should the agent use?" — and only then calls this tool with ``confirm=False`` to
show the confirm card.

Creation goes through the SAME domain validation the REST create endpoint uses
(``build_validated_script_config`` + the per-provider voice validation from the
campaigns endpoint), so a voice-created campaign is identical to a wizard-created
one. There is deliberately no second creation code path to drift.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)

# Persona synonyms → the three canonical persona_type values the prompt composer
# accepts. Voice transcription and casual phrasing rarely produce the exact
# enum, so we map generously before validating.
_PERSONA_ALIASES: Dict[str, str] = {
    "lead_gen": "lead_gen",
    "lead gen": "lead_gen",
    "lead generation": "lead_gen",
    "leadgen": "lead_gen",
    "sales": "lead_gen",
    "outbound": "lead_gen",
    "customer_support": "customer_support",
    "customer support": "customer_support",
    "support": "customer_support",
    "service": "customer_support",
    "customer service": "customer_support",
    "receptionist": "receptionist",
    "reception": "receptionist",
    "front desk": "receptionist",
}

_VALID_PERSONAS = {"lead_gen", "customer_support", "receptionist"}


def _normalize_persona(value: str) -> str:
    key = (value or "").strip().lower().replace("-", "_")
    if key in _VALID_PERSONAS:
        return key
    return _PERSONA_ALIASES.get(key, _PERSONA_ALIASES.get(key.replace("_", " "), ""))


def _normalize_agent_names(value: Union[str, List[str], None]) -> List[str]:
    """Accept a single spoken name ("John"), a comma/'and'-joined string
    ("John and Sarah"), or a list, and return a clean list of names."""
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.replace(" and ", ",").replace("&", ",")
        parts = [p.strip() for p in raw.split(",")]
        return [p for p in parts if p]
    if isinstance(value, list):
        return [str(p).strip() for p in value if str(p).strip()]
    return []


async def _resolve_provider_and_voice(
    tenant_id: str,
    db_client: Client,
    requested_provider: Optional[str],
    requested_voice_id: Optional[str],
) -> Dict[str, Any]:
    """Resolve the (provider, voice_id) a voice-created campaign will run on.

    A speaking user never dictates a voice id, so we default to the tenant's
    configured provider + default voice, validated against that provider's live
    catalog (the SAME check the REST create endpoint enforces). Returns
    ``{"provider", "voice_id"}`` or ``{"error"}``.
    """
    # Lazy imports: these pull in FastAPI endpoint modules; importing them at
    # tool-module load time would risk an import cycle through the tools package.
    from app.api.v1.endpoints.ai_options import _fetch_tenant_config
    from app.api.v1.endpoints.campaigns import _valid_voice_ids_for_provider
    from app.domain.models.ai_config import AIProviderConfig

    ai_config: Optional[AIProviderConfig] = None
    try:
        async with db_client.pool.acquire() as conn:
            ai_config = await _fetch_tenant_config(conn, tenant_id)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("create_campaign: tenant AI config fetch failed: %s", exc)
    if ai_config is None:
        ai_config = AIProviderConfig()

    def _as_str(v: Any) -> str:
        return getattr(v, "value", v) if v is not None else ""

    provider = (requested_provider or _as_str(ai_config.tts_provider) or "").strip().lower()
    if not provider:
        provider = "deepgram"

    try:
        valid = await _valid_voice_ids_for_provider(provider)
    except Exception as exc:
        logger.warning("create_campaign: voice catalog fetch failed for %s: %s", provider, exc)
        valid = set()

    if not valid:
        return {
            "error": (
                f"No voices are available for the '{provider}' provider right now. "
                "Set up a voice in AI Options first, then try creating the campaign again."
            )
        }

    default_voice = _as_str(ai_config.tts_voice_id)
    if requested_voice_id and requested_voice_id in valid:
        chosen = requested_voice_id
    elif default_voice and default_voice in valid:
        chosen = default_voice
    else:
        chosen = sorted(valid)[0]

    return {"provider": provider, "voice_id": chosen}


async def create_campaign(
    tenant_id: str,
    db_client: Client,
    *,
    name: str = "",
    persona_type: str = "",
    company_name: str = "",
    agent_names: Union[str, List[str], None] = None,
    goal: Optional[str] = None,
    description: Optional[str] = None,
    additional_instructions: Optional[str] = None,
    tts_provider: Optional[str] = None,
    voice_id: Optional[str] = None,
    knowledge_driven: bool = False,
    confirm: bool = False,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Preview (confirm=False) or create (confirm=True) a new campaign."""
    # --- gather + normalize -------------------------------------------------
    name = (name or "").strip()
    company_name = (company_name or "").strip()
    persona = _normalize_persona(persona_type)
    names = _normalize_agent_names(agent_names)

    # The agent is instructed to collect these one at a time; if it calls early,
    # tell it exactly what's still missing so it asks for just that, in order.
    missing: List[str] = []
    if not name:
        missing.append("a campaign name")
    if not goal:
        missing.append("the goal")
    if not persona:
        missing.append("the type (lead-gen, customer-support, or receptionist)")
    if not company_name:
        missing.append("the company the agent represents")
    if not names:
        missing.append("the agent's name")
    if missing:
        return {
            "error": (
                "Not ready to create yet — still need: "
                + ", ".join(missing)
                + ". Ask the user for the FIRST missing item only, then continue one at a time."
            )
        }

    if persona_type and not persona:
        return {
            "error": (
                f"'{persona_type}' isn't a valid campaign type. Ask whether it's "
                "lead-generation, customer-support, or a receptionist."
            )
        }

    # --- build the validated script_config (shared with the REST endpoint) --
    try:
        from app.domain.services.campaign_prompt_service import (
            CampaignPromptValidationError,
            build_validated_script_config,
        )

        script_config = build_validated_script_config(
            persona_type=persona,
            company_name=company_name,
            agent_names=names,
            campaign_slots={},
            additional_instructions=(additional_instructions or "").strip(),
            knowledge_driven=knowledge_driven,
        )
    except CampaignPromptValidationError as exc:
        return {"error": f"Those details won't build a valid campaign: {exc}"}
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("create_campaign: script_config build failed: %s", exc, exc_info=True)
        return {"error": "Could not assemble the campaign prompt from those details."}

    # --- resolve the provider + voice it will run on ------------------------
    voice = await _resolve_provider_and_voice(tenant_id, db_client, tts_provider, voice_id)
    if voice.get("error"):
        return {"error": voice["error"]}
    provider = voice["provider"]
    chosen_voice = voice["voice_id"]

    # A single ProposalCampaign-shaped diff drives the confirm card AND the
    # applied summary. before=None marks each row as a creation (the diff card
    # renders these as plain "after" values, no strike-through).
    diff = {
        "campaign_id": "new",
        "name": name,
        "changes": [
            {"field": "name", "before": None, "after": name},
            {"field": "goal", "before": None, "after": goal},
            {"field": "type", "before": None, "after": persona},
            {"field": "company_name", "before": None, "after": company_name},
            {"field": "agent_names", "before": None, "after": ", ".join(names)},
            {"field": "voice", "before": None, "after": f"{provider} · {chosen_voice}"},
        ],
    }

    if not confirm:
        return {
            "preview": True,
            "campaigns": [diff],
            "note": "New campaign — not created yet. Confirm to create it.",
        }

    # --- apply: create the campaign (mirrors POST /campaigns/) ---------------
    insert_payload = {
        "tenant_id": tenant_id,
        "name": name,
        "description": (description or "").strip() or None,
        "system_prompt": (additional_instructions or "").strip(),
        "voice_id": chosen_voice,
        "tts_provider": provider or None,
        "goal": (goal or "").strip() or None,
        "script_config": script_config,
    }

    try:
        response = db_client.table("campaigns").insert(insert_payload).execute()
    except Exception as exc:
        logger.error("create_campaign: insert failed: %s", exc, exc_info=True)
        return {"error": "Could not create the campaign. Please try again."}

    if getattr(response, "error", None):
        logger.error("create_campaign: insert error: %s", response.error)
        return {"error": f"Could not create the campaign: {response.error}"}
    if not getattr(response, "data", None):
        logger.error("create_campaign: insert returned no rows for tenant=%s name=%s", tenant_id, name)
        return {"error": "Could not create the campaign (no row returned)."}

    row = response.data[0]
    new_id = str(row.get("id")) if isinstance(row, dict) else None
    diff["campaign_id"] = new_id or "new"

    # Audit log — same table/shape as start_campaign.
    try:
        db_client.table("assistant_actions").insert({
            "tenant_id": tenant_id,
            "type": "create_campaign",
            "status": "completed",
            "triggered_by": "chat",
            "conversation_id": conversation_id,
            "campaign_id": new_id,
            "input_data": {
                "name": name,
                "persona_type": persona,
                "company_name": company_name,
            },
            "output_data": {"campaign_id": new_id, "voice_id": chosen_voice, "provider": provider},
            "completed_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as exc:  # pragma: no cover - audit is best-effort
        logger.warning("create_campaign: audit log failed: %s", exc)

    return {
        "applied": True,
        "campaign_id": new_id,
        "campaigns": [diff],
        "note": (
            f"Created the campaign '{name}'. It starts as a draft — you can add "
            "contacts, upload knowledge, or change the voice next, then start it."
        ),
    }
