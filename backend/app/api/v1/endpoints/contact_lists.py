"""
Contact Lists Endpoints

Group a campaign's leads into named, toggleable lists — one list per upload
(a CSV file or a paste). A campaign can turn a whole list on/off for dialing
and "call this list".

Backing schema: database/migrations/20260703_add_contact_lists.sql
  * contact_lists (id, campaign_id, tenant_id, name, source, is_active, created_at)
  * leads.list_id (nullable FK-by-convention → contact_lists.id)

Leads with list_id = NULL are "Ungrouped" and are always active/eligible, so
every lead that existed before this feature keeps dialing exactly as before.

Endpoints (all tenant-auth like the rest of contacts):
  GET   /campaigns/{campaign_id}/contact-lists
  PATCH /contact-lists/{list_id}          body {is_active: bool}
  POST  /contact-lists/{list_id}/call

The `create_contact_list` helper is imported by contacts.py so an upload/paste
creates (or reuses) exactly one list and tags every imported lead with it.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from app.core.postgres_adapter import Client
from app.api.v1.dependencies import get_db_client, get_current_user, CurrentUser
from app.api.v1.endpoints._outbound_campaign import (
    outbound_campaign_conflict,
    require_owned_outbound_campaign,
)
from app.core.security.rbac import Permission, require_permission
from app.domain.services.campaign_direction_guard import (
    OutboundCampaignDirectionConflict,
    raise_for_outbound_direction_guard,
)
from app.utils.tenant_filter import apply_tenant_filter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["contact-lists"])


# =============================================================================
# Schemas
# =============================================================================

class ContactListOut(BaseModel):
    """A contact list as returned to the client."""
    id: str
    name: str
    source: str
    is_active: bool
    contact_count: int
    created_at: Optional[str] = None


class ContactListToggle(BaseModel):
    """PATCH body — flip a list on or off for dialing."""
    is_active: bool


class CallListResponse(BaseModel):
    """POST /contact-lists/{id}/call result."""
    list_id: str
    is_active: bool
    eligible_count: int
    jobs_enqueued: int
    started: bool
    message: str


# =============================================================================
# Shared helper — used by contacts.py (CSV upload + paste import)
# =============================================================================

# Synthetic id used in the GET response for the Ungrouped bucket. Not a real
# row — clients must not PATCH/call it (the endpoints reject it).
UNGROUPED_ID = "ungrouped"


def create_contact_list(
    db_client: Client,
    *,
    campaign_id: str,
    tenant_id: Optional[str],
    name: str,
    source: str,
) -> Optional[str]:
    """Create (or reuse) a contact list for an upload and return its id.

    Dedup policy: if a list with the SAME (campaign_id, name) already exists,
    we reuse it and append the new leads into it rather than creating a
    duplicate-named list. This makes "re-upload the same file" idempotent from
    the user's perspective (the list's contact_count just grows). A caller that
    wants a distinct list should pass a distinct name.

    Best-effort: on an ordinary list failure returns None so the caller falls
    back to an Ungrouped import (leads with list_id NULL). A database direction
    guard is different: it raises ``OutboundCampaignDirectionConflict`` so an
    API cannot claim an inbound campaign import partially succeeded.
    """
    name = (name or "").strip() or "Untitled list"
    src = source if source in ("csv", "paste", "manual") else "manual"
    try:
        existing = db_client.table("contact_lists").select("id")\
            .eq("campaign_id", campaign_id).eq("tenant_id", tenant_id)\
            .eq("name", name).execute()
        if getattr(existing, "error", None):
            raise RuntimeError("contact-list lookup failed")
        if getattr(existing, "data", None):
            return str(existing.data[0]["id"])

        new_id = str(uuid.uuid4())
        inserted = db_client.table("contact_lists").insert({
            "id": new_id,
            "campaign_id": campaign_id,
            "tenant_id": tenant_id,
            "name": name,
            "source": src,
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        if getattr(inserted, "error", None):
            raise_for_outbound_direction_guard(inserted.error, campaign_id)
            raise RuntimeError("contact-list insert was not applied")
        if not getattr(inserted, "data", None):
            raise RuntimeError("contact-list insert was not applied")
        return str(inserted.data[0].get("id") or new_id)
    except OutboundCampaignDirectionConflict:
        raise
    except Exception as exc:  # noqa: BLE001 — never fail an import over the list
        logger.warning(
            "create_contact_list failed for campaign %s (%r): %s — importing as "
            "Ungrouped", campaign_id, name, exc,
        )
        return None


def default_paste_list_name(now: Optional[datetime] = None) -> str:
    """Name for a paste-created list: 'Pasted contacts <YYYY-MM-DD>'."""
    now = now or datetime.now(timezone.utc)
    return f"Pasted contacts {now.strftime('%Y-%m-%d')}"


# =============================================================================
# Internal: tenant-scoped list fetch + live counts
# =============================================================================

def _verify_campaign(db_client: Client, campaign_id: str, current_user: CurrentUser) -> None:
    """404 unless the campaign exists and belongs to the caller's tenant."""
    q = db_client.table("campaigns").select("id, tenant_id").eq("id", campaign_id)
    q = apply_tenant_filter(q, current_user.tenant_id)
    resp = q.execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Campaign not found")


def _fetch_list_or_404(db_client: Client, list_id: str, current_user: CurrentUser) -> dict:
    """Return the contact_lists row, tenant-scoped, or raise 404."""
    if list_id == UNGROUPED_ID:
        # Ungrouped is synthetic — it has no row to toggle or own.
        raise HTTPException(
            status_code=400,
            detail="The Ungrouped bucket is not a real list and cannot be toggled or called.",
        )
    q = db_client.table("contact_lists").select(
        "id, campaign_id, tenant_id, name, source, is_active, created_at"
    ).eq("id", list_id)
    q = apply_tenant_filter(q, current_user.tenant_id)
    resp = q.execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Contact list not found")
    return resp.data[0]


def _live_count(
    db_client: Client,
    campaign_id: str,
    list_id: Optional[str],
    *,
    tenant_id: Optional[str],
) -> int:
    """Count non-deleted leads for a list (or the Ungrouped/NULL bucket)."""
    q = db_client.table("leads").select("id", count="exact")\
        .eq("campaign_id", campaign_id).eq("tenant_id", tenant_id)\
        .neq("status", "deleted")
    if list_id is None:
        q = q.is_("list_id", None)
    else:
        q = q.eq("list_id", list_id)
    resp = q.execute()
    return resp.count or 0


# =============================================================================
# GET /campaigns/{campaign_id}/contact-lists
# =============================================================================

@router.get("/campaigns/{campaign_id}/contact-lists", response_model=List[ContactListOut])
async def list_contact_lists(
    campaign_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """List a campaign's contact lists with live contact counts.

    Includes a synthetic "Ungrouped" entry (id='ungrouped') for the leads that
    have no list_id, but only when at least one such lead exists. Ungrouped is
    always active and cannot be toggled/called.
    """
    _verify_campaign(db_client, campaign_id, current_user)

    q = db_client.table("contact_lists").select(
        "id, name, source, is_active, created_at"
    ).eq("campaign_id", campaign_id)
    q = apply_tenant_filter(q, current_user.tenant_id)
    resp = q.order("created_at", desc=True).execute()
    rows = resp.data or []

    out: List[ContactListOut] = []
    for r in rows:
        out.append(ContactListOut(
            id=str(r["id"]),
            name=r.get("name") or "Untitled list",
            source=r.get("source") or "manual",
            is_active=bool(r.get("is_active", True)),
            contact_count=_live_count(
                db_client,
                campaign_id,
                str(r["id"]),
                tenant_id=current_user.tenant_id,
            ),
            created_at=str(r.get("created_at")) if r.get("created_at") else None,
        ))

    ungrouped_count = _live_count(
        db_client,
        campaign_id,
        None,
        tenant_id=current_user.tenant_id,
    )
    if ungrouped_count > 0:
        out.append(ContactListOut(
            id=UNGROUPED_ID,
            name="Ungrouped",
            source="manual",
            is_active=True,
            contact_count=ungrouped_count,
            created_at=None,
        ))
    return out


# =============================================================================
# PATCH /contact-lists/{list_id}
# =============================================================================

@router.patch(
    "/contact-lists/{list_id}",
    response_model=ContactListOut,
    dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))],
)
async def toggle_contact_list(
    list_id: str,
    body: ContactListToggle,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Toggle a list active/inactive. Tenant-scoped.

    Turning a list OFF makes the dialer skip its leads on the next selection
    pass; it does NOT hang up calls already in flight.
    """
    row = _fetch_list_or_404(db_client, list_id, current_user)
    campaign_id = str(row["campaign_id"])
    require_owned_outbound_campaign(
        db_client,
        campaign_id,
        tenant_id=current_user.tenant_id,
    )

    upd = db_client.table("contact_lists").update(
        {"is_active": bool(body.is_active)}
    ).eq("id", list_id).eq("campaign_id", campaign_id)
    upd = apply_tenant_filter(upd, current_user.tenant_id)
    resp = upd.execute()
    write_error = getattr(resp, "error", None)
    if write_error:
        try:
            raise_for_outbound_direction_guard(write_error, campaign_id)
        except OutboundCampaignDirectionConflict as exc:
            raise outbound_campaign_conflict(
                campaign_id,
                message=(
                    "The campaign changed to inbound before the list was updated. "
                    "Use the inbound campaign lifecycle."
                ),
            ) from exc
        logger.error("contact_list toggle failed list=%s error=%s", list_id, write_error)
        raise HTTPException(
            status_code=503,
            detail="Contact-list update could not be confirmed",
        )
    if not getattr(resp, "data", None):
        raise HTTPException(
            status_code=409,
            detail="Contact list changed before update; reload and retry",
        )
    updated = resp.data[0]

    logger.info(
        "contact_list %s set is_active=%s (campaign=%s)",
        list_id, bool(body.is_active), row.get("campaign_id"),
    )
    return ContactListOut(
        id=str(updated["id"]),
        name=updated.get("name") or "Untitled list",
        source=updated.get("source") or "manual",
        is_active=bool(updated.get("is_active", True)),
        contact_count=_live_count(
            db_client,
            str(row["campaign_id"]),
            list_id,
            tenant_id=current_user.tenant_id,
        ),
        created_at=str(updated.get("created_at")) if updated.get("created_at") else None,
    )


# =============================================================================
# POST /contact-lists/{list_id}/call
# =============================================================================

@router.post(
    "/contact-lists/{list_id}/call",
    response_model=CallListResponse,
    dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))],
)
async def call_contact_list(
    list_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Activate a list and start dialing its eligible (pending) contacts.

    Reuses the campaign start/enqueue flow scoped to this list's leads
    (CampaignService.start_campaign(list_id=..., allow_running=True)), so the
    dialer picks up exactly this list's pending/calling leads — even if the
    campaign is already running. The active-job dedup in start_campaign keeps
    re-entry from double-dialing.
    """
    row = _fetch_list_or_404(db_client, list_id, current_user)
    campaign_id = str(row["campaign_id"])

    # A stale/hand-crafted list reference must not activate an inbound
    # campaign through the outbound dialer path.
    require_owned_outbound_campaign(
        db_client,
        campaign_id,
        tenant_id=current_user.tenant_id,
    )

    # 1. Activate the list (idempotent).
    upd = db_client.table("contact_lists").update({"is_active": True}).eq("id", list_id)
    upd = apply_tenant_filter(upd, current_user.tenant_id)
    activation = upd.eq("campaign_id", campaign_id).execute()
    activation_error = str(getattr(activation, "error", "") or "")
    if "contact_lists_outbound_campaign_guard" in activation_error:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "inbound_campaign_managed_separately",
                "message": (
                    "The campaign changed to inbound before the list was activated. "
                    "Use the inbound campaign lifecycle."
                ),
                "campaign_ids": [campaign_id],
            },
        )
    if activation_error:
        raise HTTPException(status_code=500, detail="Failed to activate contact list")
    if not getattr(activation, "data", None):
        raise HTTPException(
            status_code=409,
            detail="Contact list changed before activation; reload and retry",
        )

    # 2. Eligible = live pending/calling leads in this list.
    eligible = 0
    try:
        elig_resp = db_client.table("leads").select("id", count="exact")\
            .eq("campaign_id", campaign_id).eq("list_id", list_id)\
            .eq("tenant_id", current_user.tenant_id)\
            .in_("status", ["pending", "calling"]).execute()
        eligible = elig_resp.count or 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("eligible-count failed for list %s: %s", list_id, exc)

    # 3. Enqueue via the shared campaign start flow, scoped to this list.
    jobs_enqueued = 0
    started = False
    message = ""
    try:
        from app.api.v1.endpoints.campaigns import _get_campaign_service
        service = _get_campaign_service(db_client)
        result = await service.start_campaign(
            campaign_id=campaign_id,
            tenant_id=current_user.tenant_id,
            list_id=list_id,
            allow_running=True,
        )
        jobs_enqueued = result.jobs_enqueued
        started = True
        message = f"List activated — {jobs_enqueued} contact(s) queued for dialing."
    except HTTPException:
        # Preserve lifecycle conflicts (notably the outbound/inbound 409)
        # rather than returning an HTTP-200 partial-success envelope.
        raise
    except Exception as exc:  # noqa: BLE001 — surface partial success honestly
        logger.error("call_contact_list enqueue failed for %s: %s", list_id, exc)
        has_dispatch_counts = hasattr(exc, "jobs_enqueued") or hasattr(
            exc, "jobs_pending"
        )
        confirmed_jobs = max(0, int(getattr(exc, "jobs_enqueued", 0) or 0))
        pending_jobs = max(0, int(getattr(exc, "jobs_pending", 0) or 0))
        if has_dispatch_counts:
            message = (
                "The list was activated, but queue dispatch was not fully "
                "confirmed. Retry this action; pending durable jobs will "
                "be reconciled without creating a second job."
            )
        else:
            message = (
                "The list was activated, but its contacts were not queued. "
                "Retry this action; do not assume a later campaign start will dial them."
            )
        detail = {
            "error": "contact_list_start_failed_after_activation",
            "message": message,
            "list_id": list_id,
            "campaign_id": campaign_id,
            "is_active": True,
            "jobs_enqueued": confirmed_jobs,
        }
        if has_dispatch_counts:
            detail["jobs_pending"] = pending_jobs
        raise HTTPException(
            status_code=503,
            detail=detail,
        ) from exc

    return CallListResponse(
        list_id=list_id,
        is_active=True,
        eligible_count=eligible,
        jobs_enqueued=jobs_enqueued,
        started=started,
        message=message,
    )
