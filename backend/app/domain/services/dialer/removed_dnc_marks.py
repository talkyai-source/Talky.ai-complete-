"""Do-not-call marks carried by a campaign's removed contacts.

A removed contact keeps its row, and that row can carry the do-not-call mark. When the same number is added or imported again the mark must come back with it, so an import reads these numbers first.
"""
from __future__ import annotations


def removed_marked_phones(db_client, *, campaign_id, tenant_id) -> set[str]:
    """Phone numbers of THIS campaign's removed contacts that carry the mark.

    A failed read raises: it must never be read as "no marks".
    """
    resp = (
        db_client.table("leads")
        .select("phone_number")
        .eq("campaign_id", campaign_id)
        .eq("tenant_id", tenant_id)
        .eq("status", "deleted")
        .eq("do_not_call", True)
        .execute()
    )
    if getattr(resp, "error", None) is not None:
        raise RuntimeError("removed do-not-call lookup failed")
    return {
        row["phone_number"]
        for row in (getattr(resp, "data", None) or [])
        if row.get("phone_number")
    }
