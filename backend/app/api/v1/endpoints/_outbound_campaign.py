"""Shared ownership/direction guard for outbound-only campaign endpoints."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from fastapi import HTTPException

from app.core.postgres_adapter import Client


def outbound_campaign_conflict(
    campaign_id: str,
    *,
    message: str | None = None,
) -> HTTPException:
    """Build the stable public response for an outbound/inbound boundary race."""

    return HTTPException(
        status_code=409,
        detail={
            "error": "inbound_campaign_managed_separately",
            "message": message or (
                "Inbound campaigns are managed through /inbound-campaigns "
                "and cannot be changed by outbound contact endpoints."
            ),
            "campaign_ids": [str(campaign_id)],
        },
    )


def require_owned_outbound_campaign(
    db_client: Client,
    campaign_id: str,
    *,
    tenant_id: Optional[str],
    extra_columns: Iterable[str] = (),
) -> dict:
    """Return a tenant-owned outbound campaign or fail before caller writes.

    Missing and foreign IDs intentionally share a 404. Inbound campaigns have
    a separate versioned lifecycle, so outbound contact/dialer endpoints return
    the same stable 409 contract as the legacy campaign mutation endpoints.
    """

    if not tenant_id:
        raise HTTPException(
            status_code=403,
            detail="Tenant context required for this operation",
        )

    columns = list(dict.fromkeys(("id", "tenant_id", "direction", *extra_columns)))
    query = (
        db_client.table("campaigns")
        .select(", ".join(columns))
        .eq("id", campaign_id)
        .eq("tenant_id", tenant_id)
    )
    response = query.execute()
    if getattr(response, "error", None):
        raise HTTPException(
            status_code=503,
            detail="Campaign direction check unavailable",
        )
    if not response.data:
        raise HTTPException(status_code=404, detail="Campaign not found")

    campaign = response.data[0]
    # Old fixtures may omit the post-0022 column, so only an absent key gets
    # the database default. An explicitly null/blank/unknown value is corrupt
    # direction state and must fail closed, exactly like an inbound value.
    direction = str(campaign.get("direction", "outbound")).strip().lower()
    if direction != "outbound":
        raise outbound_campaign_conflict(campaign_id)
    return campaign
