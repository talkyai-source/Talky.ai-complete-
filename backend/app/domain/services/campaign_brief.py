"""Typed campaign strategy shared by save, preview, and live prompt composition.

The campaigns table already stores ``script_config`` as JSONB, so this contract
does not need a schema migration.  The important part is that this is not a UI
decoration: the same normalized object is persisted, rendered into the system
prompt, and used for the live objection limit.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo
from app.domain.services.voice_action_config import normalize_action_config

MAX_BRIEF_IDENTITY_CHARS = 120
MAX_DECISION_MAKER_ROLE_CHARS = 160
MAX_OPENING_OBJECTIVE_CHARS = 500
MAX_TRANSFER_DESTINATION_CHARS = 255
MAX_TRANSFER_DESTINATIONS = 5
MAX_TRANSFER_NAME_CHARS = 40
MAX_TRANSFER_WHEN_CHARS = 200
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
    # Structured brief values occupy one bullet each. Collapse every whitespace
    # run so a stored newline cannot manufacture a new trusted prompt section.
    return " ".join(sanitize_tenant_text(raw, max_len=max_len).split())


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
        from app.services.scripts.prompts.prompt_safety import scan_for_injection

        if scan_for_injection(label):
            raise ValueError(
                "required_lead_fields.label must be a field label, not an instruction"
            )
        folded = key.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        fields.append({"field_key": key, "label": label})
    return fields


def normalize_required_lead_fields(value: object) -> list[dict[str, str]]:
    """Validate prompt-facing lead-field references at every write boundary."""
    return _normalise_lead_fields(value)


def _render_clean(value: object, *, max_len: int) -> str:
    """Fail closed for legacy/unvalidated JSON read from the database."""
    try:
        return _clean(value, field="stored campaign brief value", max_len=max_len)
    except ValueError:
        return ""

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_CLOCK_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def normalize_transfer_destinations(value: object) -> list[dict[str, str]]:
    """Validate the named transfer destinations (1-5); numbers are checked on save."""
    from app.domain.services.telephony.transfer_validation import (
        validate_any_transfer_destination,
    )

    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("transfer_destinations must be a list")
    if not 1 <= len(value) <= MAX_TRANSFER_DESTINATIONS:
        raise ValueError(
            f"transfer_destinations supports 1 to {MAX_TRANSFER_DESTINATIONS} destinations "
            f"(at most {MAX_TRANSFER_DESTINATIONS})"
        )
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("transfer_destinations entries must contain name, number and when")
        name = _clean(item.get("name"), field="transfer_destinations.name",
                      max_len=MAX_TRANSFER_NAME_CHARS)
        if not name:
            raise ValueError("transfer_destinations.name is required")
        if name.casefold() in seen:
            raise ValueError("transfer_destinations names must be unique, ignoring case")
        seen.add(name.casefold())
        try:
            number = validate_any_transfer_destination(str(item.get("number") or "").strip())
        except ValueError:
            raise ValueError(
                f"transfer_destinations.number for {name!r} is not a valid transfer number"
            ) from None
        when = _clean(item.get("when"), field="transfer_destinations.when",
                      max_len=MAX_TRANSFER_WHEN_CHARS)
        result.append({"name": name, "number": number, "when": when})
    if len(result) > 1 and not all(entry["when"] for entry in result):
        raise ValueError(
            "transfer_destinations.when is required when there are two or more destinations"
        )
    return result


def normalize_transfer_hours(value: object) -> dict[str, Any]:
    """Validate business hours; overnight windows are not supported."""
    if not isinstance(value, Mapping):
        raise ValueError("transfer_hours must be an object")
    unknown = set(value) - {"timezone", "days", "start", "end"}
    if unknown:
        raise ValueError(f"transfer_hours has unsupported fields: {sorted(unknown)}")
    zone = str(value.get("timezone") or "").strip()
    try:
        ZoneInfo(zone)
    except (KeyError, ValueError, OSError):
        raise ValueError("transfer_hours.timezone must be a valid IANA timezone") from None
    days = value.get("days")
    if isinstance(days, (str, bytes)) or not isinstance(days, Sequence) or not days:
        raise ValueError("transfer_hours.days must be a non-empty list of mon..sun")
    cleaned = [str(day).strip().lower() for day in days]
    if any(day not in _WEEKDAYS for day in cleaned) or len(set(cleaned)) != len(cleaned):
        raise ValueError(
            "transfer_hours.days must be unique values from mon tue wed thu fri sat sun"
        )
    start = str(value.get("start") or "").strip()
    end = str(value.get("end") or "").strip()
    if not _CLOCK_RE.fullmatch(start):
        raise ValueError("transfer_hours.start must be 24-hour HH:MM")
    if not _CLOCK_RE.fullmatch(end):
        raise ValueError("transfer_hours.end must be 24-hour HH:MM")
    if start >= end:
        raise ValueError(
            "transfer_hours.end must be later than start on the same day; "
            "overnight windows are not supported"
        )
    return {
        "timezone": zone,
        "days": [day for day in _WEEKDAYS if day in cleaned],
        "start": start,
        "end": end,
    }


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def transfer_open_now(brief: Mapping[str, Any] | None, now: datetime | None = None) -> bool:
    """True when no hours are set, or ``now`` falls inside them (brief timezone)."""
    hours = (brief or {}).get("transfer_hours")
    if not hours:
        return True
    try:
        local = _as_utc(now).astimezone(ZoneInfo(hours["timezone"]))
        if _WEEKDAYS[local.weekday()] not in hours["days"]:
            return False
        return hours["start"] <= local.strftime("%H:%M") < hours["end"]
    except (KeyError, TypeError, ValueError, OSError):
        return False  # unreadable stored hours: do not offer a transfer


def _day_span(days: Sequence[str]) -> str:
    indexes = [_WEEKDAYS.index(day) for day in days]
    if len(indexes) > 2 and indexes == list(range(indexes[0], indexes[-1] + 1)):
        return f"{_WEEKDAY_NAMES[indexes[0]]} to {_WEEKDAY_NAMES[indexes[-1]]}"
    return ", ".join(_WEEKDAY_NAMES[i] for i in indexes)


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
    transfer_destinations = None
    if raw.get("transfer_destinations"):
        transfer_destinations = normalize_transfer_destinations(raw["transfer_destinations"])
        if transfer_destination and transfer_destination not in {
            entry["number"] for entry in transfer_destinations
        }:
            raise ValueError(
                "campaign_brief.transfer_destination must be one of the "
                "transfer_destinations numbers"
            )
        # Every existing reader of the single field keeps working.
        transfer_destination = transfer_destinations[0]["number"]
    if "transfer" in actions and not transfer_destination:
        raise ValueError(
            "campaign_brief.transfer_destination is required when transfer is approved"
        )
    if transfer_destination and "transfer" not in actions:
        raise ValueError(
            "campaign_brief.transfer_destination requires transfer in approved_next_actions"
        )
    optional_transfer: dict[str, Any] = {}
    if transfer_destinations is not None:
        optional_transfer["transfer_destinations"] = transfer_destinations
    if raw.get("transfer_hours") is not None:
        if "transfer" not in actions:
            raise ValueError(
                "campaign_brief.transfer_hours requires transfer in approved_next_actions"
            )
        optional_transfer["transfer_hours"] = normalize_transfer_hours(raw["transfer_hours"])

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
        **optional_transfer,
        "required_lead_fields": _normalise_lead_fields(
            raw.get("required_lead_fields")
        ),
        "opening_objective": _clean_optional(
            raw.get("opening_objective"),
            field="opening_objective",
            max_len=MAX_OPENING_OBJECTIVE_CHARS,
        ),
        "max_objection_attempts": max_objections,
        **normalize_action_config(raw),
    }


def _render_transfer_lines(
    brief: Mapping[str, Any], destination: str, now: datetime | None
) -> list[str]:
    """Transfer facts for the prompt; numbers stay out, hours are stated truthfully."""
    named = []
    for item in brief.get("transfer_destinations") or []:
        if not isinstance(item, Mapping):
            continue
        name = _render_clean(item.get("name"), max_len=MAX_TRANSFER_NAME_CHARS)
        when = _render_clean(item.get("when"), max_len=MAX_TRANSFER_WHEN_CHARS)
        if name:
            named.append(f"{name}: {when}" if when else name)
    hours = brief.get("transfer_hours")
    if hours and not transfer_open_now(brief, now):
        try:
            available = (
                f"{_day_span(hours['days'])}, {hours['start']} to {hours['end']} "
                f"({hours['timezone']})"
            )
        except (KeyError, TypeError, ValueError):
            available = "during business hours"
        return [
            "- Transfers to a person: the team is not available right now. They are "
            f"available {available}. Do not promise a transfer on this call; you may "
            "offer to take a message instead."
        ]
    if named:
        return ["- Approved transfer destinations (use the transfer tool with the name):"] + [
            f"  - {entry}" for entry in named
        ]
    return [f"- Approved transfer destination: {destination}"]


def render_campaign_brief(
    brief: Mapping[str, Any] | None,
    *,
    representative_name: str | None = None,
    brand: str | None = None,
    now: datetime | None = None,
) -> str:
    """Render only configured facts; never invent an action or destination."""
    if not brief:
        return ""
    representative = _render_clean(
        representative_name or brief.get("representative_name") or "",
        max_len=MAX_BRIEF_IDENTITY_CHARS,
    )
    brand_name = _render_clean(
        brand or brief.get("brand") or "",
        max_len=MAX_BRIEF_IDENTITY_CHARS,
    )
    lines = ["## CAMPAIGN BRIEF"]
    if representative:
        lines.append(f"- Representative on this call: {representative}")
    if brand_name:
        lines.append(f"- Brand represented: {brand_name}")
    decision_role = _render_clean(
        brief.get("decision_maker_role"), max_len=MAX_DECISION_MAKER_ROLE_CHARS
    )
    if decision_role:
        lines.append(f"- Intended decision-maker role: {decision_role}")
    objective = _render_clean(
        brief.get("opening_objective"), max_len=MAX_OPENING_OBJECTIVE_CHARS
    )
    if objective:
        lines.append(f"- Opening objective: {objective}")

    actions = [
        _ACTION_LABELS[action]
        for action in brief.get("approved_next_actions") or []
        if action in _ACTION_LABELS
    ]
    if actions:
        lines.append("- Approved next actions: " + "; ".join(actions))
    destination = _render_clean(
        brief.get("transfer_destination"), max_len=MAX_TRANSFER_DESTINATION_CHARS
    )
    if destination:
        lines.extend(_render_transfer_lines(brief, destination, now))

    required_fields = brief.get("required_lead_fields") or []
    rendered_fields = []
    for item in required_fields:
        try:
            normalized = _normalise_lead_fields([item])
        except ValueError:
            continue
        if normalized:
            rendered_fields.append(
                f"{normalized[0]['label']} ({normalized[0]['field_key']})"
            )
    if rendered_fields:
        lines.append("- Required lead fields: " + "; ".join(rendered_fields))

    attempts = brief.get("max_objection_attempts")
    try:
        attempts = int(attempts)
    except (TypeError, ValueError):
        attempts = None
    if attempts is not None and 1 <= attempts <= 5:
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
