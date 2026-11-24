"""Protections the website's contact routes have, for the assistant's ``manage_lead``.

The website routes (``endpoints/campaigns.py``) refuse a duplicate number, revive an
earlier removed row (keeping its do-not-call flag), hand a contact back to the queue
after its number changes, and queue a contact on a running campaign. These helpers
give the assistant the same steps, in the same terms, without touching the routes.
"""
from __future__ import annotations

import logging
from typing import Any, List, Tuple

logger = logging.getLogger(__name__)

# Fixed sentences: the assistant never shows database text.
S3 = "The campaign's contacts could not be checked for this number, so nothing was added."
S4 = "This number is already a contact in this campaign."
S5 = "Another contact in this campaign already has this number."
S6 = "The campaign's contacts could not be checked for this number, so nothing was changed."
S7 = "The calls already queued for this contact could not be cancelled, so the number was not changed."
N1 = (
    "Queued calls for this contact could not be cancelled now. "
    "The contact is removed, so the dialler will not call it."
)
W1 = (
    "This number was removed from the campaign earlier and is marked do not call. "
    "It comes back marked do not call, so it will not be dialled."
)
W2 = (
    "This number was removed from the campaign earlier and is marked do not call. "
    "The contact takes that mark with the number, so it will not be dialled."
)


def rows_with_phone(db_client, *, tenant_id: str, campaign_id: str, phone: str) -> Tuple[List[Any], List[Any]]:
    """Return (live rows, soft-deleted rows) holding ``phone`` in this campaign.

    A returned database error raises: it is a failure, never "nothing found".
    """
    resp = (
        db_client.table("leads")
        .select("id, status, is_lead, do_not_call")
        .eq("campaign_id", campaign_id)
        .eq("tenant_id", tenant_id)
        .eq("phone_number", phone)
        .execute()
    )
    if getattr(resp, "error", None) is not None:
        raise RuntimeError("manage_lead: contact lookup failed")
    rows = resp.data or []
    live = [r for r in rows if r.get("status") != "deleted"]
    deleted = [r for r in rows if r.get("status") == "deleted"]
    return live, deleted


def revive_choice(deleted_rows: List[Any]) -> Tuple[str, bool]:
    """The id of the removed row to revive (a qualified lead first) and the do-not-call flag.

    The flag is True when ANY removed row for the number carries it, as on the website.
    """
    ordered = sorted(deleted_rows, key=lambda r: (0 if r.get("is_lead") else 1))
    return ordered[0]["id"], any(bool(r.get("do_not_call")) for r in deleted_rows)


def _log_reset_not_done(response, campaign_id: str) -> None:
    """The database layer RETURNS most failures: a returned error is logged like a raised one."""
    if getattr(response, "error", None) is not None:
        logger.warning(
            "manage_lead_requeue_status_not_reset campaign=%s error=returned",
            str(campaign_id)[:8],
        )


def release_called_contact(
    db_client, *, tenant_id: str, campaign_id: str, lead_id: str,
) -> None:
    """Make a contact whose queued calls were just cancelled selectable again. Never raises.

    A cancelled retry leaves the lead ``called``, which no Start selects. Only for a
    contact whose calls THIS change cancelled: a ``called`` contact with nothing
    queued has used its attempts and stays as it is.
    """
    try:
        response = db_client.table("leads").update({"status": "pending"}).eq(
            "id", lead_id,
        ).eq("tenant_id", tenant_id).eq(
            "campaign_id", campaign_id,
        ).eq("status", "called").execute()
        _log_reset_not_done(response, campaign_id)
    except Exception as exc:  # noqa: BLE001 - the contact change stands
        logger.warning(
            "manage_lead_requeue_status_not_reset campaign=%s error=%s",
            str(campaign_id)[:8], type(exc).__name__,
        )


def hand_back_after_phone_change(
    db_client, *, tenant_id: str, campaign_id: str, lead_id: str, cancelled: int,
) -> None:
    """After a saved phone change, make the contact selectable again. Never raises.

    A cancelled retry leaves the lead ``called``, which no Start selects; a contact
    skipped for an invalid number is dialled again once the number is corrected.
    """
    if cancelled:
        release_called_contact(
            db_client, tenant_id=tenant_id, campaign_id=campaign_id, lead_id=lead_id)
    try:
        response = db_client.table("leads").update({"status": "pending"}).eq(
            "id", lead_id,
        ).eq("tenant_id", tenant_id).eq(
            "campaign_id", campaign_id,
        ).eq("status", "skipped").eq(
            "last_call_result", "invalid_phone_number",
        ).execute()
        _log_reset_not_done(response, campaign_id)
    except Exception as exc:  # noqa: BLE001 - the contact change stands
        logger.warning(
            "manage_lead_requeue_status_not_reset campaign=%s error=%s",
            str(campaign_id)[:8], type(exc).__name__,
        )


async def queue_if_running(
    db_client, campaign: dict, *, tenant_id: str, campaign_id: str, lead_id: str,
    count_new: bool = False,
) -> None:
    """Queue the contact now while its campaign runs (never during its live call). Never raises."""
    try:
        # Lazy import: the API layer imports the tools package, so a module-level
        # import here would be circular (same as campaign_create.py).
        from app.api.v1.endpoints.campaigns import (
            _count_new_contact_if_running,
            _queue_new_contacts_if_running,
        )
        if count_new:
            await _count_new_contact_if_running(
                db_client, campaign, campaign_id, tenant_id)
        await _queue_new_contacts_if_running(
            db_client, campaign, campaign_id, tenant_id, str(lead_id))
    except Exception as exc:  # noqa: BLE001 - the contact write stands
        logger.warning(
            "manage_lead_queue_failed campaign=%s error=%s",
            str(campaign_id)[:8], type(exc).__name__,
        )


async def put_back_after_failed_save(
    db_client, campaign: dict, *, tenant_id: str, campaign_id: str, lead_id: str,
) -> None:
    """The save failed AFTER this contact's queued calls were cancelled. Never raises.

    Its number is unchanged and its calls are gone: without this it would stay
    ``called`` with nothing queued, which no Start selects. Make it selectable
    again and, on a running campaign, queue it (with the number it still has).
    """
    release_called_contact(
        db_client, tenant_id=tenant_id, campaign_id=campaign_id, lead_id=lead_id)
    await queue_if_running(
        db_client, campaign, tenant_id=tenant_id, campaign_id=campaign_id, lead_id=lead_id)
