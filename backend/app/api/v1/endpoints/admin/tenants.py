"""
Admin Tenants Endpoints
Tenant management: create, list, detail, quota, suspend, resume, archive.

Why this file changed (goals.md §12, "200-Tenant Validation")
=============================================================
* There was **no create verb**, so the 200-tenant seeder had nothing to
  call and §12 sat at 0%. ``POST /admin/tenants`` mirrors the tenant row
  the signup path writes (``auth/registration.py`` and ``auth/signup.py``:
  same column list, plan-derived minutes) and reuses the same
  ``seed_platform_sip_trunk.seed_for_tenant`` hook, so an admin-created
  tenant is indistinguishable from a self-signed-up one.
* There was no archive verb. ``POST /tenants/{id}/archive`` is a soft
  state change to ``subscription_status = 'cancelled'`` — the spelling
  ``CallGuard._check_tenant_active`` / ``_check_subscription`` actually
  block on — so an archived tenant cannot place calls. No child rows are
  touched, and the existing ``/resume`` verb reverses it.
* The list endpoint issued ``1 + 2N`` queries (one page query plus a user
  count and a campaign count **per tenant** — 401 queries for 200
  tenants) and had no pagination. Both counts are now correlated
  subqueries in the page query, so the endpoint issues a constant 2
  statements: one COUNT for the total, one page SELECT.

The list response stays a bare JSON array — the Admin console consumes
``TenantListItem[]`` — so the total is returned in the ``X-Total-Count``
response header rather than by wrapping the body in an envelope.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_db_client,
    require_platform_admin,
)
from app.core.postgres_adapter import Client
from app.domain.services.audit_logger import AuditEvent, AuditLogger

from ._serialization import AdminResponseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# The subscription_status a tenant carries once archived. Two Ls on purpose:
# that is the literal CallGuard blocks on, so archiving really does stop the
# tenant dialling.
ARCHIVED_STATUS = "cancelled"

# Statuses an operator may seed a tenant with (goals.md §12 asks for active,
# trial, suspended, cancelled and overdue populations).
_SUBSCRIPTION_STATUS_PATTERN = (
    r"^(active|trialing|inactive|past_due|suspended|cancelled)$"
)

_DEFAULT_MAX_CONCURRENT_CALLS = 10


# =============================================================================
# Response Models
# =============================================================================

class TenantListItem(AdminResponseModel):
    """Enhanced tenant list item with counts and status"""
    id: str
    business_name: str
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    minutes_used: int
    minutes_allocated: int
    status: str  # subscription_status: active, suspended, inactive
    user_count: int
    campaign_count: int
    max_concurrent_calls: int
    created_at: Optional[str] = None


class TenantDetailResponse(AdminResponseModel):
    """Full tenant details response"""
    id: str
    business_name: str
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    minutes_used: int
    minutes_allocated: int
    status: str
    user_count: int
    campaign_count: int
    max_concurrent_calls: int
    calling_rules: Optional[dict] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class QuotaUpdateRequest(BaseModel):
    """Request model for updating tenant quota"""
    minutes_allocated: Optional[int] = None
    max_concurrent_calls: Optional[int] = None


class TenantCreateRequest(BaseModel):
    """Request model for provisioning a tenant."""
    business_name: str = Field(..., min_length=1, max_length=255)
    plan_id: str = Field("free", min_length=1, max_length=50)
    minutes_allocated: Optional[int] = Field(None, ge=0)
    max_concurrent_calls: Optional[int] = Field(None, ge=1, le=1000)
    subscription_status: str = Field("active", pattern=_SUBSCRIPTION_STATUS_PATTERN)

    @field_validator("business_name")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("business_name must not be blank")
        return stripped


class TenantCreateResponse(AdminResponseModel):
    """The provisioned tenant plus whether this call actually created it."""
    id: str
    business_name: str
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    minutes_allocated: int
    minutes_used: int
    max_concurrent_calls: int
    status: str
    created_at: Optional[str] = None
    # False when the natural key already existed and the existing tenant was
    # returned instead — lets a seeder tell a fresh run from a replay.
    created: bool


# =============================================================================
# Helpers
# =============================================================================

# Both per-tenant counts are correlated subqueries so the page costs one
# statement instead of 1 + 2N. They are evaluated only for the LIMITed rows.
_TENANT_LIST_SELECT = """
    SELECT t.id,
           t.business_name,
           t.plan_id,
           p.name AS plan_name,
           t.minutes_used,
           t.minutes_allocated,
           t.subscription_status,
           t.calling_rules,
           t.created_at,
           (SELECT count(*) FROM user_profiles up WHERE up.tenant_id = t.id)
               AS user_count,
           (SELECT count(*) FROM campaigns c WHERE c.tenant_id = t.id)
               AS campaign_count
    FROM tenants t
    LEFT JOIN plans p ON p.id = t.plan_id
"""

_TENANT_ROW_SELECT = """
    SELECT t.id,
           t.business_name,
           t.plan_id,
           p.name AS plan_name,
           t.minutes_allocated,
           t.minutes_used,
           t.subscription_status,
           t.calling_rules,
           t.created_at
    FROM tenants t
    LEFT JOIN plans p ON p.id = t.plan_id
"""


def _calling_rules(value) -> dict:
    """jsonb comes back as a dict via the pool codec; tolerate a raw string."""
    if isinstance(value, str):
        try:
            return json.loads(value) or {}
        except ValueError:
            return {}
    return value or {}


def _max_concurrent(rules: dict) -> int:
    try:
        return int(rules.get("max_concurrent_calls", _DEFAULT_MAX_CONCURRENT_CALLS))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_CONCURRENT_CALLS


def _natural_key_lock(business_name: str) -> int:
    """Stable advisory-lock key for a business name.

    ``tenants.business_name`` has no unique index, so idempotency is a
    check-then-insert. Two seeder workers racing on the same name would
    otherwise both miss the SELECT and insert twice; a transaction-scoped
    advisory lock on the name serialises them.
    """
    digest = hashlib.blake2b(
        business_name.strip().lower().encode("utf-8"), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


async def _safe_audit(audit_logger: AuditLogger, **kwargs) -> None:
    """Audit logging must never break the actual operation."""
    try:
        await audit_logger.log(**kwargs)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("audit log failed: %s", exc)


def _create_response(row, *, created: bool) -> TenantCreateResponse:
    rules = _calling_rules(row["calling_rules"])
    return TenantCreateResponse(
        id=row["id"],
        business_name=row["business_name"],
        plan_id=row["plan_id"],
        plan_name=row["plan_name"],
        minutes_allocated=row["minutes_allocated"] or 0,
        minutes_used=row["minutes_used"] or 0,
        max_concurrent_calls=_max_concurrent(rules),
        status=row["subscription_status"] or "active",
        created_at=row["created_at"],
        created=created,
    )


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/tenants", response_model=TenantCreateResponse, status_code=201)
async def create_tenant(
    body: TenantCreateRequest,
    response: Response,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Provision a tenant with its plan, minute quota and limits rows
    (platform_admin only).

    Idempotent on the natural key ``business_name`` (case-insensitive): a
    re-run of a seeder returns the existing tenant with ``created: false``
    and HTTP 200 instead of creating a duplicate. A genuine create returns
    201.
    """
    name = body.business_name.strip()

    try:
        async with db_client.pool.acquire() as conn:
            async with conn.transaction():
                plan = await conn.fetchrow(
                    "SELECT id, name, minutes, concurrent_calls "
                    "FROM plans WHERE id = $1",
                    body.plan_id,
                )
                if not plan:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unknown plan_id '{body.plan_id}'",
                    )

                # Serialise concurrent creates of the same natural key.
                await conn.fetchval(
                    "SELECT pg_advisory_xact_lock($1)", _natural_key_lock(name)
                )

                existing = await conn.fetchrow(
                    _TENANT_ROW_SELECT
                    + " WHERE lower(t.business_name) = lower($1) LIMIT 1",
                    name,
                )
                if existing:
                    response.status_code = 200
                    return _create_response(existing, created=False)

                minutes = (
                    body.minutes_allocated
                    if body.minutes_allocated is not None
                    else (plan["minutes"] or 0)
                )

                # Same column list the signup path writes.
                tenant = await conn.fetchrow(
                    """
                    INSERT INTO tenants
                        (business_name, plan_id, minutes_allocated,
                         minutes_used, subscription_status)
                    VALUES ($1, $2, $3, 0, $4)
                    RETURNING id, business_name, plan_id, minutes_allocated,
                              minutes_used, subscription_status,
                              calling_rules, created_at
                    """,
                    name,
                    plan["id"],
                    int(minutes),
                    body.subscription_status,
                )

                rules = _calling_rules(tenant["calling_rules"])
                desired = (
                    body.max_concurrent_calls
                    or plan["concurrent_calls"]
                    or _max_concurrent(rules)
                )
                if int(desired) != _max_concurrent(rules):
                    updated = await conn.fetchrow(
                        """
                        UPDATE tenants
                        SET calling_rules = jsonb_set(
                                COALESCE(calling_rules, '{}'::jsonb),
                                '{max_concurrent_calls}',
                                to_jsonb($2::int)),
                            updated_at = NOW()
                        WHERE id = $1
                        RETURNING calling_rules
                        """,
                        tenant["id"],
                        int(desired),
                    )
                    if updated:
                        rules = _calling_rules(updated["calling_rules"])

                # Default per-tenant limits row (emails/sms/calls per day).
                await conn.execute(
                    "INSERT INTO tenant_quotas (tenant_id) VALUES ($1) "
                    "ON CONFLICT (tenant_id) DO NOTHING",
                    tenant["id"],
                )

                # Same hook the signup path runs — never raises.
                from app.services.scripts.seed_platform_sip_trunk import (
                    seed_for_tenant,
                )
                await seed_for_tenant(conn, str(tenant["id"]))

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create tenant: {str(e)}",
        )

    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.TENANT_CREATED,
        actor_id=admin_user.id,
        actor_type="user",
        tenant_id=str(tenant["id"]),
        resource_type="tenant",
        resource_id=str(tenant["id"]),
        action="tenant_created",
        description=f"Admin created tenant '{name}' on plan {plan['id']}",
        metadata={
            "business_name": name,
            "plan_id": plan["id"],
            "minutes_allocated": int(minutes),
            "subscription_status": body.subscription_status,
        },
    )

    return _create_response(
        {
            "id": tenant["id"],
            "business_name": tenant["business_name"],
            "plan_id": tenant["plan_id"],
            "plan_name": plan["name"],
            "minutes_allocated": tenant["minutes_allocated"],
            "minutes_used": tenant["minutes_used"],
            "subscription_status": tenant["subscription_status"],
            "calling_rules": rules,
            "created_at": tenant["created_at"],
        },
        created=True,
    )


@router.get("/tenants", response_model=List[TenantListItem])
async def list_tenants(
    response: Response,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    search: Optional[str] = None,
    status: Optional[str] = None,
    include_archived: bool = Query(
        False, description="Include archived (cancelled) tenants"
    ),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """
    List tenants with plan, counts and status (platform_admin only).

    Query params:
        - search: Filter by business name
        - status: Filter by subscription_status (active, suspended, ...)
        - include_archived: Show archived (cancelled) tenants too
        - limit / offset: Pagination

    The total number of matching tenants (ignoring limit/offset) is returned
    in the ``X-Total-Count`` header.
    """
    conditions: list[str] = []
    params: list = []

    if search:
        params.append(f"%{search}%")
        conditions.append(f"t.business_name ILIKE ${len(params)}")

    if status:
        params.append(status)
        conditions.append(f"t.subscription_status = ${len(params)}")
    elif not include_archived:
        # Archived tenants drop out of the default view; an explicit status
        # filter (or include_archived) brings them back.
        params.append(ARCHIVED_STATUS)
        conditions.append(f"t.subscription_status IS DISTINCT FROM ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    try:
        async with db_client.pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT count(*) FROM tenants t {where}", *params
            )

            page_params = list(params)
            page_params.append(limit)
            limit_idx = len(page_params)
            page_params.append(offset)
            offset_idx = len(page_params)

            rows = await conn.fetch(
                _TENANT_LIST_SELECT
                + f" {where} ORDER BY t.business_name, t.id "
                + f"LIMIT ${limit_idx} OFFSET ${offset_idx}",
                *page_params,
            )

        response.headers["X-Total-Count"] = str(total or 0)

        return [
            TenantListItem(
                id=row["id"],
                business_name=row["business_name"],
                plan_id=row["plan_id"],
                plan_name=row["plan_name"],
                minutes_used=row["minutes_used"] or 0,
                minutes_allocated=row["minutes_allocated"] or 0,
                status=row["subscription_status"] or "active",
                user_count=row["user_count"] or 0,
                campaign_count=row["campaign_count"] or 0,
                max_concurrent_calls=_max_concurrent(
                    _calling_rules(row["calling_rules"])
                ),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch tenants: {str(e)}"
        )


@router.get("/tenants/{tenant_id}", response_model=TenantDetailResponse)
async def get_tenant(
    tenant_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Get a single tenant by ID with full details (admin only).
    """
    try:
        # Fetch tenant with plan info
        response = db_client.table("tenants").select(
            "*, plans(name)"
        ).eq("id", tenant_id).single().execute()

        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Tenant not found"
            )

        tenant = response.data

        # Get user count
        user_count_resp = db_client.table("user_profiles").select(
            "id", count="exact"
        ).eq("tenant_id", tenant_id).execute()
        user_count = user_count_resp.count or 0

        # Get campaign count
        campaign_count_resp = db_client.table("campaigns").select(
            "id", count="exact"
        ).eq("tenant_id", tenant_id).execute()
        campaign_count = campaign_count_resp.count or 0

        # Extract max_concurrent_calls from calling_rules
        calling_rules = tenant.get("calling_rules") or {}
        max_concurrent = calling_rules.get("max_concurrent_calls", 10)

        # Get plan name
        plan_data = tenant.get("plans") or {}
        plan_name = plan_data.get("name") if plan_data else None

        return TenantDetailResponse(
            id=tenant["id"],
            business_name=tenant["business_name"],
            plan_id=tenant.get("plan_id"),
            plan_name=plan_name,
            minutes_used=tenant.get("minutes_used", 0),
            minutes_allocated=tenant.get("minutes_allocated", 0),
            status=tenant.get("subscription_status", "active"),
            user_count=user_count,
            campaign_count=campaign_count,
            max_concurrent_calls=max_concurrent,
            calling_rules=calling_rules,
            created_at=tenant.get("created_at"),
            updated_at=tenant.get("updated_at")
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch tenant: {str(e)}"
        )


@router.patch("/tenants/{tenant_id}/quota")
async def update_tenant_quota(
    tenant_id: str,
    quota: QuotaUpdateRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)
):
    """
    Update tenant's quota (minutes allocated and/or max concurrent calls).
    """
    try:
        # First get current tenant data
        current = db_client.table("tenants").select(
            "calling_rules"
        ).eq("id", tenant_id).single().execute()

        if not current.data:
            raise HTTPException(
                status_code=404,
                detail="Tenant not found"
            )

        update_data = {}

        if quota.minutes_allocated is not None:
            update_data["minutes_allocated"] = quota.minutes_allocated

        if quota.max_concurrent_calls is not None:
            # Update calling_rules JSONB
            calling_rules = current.data.get("calling_rules") or {}
            calling_rules["max_concurrent_calls"] = quota.max_concurrent_calls
            update_data["calling_rules"] = calling_rules

        if not update_data:
            return {"detail": "No changes provided"}

        response = db_client.table("tenants").update(
            update_data
        ).eq("id", tenant_id).execute()

        if not response.data:
            raise HTTPException(
                status_code=404,
                detail="Tenant not found"
            )

        return {
            "detail": "Quota updated",
            "minutes_allocated": quota.minutes_allocated,
            "max_concurrent_calls": quota.max_concurrent_calls
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update quota: {str(e)}"
        )


async def _set_subscription_status(
    db_client: Client,
    tenant_id: str,
    *,
    target: str,
    already_detail: str,
) -> tuple[dict, bool]:
    """Shared body for suspend / resume / archive.

    Returns (payload, changed). ``changed`` is False when the tenant was
    already in the target state, so the caller can skip the audit row.
    """
    async with db_client.pool.acquire() as conn:
        current = await conn.fetchrow(
            "SELECT id, subscription_status FROM tenants WHERE id = $1",
            tenant_id,
        )
        if not current:
            raise HTTPException(status_code=404, detail="Tenant not found")

        if current["subscription_status"] == target:
            return {"detail": already_detail, "status": target}, False

        await conn.fetchrow(
            """
            UPDATE tenants
            SET subscription_status = $2, updated_at = NOW()
            WHERE id = $1
            RETURNING id, subscription_status
            """,
            tenant_id,
            target,
        )

    return {"status": target}, True


@router.post("/tenants/{tenant_id}/suspend")
async def suspend_tenant(
    tenant_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Suspend a tenant (sets subscription_status to 'suspended').
    Suspended tenants cannot make calls or use the platform.
    """
    try:
        payload, changed = await _set_subscription_status(
            db_client,
            tenant_id,
            target="suspended",
            already_detail="Tenant is already suspended",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to suspend tenant: {str(e)}"
        )

    if not changed:
        return payload

    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.TENANT_SUSPENDED,
        actor_id=admin_user.id,
        actor_type="user",
        tenant_id=tenant_id,
        resource_type="tenant",
        resource_id=tenant_id,
        action="tenant_suspended",
        description=f"Admin suspended tenant {tenant_id}",
    )
    return {"detail": "Tenant suspended", "status": "suspended"}


@router.post("/tenants/{tenant_id}/resume")
async def resume_tenant(
    tenant_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Resume a suspended or archived tenant (sets subscription_status to
    'active'). This is the un-do verb for both /suspend and /archive.
    """
    try:
        payload, changed = await _set_subscription_status(
            db_client,
            tenant_id,
            target="active",
            already_detail="Tenant is already active",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resume tenant: {str(e)}"
        )

    if not changed:
        return payload

    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.TENANT_RESTORED,
        actor_id=admin_user.id,
        actor_type="user",
        tenant_id=tenant_id,
        resource_type="tenant",
        resource_id=tenant_id,
        action="tenant_resumed",
        description=f"Admin resumed tenant {tenant_id}",
    )
    return {"detail": "Tenant resumed", "status": "active"}


@router.post("/tenants/{tenant_id}/archive")
async def archive_tenant(
    tenant_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Archive a tenant (sets subscription_status to 'cancelled').

    Reversible: ``POST /tenants/{id}/resume`` puts it back to 'active'.
    Nothing is deleted, so no child rows (users, campaigns, calls,
    recordings, invoices) are orphaned — retention/purge is a separate,
    deliberate operation. Archived tenants are hidden from the default
    tenant list and are blocked by CallGuard.
    """
    try:
        payload, changed = await _set_subscription_status(
            db_client,
            tenant_id,
            target=ARCHIVED_STATUS,
            already_detail="Tenant is already archived",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to archive tenant: {str(e)}"
        )

    if not changed:
        return payload

    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.TENANT_UPDATED,
        actor_id=admin_user.id,
        actor_type="user",
        tenant_id=tenant_id,
        resource_type="tenant",
        resource_id=tenant_id,
        action="tenant_archived",
        description=f"Admin archived tenant {tenant_id}",
        metadata={"subscription_status": ARCHIVED_STATUS},
    )
    return {"detail": "Tenant archived", "status": ARCHIVED_STATUS}
