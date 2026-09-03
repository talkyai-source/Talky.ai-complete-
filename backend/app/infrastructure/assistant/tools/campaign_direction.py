"""Shared direction contract for outbound-only assistant campaign tools."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def is_outbound_campaign(row: Mapping[str, Any]) -> bool:
    """Treat only the schema's outbound value (or a legacy omission) as outbound."""
    direction = str(row.get("direction", "outbound")).strip().lower()
    return direction == "outbound"


def inbound_campaign_refusal(
    campaign_ids: Iterable[str],
    *,
    include_success: bool = False,
) -> dict[str, Any]:
    """Return the same machine-readable contract as the legacy HTTP guard."""
    unique_ids = list(dict.fromkeys(str(value) for value in campaign_ids))
    result: dict[str, Any] = {
        "error": "inbound_campaign_managed_separately",
        "message": (
            "Inbound campaigns must be changed through the inbound campaign "
            "versioned lifecycle."
        ),
        "campaign_ids": unique_ids,
    }
    if include_success:
        result["success"] = False
    return result


def outbound_campaign_refusal(
    rows: Iterable[Mapping[str, Any]],
    *,
    include_success: bool = False,
) -> dict[str, Any] | None:
    """Return a refusal when any selected campaign is not outbound."""
    blocked = [str(row.get("id")) for row in rows if not is_outbound_campaign(row)]
    if not blocked:
        return None
    return inbound_campaign_refusal(blocked, include_success=include_success)
