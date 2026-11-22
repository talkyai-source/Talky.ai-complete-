"""Permission to call: how many contacts are held back, and confirming the
contacts that have no list.

  GET  /campaigns/{campaign_id}/contact-permission
  POST /campaigns/{campaign_id}/contact-lists/ungrouped/permission

The POST records the customer's statement on a NEW confirmed list and moves
exactly the campaign's listless contacts into it, in ONE transaction. The
caller sends ``expected_count`` (the number it showed the person); if the number
moved differs, everything rolls back and the answer is 409, so the statement
only ever covers the contacts the person saw.

``queue_confirmed_contacts_if_running`` is also used by the list-confirm route
(contact_lists.py): when the switch is ``on`` and the campaign is ``running``,
the newly confirmed pending contacts are queued at once. It never restarts a
stopped or paused campaign (``only_lead_ids`` makes the start refuse anything
that is not running).
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.api.v1.endpoints._outbound_campaign import (
    outbound_campaign_conflict,
    require_owned_outbound_campaign,
)
from app.api.v1.endpoints.contact_lists import (
    ContactListOut,
    ContactListPermission,
    validate_for_route,
)
from app.api.v1.validation import ResourceUUID
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.rbac import Permission, require_permission
from app.domain.services.contact_permission import (
    CODE_INVALID,
    CONTACT_PERMISSION_STATEMENT_VERSION,
)
from app.domain.services.dialer.permission_gate import (
    contact_permission_dial_gate_mode,
    count_waiting_without_permission,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["contact-lists"])

MAX_EXPECTED_COUNT = 1_000_000


class ContactPermissionSummary(BaseModel):
    dial_gate: str
    statement_version: str
    waiting_without_permission: int
    ungrouped_without_permission: int
    queues_on_confirm: bool


class UngroupedPermissionOut(BaseModel):
    list: ContactListOut
    contacts_confirmed: int
    queued_for_calling: Optional[int] = None
    message: str

# $10 is the confirmation time (UTC), passed in so the statement is a plain
# single-row VALUES insert that binds this tenant; created_at takes its default.
_INSERT_LIST_SQL = (
    "INSERT INTO contact_lists (id, campaign_id, tenant_id, name, source, is_active, "
    "consent_confirmed_at, consent_confirmed_by, consent_basis, consent_source, "
    "consent_permission_date, consent_statement_version) "
    "VALUES ($3::uuid, $2::uuid, $1::uuid, $4, 'manual', TRUE, $10::timestamptz, "
    "$5::uuid, $6, $7, $8::date, $9) "
    "RETURNING id, name, source, is_active, created_at, consent_confirmed_at, "
    "consent_basis, consent_source, consent_permission_date"
)

_MOVE_SQL = (
    "UPDATE leads SET list_id = $3::uuid, updated_at = NOW() "
    "WHERE tenant_id = $1::uuid AND campaign_id = $2::uuid "
    "AND list_id IS NULL AND status <> 'deleted' RETURNING id"
)

_CAMPAIGN_STATUS_SQL = (
    "SELECT status FROM campaigns "
    "WHERE id = $2::uuid AND tenant_id = $1::uuid AND direction = 'outbound'"
)

_PENDING_IDS_SQL = (
    "SELECT l.id FROM leads AS l "
    "WHERE l.tenant_id = $1::uuid AND l.campaign_id = $2::uuid "
    "AND l.list_id = $3::uuid AND l.status = 'pending' "
    "AND l.do_not_call IS NOT TRUE"
)


async def queue_confirmed_contacts_if_running(
    db_client: Client, *, campaign_id: str, tenant_id: str, list_id: str,
) -> Tuple[str, int]:
    """Queue a confirmed list's pending contacts now, when that is safe.

    Returns ``("not_attempted", 0)`` unless the switch is ``on`` and the
    campaign is ``running``; ``("queued", n)`` after a start; ``("failed", 0)``
    when the start raised. The start runs with ``allow_running=True`` and
    ``only_lead_ids``, which refuses any campaign that is not running, so a
    stopped or paused campaign is never restarted by a confirmation.
    """
    if contact_permission_dial_gate_mode() != "on":
        return "not_attempted", 0
    try:
        async with acquire_with_tenant(db_client.pool, str(tenant_id)) as conn:
            status = await conn.fetchval(
                _CAMPAIGN_STATUS_SQL, str(tenant_id), str(campaign_id),
            )
            if status != "running":
                return "not_attempted", 0
            rows = await conn.fetch(
                _PENDING_IDS_SQL, str(tenant_id), str(campaign_id), str(list_id),
            )
        ids = [str(r["id"]) for r in rows]
        if not ids:
            return "queued", 0
        from app.api.v1.endpoints.campaigns import _get_campaign_service
        from app.domain.services.campaign_service import CampaignStateError

        try:
            result = await _get_campaign_service(db_client).start_campaign(
                campaign_id=str(campaign_id),
                tenant_id=str(tenant_id),
                allow_running=True,
                only_lead_ids=ids,
            )
        except CampaignStateError:
            # Stopped or completed between our read and the start: not an error.
            return "not_attempted", 0
        return "queued", int(result.jobs_enqueued or 0) + int(
            getattr(result, "jobs_dispatching", 0) or 0
        )
    except Exception as exc:  # noqa: BLE001 — the permission itself is already saved
        logger.warning(
            "contact_permission_queue_failed campaign=%s list=%s error=%s",
            str(campaign_id)[:8], str(list_id)[:8], exc,
        )
        return "failed", 0


@router.get(
    "/campaigns/{campaign_id}/contact-permission",
    response_model=ContactPermissionSummary,
    dependencies=[Depends(require_permission(Permission.CAMPAIGNS_READ))],
)
async def get_contact_permission_summary(
    campaign_id: ResourceUUID,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """How many waiting contacts have no recorded permission to call."""
    tenant = str(current_user.tenant_id)
    campaign = require_owned_outbound_campaign(
        db_client, campaign_id, tenant_id=tenant, extra_columns=("status",),
    )
    async with acquire_with_tenant(db_client.pool, tenant) as conn:
        waiting, ungrouped = await count_waiting_without_permission(
            conn, tenant, campaign_id,
        )
    mode = contact_permission_dial_gate_mode()
    return {
        "dial_gate": mode,
        "statement_version": CONTACT_PERMISSION_STATEMENT_VERSION,
        "waiting_without_permission": waiting,
        "ungrouped_without_permission": ungrouped,
        "queues_on_confirm": mode == "on" and campaign.get("status") == "running",
    }


def _expected_count(body: Any) -> int:
    value = body.get("expected_count") if isinstance(body, dict) else None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_EXPECTED_COUNT
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": CODE_INVALID,
                "field": "expected_count",
                "message": "Reload the page and confirm again.",
            },
        )
    return value


@router.post(
    "/campaigns/{campaign_id}/contact-lists/ungrouped/permission",
    response_model=UngroupedPermissionOut,
    dependencies=[Depends(require_permission(Permission.CAMPAIGNS_UPDATE))],
)
async def confirm_ungrouped_permission(
    campaign_id: ResourceUUID,
    body: Optional[dict] = Body(None),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
):
    """Record the customer's statement for the contacts that have no list."""
    permission = validate_for_route(body)
    expected = _expected_count(body)
    tenant = str(current_user.tenant_id)
    require_owned_outbound_campaign(db_client, campaign_id, tenant_id=tenant)

    now = datetime.now(timezone.utc)
    list_id = str(uuid.uuid4())
    name = f"Earlier contacts, confirmed {now:%Y-%m-%d %H:%M} UTC"
    try:
        async with acquire_with_tenant(
            db_client.pool, tenant, user_id=str(current_user.id),
        ) as conn:
            status = await conn.fetchval(
                _CAMPAIGN_STATUS_SQL, tenant, str(campaign_id),
            )
            if status is None or status == "deleted":
                raise HTTPException(
                    status_code=409, detail="The campaign changed; reload the page.",
                )
            created = await conn.fetchrow(
                _INSERT_LIST_SQL, tenant, str(campaign_id), list_id, name,
                str(current_user.id), permission.basis, permission.source,
                permission.permission_date, permission.statement_version, now,
            )
            moved = await conn.fetch(_MOVE_SQL, tenant, str(campaign_id), list_id)
            if len(moved) != expected:
                # Raised INSIDE the block: the list insert and the move roll back.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "contact_count_changed",
                        "message": (
                            f"The number of contacts without a list changed from "
                            f"{expected} to {len(moved)}. Check them and confirm again."
                        ),
                    },
                )
    except asyncpg.PostgresError as exc:
        if "outbound" in str(exc):
            raise outbound_campaign_conflict(str(campaign_id)) from exc
        logger.error(
            "ungrouped permission failed campaign=%s error=%s",
            str(campaign_id)[:8], type(exc).__name__,
        )
        raise HTTPException(
            status_code=503, detail="Permission could not be recorded; please try again.",
        ) from exc

    count = len(moved)
    outcome, queued = await queue_confirmed_contacts_if_running(
        db_client, campaign_id=str(campaign_id), tenant_id=tenant, list_id=list_id,
    )
    noun = "contact" if count == 1 else "contacts"
    message = (
        f"Permission recorded for {count} {noun}. They are now in the list "
        f"“{created['name']}”."
    )
    queued_out: Optional[int] = None
    if outcome == "queued":
        queued_out = queued
        if queued > 0:
            message += f" {queued} were queued for calling."
    elif outcome == "failed":
        message += " They could not be queued just now; use Call this list on that list."
    confirmed_at = created["consent_confirmed_at"]
    permission_date = created["consent_permission_date"]
    return {
        "list": ContactListOut(
            id=str(created["id"]),
            name=created["name"],
            source=created["source"] or "manual",
            is_active=bool(created["is_active"]),
            contact_count=count,
            created_at=str(created["created_at"]) if created["created_at"] else None,
            permission=ContactListPermission(
                confirmed=True,
                basis=created["consent_basis"],
                source=created["consent_source"],
                permission_date=permission_date.isoformat() if permission_date else None,
                confirmed_at=confirmed_at.isoformat() if confirmed_at else None,
            ),
        ),
        "contacts_confirmed": count,
        "queued_for_calling": queued_out,
        "message": message,
    }
