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
from typing import Any, List, Optional

from fastapi import APIRouter, Body, HTTPException, Depends
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
from app.domain.services.contact_permission import (
    ContactPermission,
    ContactPermissionError,
    contact_permission_required,
    permission_required_error,
    validate_contact_permission,
)
from app.utils.tenant_filter import apply_tenant_filter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["contact-lists"])


# =============================================================================
# Schemas
# =============================================================================

class ContactListPermission(BaseModel):
    """The customer's recorded permission confirmation for a list."""
    confirmed: bool = False
    basis: Optional[str] = None
    source: Optional[str] = None
    permission_date: Optional[str] = None
    confirmed_at: Optional[str] = None


class ContactListOut(BaseModel):
    """A contact list as returned to the client."""
    id: str
    name: str
    source: str
    is_active: bool
    contact_count: int
    created_at: Optional[str] = None
    permission: ContactListPermission = ContactListPermission()


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


_CONSENT_COLUMNS = (
    "consent_confirmed_at, consent_basis, consent_source, consent_permission_date"
)
LIST_UNAVAILABLE_DETAIL = (
    "The contact list could not be saved, so no contacts were added. Please try again."
)


def resolve_contact_permission(raw: Any) -> Optional[ContactPermission]:
    """Validate a confirmation sent with an import, or answer 422.

    ``None`` means nothing was sent: 422 while the requirement is on, and
    ``None`` back (import as before) while it is off. A confirmation that IS
    sent is always validated.
    """
    try:
        if raw is None:
            if contact_permission_required():
                raise permission_required_error()
            return None
        return validate_contact_permission(raw)
    except ContactPermissionError as exc:
        raise HTTPException(status_code=422, detail=exc.detail()) from exc


def permission_from_form(
    basis: Optional[str],
    source: Optional[str],
    permission_date: Optional[str],
    statement_version: Optional[str],
) -> Optional[dict]:
    """Collect the four multipart fields; ``None`` when none was sent."""
    fields = {
        "basis": basis,
        "source": source,
        "permission_date": permission_date,
        "statement_version": statement_version,
    }
    if all(v is None or (isinstance(v, str) and not v.strip()) for v in fields.values()):
        return None
    return fields


def validate_for_route(raw: Any) -> ContactPermission:
    """Validate a confirmation that is mandatory for the route (422 otherwise)."""
    try:
        return validate_contact_permission(raw)
    except ContactPermissionError as exc:
        raise HTTPException(status_code=422, detail=exc.detail()) from exc


def list_display_name(
    db_client: Client, list_id: Optional[str], tenant_id: Optional[str], fallback: str,
) -> str:
    """The stored name of a list (a confirmed import may have been named "x (2)")."""
    if list_id is None:
        return fallback
    try:
        resp = db_client.table("contact_lists").select("id, name")\
            .eq("id", list_id).eq("tenant_id", tenant_id).execute()
        if getattr(resp, "data", None):
            return str(resp.data[0].get("name") or fallback)
    except Exception:  # noqa: BLE001 — cosmetic
        pass
    return fallback


def _same_confirmation(row: dict, permission: ContactPermission) -> bool:
    return (
        row.get("consent_basis") == permission.basis
        and str(row.get("consent_source") or "").strip() == permission.source
        and str(row.get("consent_permission_date") or "")[:10]
        == permission.permission_date.isoformat()
    )


def create_contact_list(
    db_client: Client,
    *,
    campaign_id: str,
    tenant_id: Optional[str],
    name: str,
    source: str,
    permission: Optional[ContactPermission] = None,
    confirmed_by: Optional[str] = None,
) -> Optional[str]:
    """Create (or reuse) a contact list for an upload and return its id.

    Dedup policy: if a list with the SAME (campaign_id, name) already exists,
    we reuse it and append the new leads into it rather than creating a
    duplicate-named list. Only an ACTIVE list is reused: appending to a list the
    user switched off would import contacts that start_campaign silently skips.
    This makes "re-upload the same file" idempotent from
    the user's perspective (the list's contact_count just grows). A caller that
    wants a distinct list should pass a distinct name.

    With a ``permission`` the list carries the customer's confirmation, and an
    active list is reused ONLY when its stored basis, source and permission date
    equal it. Otherwise (unconfirmed, or a different confirmation) the old list
    is left untouched and a new one is created as "<name> (2)", "(3)", ... so a
    confirmation is never spread over contacts it was not given for.

    Without a ``permission`` this is best-effort: on an ordinary list failure
    returns None so the caller falls back to an Ungrouped import (leads with
    list_id NULL). With a ``permission`` a list failure raises HTTP 503 so no
    contact is stored without its confirmation. A database direction guard
    always raises ``OutboundCampaignDirectionConflict`` so an API cannot claim
    an inbound campaign import partially succeeded.
    """
    name = (name or "").strip() or "Untitled list"
    src = source if source in ("csv", "paste", "manual") else "manual"
    try:
        if permission is None:
            existing = db_client.table("contact_lists").select("id")\
                .eq("campaign_id", campaign_id).eq("tenant_id", tenant_id)\
                .eq("name", name).eq("is_active", True).execute()
            if getattr(existing, "error", None):
                raise RuntimeError("contact-list lookup failed")
            if getattr(existing, "data", None):
                return str(existing.data[0]["id"])
            list_name = name
        else:
            rows_resp = db_client.table("contact_lists").select(
                "id, name, is_active, " + _CONSENT_COLUMNS
            ).eq("campaign_id", campaign_id).eq("tenant_id", tenant_id)\
                .eq("is_active", True).execute()
            if getattr(rows_resp, "error", None):
                raise RuntimeError("contact-list lookup failed")
            by_name: dict = {}
            for row in getattr(rows_resp, "data", None) or []:
                by_name.setdefault(row.get("name"), []).append(row)
            list_name = name
            number = 1
            while True:
                holders = by_name.get(list_name) or []
                for row in holders:
                    if _same_confirmation(row, permission):
                        return str(row["id"])
                if not holders:
                    break
                number += 1
                list_name = f"{name} ({number})"

        new_id = str(uuid.uuid4())
        row_out = {
            "id": new_id,
            "campaign_id": campaign_id,
            "tenant_id": tenant_id,
            "name": list_name,
            "source": src,
            "is_active": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if permission is not None:
            row_out.update({
                "consent_confirmed_at": datetime.now(timezone.utc).isoformat(),
                "consent_confirmed_by": confirmed_by,
                "consent_basis": permission.basis,
                "consent_source": permission.source,
                "consent_permission_date": permission.permission_date.isoformat(),
                "consent_statement_version": permission.statement_version,
            })
        inserted = db_client.table("contact_lists").insert(row_out).execute()
        if getattr(inserted, "error", None):
            raise_for_outbound_direction_guard(inserted.error, campaign_id)
            raise RuntimeError("contact-list insert was not applied")
        if not getattr(inserted, "data", None):
            raise RuntimeError("contact-list insert was not applied")
        return str(inserted.data[0].get("id") or new_id)
    except OutboundCampaignDirectionConflict:
        raise
    except Exception as exc:  # noqa: BLE001 — never fail an import over the list
        if permission is not None:
            logger.error(
                "create_contact_list failed for campaign %s (%r) with a permission "
                "confirmation: %s — import refused", campaign_id, name, exc,
            )
            raise HTTPException(status_code=503, detail=LIST_UNAVAILABLE_DETAIL) from exc
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
        "id, campaign_id, tenant_id, name, source, is_active, created_at, "
        + _CONSENT_COLUMNS
    ).eq("id", list_id)
    q = apply_tenant_filter(q, current_user.tenant_id)
    resp = q.execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Contact list not found")
    return resp.data[0]


def _permission_out(row: dict) -> ContactListPermission:
    """The stored confirmation as returned to the client (all null when none)."""
    confirmed_at = row.get("consent_confirmed_at")
    if not confirmed_at:
        return ContactListPermission()
    pdate = row.get("consent_permission_date")
    return ContactListPermission(
        confirmed=True,
        basis=row.get("consent_basis"),
        source=row.get("consent_source"),
        permission_date=str(pdate)[:10] if pdate else None,
        confirmed_at=str(confirmed_at),
    )


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

@router.get("/campaigns/{campaign_id}/contact-lists", response_model=List[ContactListOut],
            dependencies=[Depends(require_permission(Permission.CAMPAIGNS_READ))])
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
        "id, name, source, is_active, created_at, " + _CONSENT_COLUMNS
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
            permission=_permission_out(r),
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
        permission=_permission_out({**row, **updated}),
    )


# =============================================================================
# POST /contact-lists/{list_id}/permission
# =============================================================================

@router.post(
    "/contact-lists/{list_id}/permission",
    response_model=ContactListOut,
    dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))],
)
async def record_contact_list_permission(
    list_id: str,
    body: Optional[dict] = Body(None),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Record the permission confirmation on an older list that has none.

    Tenant-scoped like PATCH. A list that already has a confirmation answers 409
    and is never overwritten.
    """
    permission = validate_for_route(body)
    row = _fetch_list_or_404(db_client, list_id, current_user)
    campaign_id = str(row["campaign_id"])
    require_owned_outbound_campaign(
        db_client,
        campaign_id,
        tenant_id=current_user.tenant_id,
    )
    already = HTTPException(
        status_code=409,
        detail={
            "code": "contact_permission_already_recorded",
            "message": "Permission is already recorded for this list.",
        },
    )
    if row.get("consent_confirmed_at"):
        raise already

    upd = db_client.table("contact_lists").update({
        "consent_confirmed_at": datetime.now(timezone.utc).isoformat(),
        "consent_confirmed_by": current_user.id,
        "consent_basis": permission.basis,
        "consent_source": permission.source,
        "consent_permission_date": permission.permission_date.isoformat(),
        "consent_statement_version": permission.statement_version,
    }).eq("id", list_id).eq("campaign_id", campaign_id).is_("consent_confirmed_at", None)
    upd = apply_tenant_filter(upd, current_user.tenant_id)
    resp = upd.execute()
    write_error = getattr(resp, "error", None)
    if write_error:
        logger.error("contact_list permission failed list=%s error=%s", list_id, write_error)
        raise HTTPException(
            status_code=503,
            detail="Permission could not be recorded; please try again.",
        )
    if not getattr(resp, "data", None):
        # Someone recorded one between our read and the conditional update.
        raise already
    updated = resp.data[0]
    return ContactListOut(
        id=str(updated["id"]),
        name=updated.get("name") or "Untitled list",
        source=updated.get("source") or "manual",
        is_active=bool(updated.get("is_active", True)),
        contact_count=_live_count(
            db_client, campaign_id, list_id, tenant_id=current_user.tenant_id,
        ),
        created_at=str(updated.get("created_at")) if updated.get("created_at") else None,
        permission=_permission_out({**row, **updated}),
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
        withheld = int(getattr(result, "jobs_withheld", 0) or 0)
        if withheld:
            # CONTACT_PERMISSION_DIAL_GATE: say why contacts were not queued.
            from app.domain.services.campaign_service import CampaignService
            message += CampaignService._withheld_note(withheld)
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
