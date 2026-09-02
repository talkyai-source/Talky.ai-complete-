"""Typed campaign strategy shared by save, preview, and live prompt composition.

The campaigns table already stores ``script_config`` as JSONB, so this contract
does not need a schema migration.  The important part is that this is not a UI
decoration: the same normalized object is persisted, rendered into the system
prompt, and used for the live objection limit.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

MAX_BRIEF_IDENTITY_CHARS = 120
MAX_DECISION_MAKER_ROLE_CHARS = 160
MAX_OPENING_OBJECTIVE_CHARS = 500
MAX_TRANSFER_DESTINATION_CHARS = 255
MAX_LEAD_FIELD_KEY_CHARS = 64
MAX_LEAD_FIELD_LABEL_CHARS = 255
MAX_REQUIRED_LEAD_FIELDS = 20

APPROVED_NEXT_ACTIONS: tuple[str, ...] = (
    "schedule_callback",
    "send_email",
    "submit_form",
    "transfer",
    "end_call",
)
_ACTION_LABELS = {
    "schedule_callback": "schedule a callback",
    "send_email": "send a follow-up email",
    "submit_form": "submit the configured form",
    "transfer": "transfer to the approved destination",
    "end_call": "end the call politely",
}
_FIELD_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _clean(value: object, *, field: str, max_len: int) -> str:
    # Lazy import avoids a package-initialisation cycle: prompts.composer uses
    # render_campaign_brief, while app.services.scripts.__init__ re-exports the
    # composer. At call time both modules are fully initialised.
    from app.services.scripts.prompts.prompt_safety import (
        sanitize_tenant_text,
        too_long,
    )

    raw = str(value or "").strip()
    if too_long(raw, max_len=max_len):
        raise ValueError(f"{field} is too long (max {max_len} characters)")
    return sanitize_tenant_text(raw, max_len=max_len)


def _clean_optional(value: object, *, field: str, max_len: int) -> str | None:
    cleaned = _clean(value, field=field, max_len=max_len)
    return cleaned or None


def _normalise_actions(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("approved_next_actions must be a list")
    actions: list[str] = []
    for item in value:
        action = str(item or "").strip()
        if action not in APPROVED_NEXT_ACTIONS:
            raise ValueError(
                f"approved_next_actions contains unsupported action {action!r}"
            )
        if action not in actions:
            actions.append(action)
    return actions


def _normalise_lead_fields(value: object) -> list[dict[str, str]]:
    if value in (None, ""):
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("required_lead_fields must be a list")
    if len(value) > MAX_REQUIRED_LEAD_FIELDS:
        raise ValueError(
            f"required_lead_fields supports at most {MAX_REQUIRED_LEAD_FIELDS} fields"
        )

    fields: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("required_lead_fields entries must contain field_key and label")
        key = _clean(
            item.get("field_key"),
            field="required_lead_fields.field_key",
            max_len=MAX_LEAD_FIELD_KEY_CHARS,
        )
        label = _clean(
            item.get("label"),
            field="required_lead_fields.label",
            max_len=MAX_LEAD_FIELD_LABEL_CHARS,
        )
        if not key or not _FIELD_KEY_RE.fullmatch(key):
            raise ValueError(
                "required_lead_fields.field_key must start with a letter and "
                "contain only letters, numbers, and underscores"
            )
        if not label:
            raise ValueError("required_lead_fields.label is required")
        folded = key.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        fields.append({"field_key": key, "label": label})
    return fields


def normalize_campaign_brief(
    value: Mapping[str, Any] | None,
    *,
    company_name: str,
    agent_names: Sequence[str],
) -> dict[str, Any]:
    """Return the canonical persisted campaign brief.

    Brand and representative are deliberately checked against the existing
    campaign identity fields.  Keeping a convenient snapshot in the nested
    contract is useful to API clients, but allowing the two copies to disagree
    would make the prompt contradict itself when agent names rotate.
    """
    raw = dict(value or {})
    canonical_brand = _clean(
        company_name,
        field="company_name",
        max_len=MAX_BRIEF_IDENTITY_CHARS,
    )
    canonical_agents = [
        _clean(name, field="agent name", max_len=MAX_BRIEF_IDENTITY_CHARS)
        for name in agent_names
        if str(name or "").strip()
    ]
    if not canonical_brand:
        raise ValueError("company_name is required")
    if not canonical_agents:
        raise ValueError("At least one agent name is required")

    representative = _clean(
        raw.get("representative_name") or canonical_agents[0],
        field="representative_name",
        max_len=MAX_BRIEF_IDENTITY_CHARS,
    )
    brand = _clean(
        raw.get("brand") or canonical_brand,
        field="brand",
        max_len=MAX_BRIEF_IDENTITY_CHARS,
    )
    if representative.casefold() not in {name.casefold() for name in canonical_agents}:
        raise ValueError("campaign_brief.representative_name must be in agent_names")
    if brand.casefold() != canonical_brand.casefold():
        raise ValueError("campaign_brief.brand must match company_name")

    actions = _normalise_actions(raw.get("approved_next_actions"))
    transfer_destination = _clean_optional(
        raw.get("transfer_destination"),
        field="transfer_destination",
        max_len=MAX_TRANSFER_DESTINATION_CHARS,
    )
    if "transfer" in actions and not transfer_destination:
        raise ValueError(
            "campaign_brief.transfer_destination is required when transfer is approved"
        )
    if transfer_destination and "transfer" not in actions:
        raise ValueError(
            "campaign_brief.transfer_destination requires transfer in approved_next_actions"
        )

    try:
        max_objections = int(raw.get("max_objection_attempts", 2))
    except (TypeError, ValueError) as exc:
        raise ValueError("max_objection_attempts must be an integer from 1 to 5") from exc
    if not 1 <= max_objections <= 5:
        raise ValueError("max_objection_attempts must be from 1 to 5")

    return {
        "representative_name": representative,
        "brand": brand,
        "decision_maker_role": _clean_optional(
            raw.get("decision_maker_role"),
            field="decision_maker_role",
            max_len=MAX_DECISION_MAKER_ROLE_CHARS,
        ),
        "approved_next_actions": actions,
        "transfer_destination": transfer_destination,
        "required_lead_fields": _normalise_lead_fields(
            raw.get("required_lead_fields")
        ),
        "opening_objective": _clean_optional(
            raw.get("opening_objective"),
            field="opening_objective",
            max_len=MAX_OPENING_OBJECTIVE_CHARS,
        ),
        "max_objection_attempts": max_objections,
    }


def render_campaign_brief(
    brief: Mapping[str, Any] | None,
    *,
    representative_name: str | None = None,
    brand: str | None = None,
) -> str:
    """Render only configured facts; never invent an action or destination."""
    if not brief:
        return ""
    representative = str(
        representative_name or brief.get("representative_name") or ""
    ).strip()
    brand_name = str(brand or brief.get("brand") or "").strip()
    lines = ["## CAMPAIGN BRIEF"]
    if representative:
        lines.append(f"- Representative on this call: {representative}")
    if brand_name:
        lines.append(f"- Brand represented: {brand_name}")
    decision_role = str(brief.get("decision_maker_role") or "").strip()
    if decision_role:
        lines.append(f"- Intended decision-maker role: {decision_role}")
    objective = str(brief.get("opening_objective") or "").strip()
    if objective:
        lines.append(f"- Opening objective: {objective}")

    actions = [
        _ACTION_LABELS[action]
        for action in brief.get("approved_next_actions") or []
        if action in _ACTION_LABELS
    ]
    if actions:
        lines.append("- Approved next actions: " + "; ".join(actions))
    destination = str(brief.get("transfer_destination") or "").strip()
    if destination:
        lines.append(f"- Approved transfer destination: {destination}")

    required_fields = brief.get("required_lead_fields") or []
    rendered_fields = []
    for item in required_fields:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get("field_key") or "").strip()
        label = str(item.get("label") or "").strip()
        if key and label:
            rendered_fields.append(f"{label} ({key})")
    if rendered_fields:
        lines.append("- Required lead fields: " + "; ".join(rendered_fields))

    attempts = brief.get("max_objection_attempts")
    if attempts is not None:
        lines.append(f"- Maximum objection-handling attempts: {attempts}")
    lines.append(
        "Only take an approved next action after its runtime tool reports success. "
        "A configured destination does not mean transfer is currently available."
    )
    return "\n".join(lines)


def campaign_guidance_text(
    additional_instructions: str | None,
    campaign_brief: Mapping[str, Any] | None,
) -> str:
    """The sole text counted by the campaign-guidance character budget."""
    parts = [render_campaign_brief(campaign_brief)]
    additional = str(additional_instructions or "").strip()
    if additional:
        parts.append(additional)
    return "\n\n".join(part for part in parts if part)
