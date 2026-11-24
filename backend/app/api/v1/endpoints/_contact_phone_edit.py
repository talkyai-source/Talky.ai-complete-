"""Helpers for the website's contact edit (PATCH) that move a lead's status after a phone change.

Both resets run AFTER the edit is saved, so a failed save never changes a contact's status.
They never raise: the edit (or its failure) is what the caller answers.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Word for word the assistant's sentence (lead_write_safety.W2).
DNC_MARK_TAKEN = (
    "This number was removed from the campaign earlier and is marked do not call. "
    "The contact takes that mark with the number, so it will not be dialled."
)


def _log_not_reset(campaign_id, error) -> None:
    logger.warning(
        "contact_requeue_status_not_reset campaign=%s error=%s",
        str(campaign_id)[:8], error,
    )


def release_called_contact(db_client, *, tenant_id, campaign_id, contact_id) -> None:
    """A cancelled retry leaves the lead 'called', which no Start selects: move it back."""
    try:
        resp = db_client.table("leads").update({"status": "pending"}).eq(
            "id", contact_id,
        ).eq("tenant_id", tenant_id).eq(
            "campaign_id", campaign_id,
        ).eq("status", "called").execute()
        if getattr(resp, "error", None) is not None:
            _log_not_reset(campaign_id, "returned")
    except Exception as exc:  # noqa: BLE001 - the caller's answer stands
        _log_not_reset(campaign_id, type(exc).__name__)


def reset_invalid_number_skip(db_client, *, tenant_id, campaign_id, contact_id) -> None:
    """A contact skipped for an invalid number is dialled again once the number is
    corrected (only that exact reason)."""
    try:
        resp = db_client.table("leads").update({"status": "pending"}).eq(
            "id", contact_id,
        ).eq("tenant_id", tenant_id).eq(
            "campaign_id", campaign_id,
        ).eq("status", "skipped").eq(
            "last_call_result", "invalid_phone_number",
        ).execute()
        if getattr(resp, "error", None) is not None:
            _log_not_reset(campaign_id, "returned")
    except Exception as exc:  # noqa: BLE001 - the caller's answer stands
        _log_not_reset(campaign_id, type(exc).__name__)
