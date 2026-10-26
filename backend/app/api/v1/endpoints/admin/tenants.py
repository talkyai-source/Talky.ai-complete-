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
from datetime import datetime, timezone
from typing import List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_db_client,
    require_platform_admin,
)
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.platform_admin_guard import (
    lock_user_administration, require_current_platform_admin,
)
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
    updated_at: Optional[str] = None


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


class TenantVersionRequest(BaseModel):
    # Explicit null supports legacy rows with no timestamp; omission is unsafe.
    expected_updated_at: Optional[datetime] = Field(...)
    reason: Optional[str] = Field(None, max_length=1000)

    @field_validator("expected_updated_at")
    @classmethod
    def _timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("expected_updated_at must include a timezone")
        return value


class QuotaUpdateRequest(TenantVersionRequest):
    """Request model for updating tenant quota"""
    minutes_allocated: Optional[int] = Field(None, ge=0, le=2147483647)
    max_concurrent_calls: Optional[int] = Field(None, ge=1, le=1000)


class TenantCreateRequest(BaseModel):
    """Request model for provisioning a tenant."""
    business_name: str = Field(..., min_length=1, max_length=255)
    plan_id: str = Field("free", min_length=1, max_length=50)
    minutes_allocated: Optional[int] = Field(None, ge=0, le=2147483647)
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
    updated_at: Optional[str] = None
    audit_event_id: Optional[str] = None
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
           t.updated_at,
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
           t.created_at,
           t.updated_at
    FROM tenants t
    LEFT JOIN plans p ON p.id = t.plan_id
"""


def _subscription_status(value: Optional[str]) -> str:
    """Preserve the list/admission legacy fallback in every Admin response.

    This is a subscription display projection, not payment or call authority.
    Stored values, audit snapshots and formal account holds stay unchanged.
    """
    return value or "active"


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


async def _lock_operator_write(conn, audit_logger, admin_user):
    # Shared flusher -> authority writers -> target rows: never reverse this.
    await audit_logger.lock_transaction(conn)
    await lock_user_administration(conn)
    await require_current_platform_admin(conn, admin_user.id)


def _version(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.astimezone(timezone.utc) if value is not None else None


def _check_version(row, expected):
    if _version(row["updated_at"]) != _version(expected):
        raise HTTPException(status_code=409, detail={
            "code": "tenant_changed",
            "message": "Tenant changed. Refresh and review before applying this change.",
            "status": _subscription_status(row["subscription_status"]),
            "updated_at": row["updated_at"].isoformat() if isinstance(row["updated_at"], datetime) else row["updated_at"],
        })


def _create_response(row, *, created: bool, audit_event_id=None) -> TenantCreateResponse:
    rules = _calling_rules(row["calling_rules"])
    return TenantCreateResponse(
        id=row["id"],
        business_name=row["business_name"],
        plan_id=row["plan_id"],
        plan_name=row["plan_name"],
        minutes_allocated=row["minutes_allocated"] or 0,
        minutes_used=row["minutes_used"] or 0,
        max_concurrent_calls=_max_concurrent(rules),
        status=_subscription_status(row["subscription_status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        audit_event_id=str(audit_event_id) if audit_event_id else None,
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
        # The tenant UUID does not exist until this transaction inserts it;
        # this platform-admin provisioning transaction is intentionally global.
        async with acquire_with_tenant(db_client.pool, None) as conn:
            async with conn.transaction():
                await _lock_operator_write(conn, audit_logger, admin_user)
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
                              calling_rules, created_at, updated_at
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
                        RETURNING calling_rules, updated_at
                        """,
                        tenant["id"],
                        int(desired),
                    )
                    if updated:
                        rules = _calling_rules(updated["calling_rules"])
                        tenant = {**dict(tenant), "updated_at": updated["updated_at"]}
                    else:
                        raise RuntimeError("Tenant limits update was not persisted")

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

                audit_event_id = await audit_logger.log_in_transaction(
                    conn, event_type=AuditEvent.TENANT_CREATED,
                    actor_id=admin_user.id, actor_role="platform_admin",
                    tenant_id=tenant["id"], resource_type="tenant", resource_id=tenant["id"],
                    action="tenant_created",
                    after_state={"business_name": name, "plan_id": plan["id"],
                                 "minutes_allocated": int(minutes),
                                 "subscription_status": body.subscription_status,
                                 "calling_rules": rules},
                )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail="Tenant creation was not confirmed. Refresh before retrying.",
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
            "updated_at": tenant["updated_at"],
        },
        created=True,
        audit_event_id=audit_event_id,
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
    sort_by: Literal["name", "minutes_used"] = "name",
):
    """
    List tenants with plan, counts and status (platform_admin only).

    Query params:
        - search: Filter by business name
        - status: Filter by subscription_status (active, suspended, ...)
        - include_archived: Show archived (cancelled) tenants too
        - limit / offset: Pagination
        - sort_by: Stable name ordering or highest recorded minutes first

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
        # Filter the same projected value displayed in the returned rows.
        conditions.append(f"COALESCE(NULLIF(t.subscription_status, ''), 'active') = ${len(params)}")
    elif not include_archived:
        # Archived tenants drop out of the default view; an explicit status
        # filter (or include_archived) brings them back.
        params.append(ARCHIVED_STATUS)
        conditions.append(f"t.subscription_status IS DISTINCT FROM ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    order = "t.minutes_used DESC NULLS LAST, t.id" if sort_by == "minutes_used" else "t.business_name, t.id"

    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
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
                + f" {where} ORDER BY {order} "
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
                status=_subscription_status(row["subscription_status"]),
                user_count=row["user_count"] or 0,
                campaign_count=row["campaign_count"] or 0,
                max_concurrent_calls=_max_concurrent(
                    _calling_rules(row["calling_rules"])
                ),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail="Tenant data is temporarily unavailable."
        )


@router.get("/tenants/{tenant_id}", response_model=TenantDetailResponse)
async def get_tenant(
    tenant_id: UUID,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            row = await conn.fetchrow(_TENANT_LIST_SELECT + " WHERE t.id=$1", tenant_id)
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")
        data = dict(row)
        data["status"] = _subscription_status(data.pop("subscription_status"))
        data["calling_rules"] = _calling_rules(data["calling_rules"])
        data["max_concurrent_calls"] = _max_concurrent(data["calling_rules"])
        return TenantDetailResponse(**data)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Admin tenant detail read failed")
        raise HTTPException(status_code=503, detail="Tenant data is temporarily unavailable.")


def _mutation_receipt(row, *, detail, changed, audit_event_id=None):
    return {
        "id": str(row["id"]), "detail": detail, "changed": changed,
        "status": _subscription_status(row["subscription_status"]),
        "updated_at": row["updated_at"],
        "minutes_allocated": row["minutes_allocated"],
        "max_concurrent_calls": _max_concurrent(_calling_rules(row["calling_rules"])),
        "audit_event_id": str(audit_event_id) if audit_event_id else None,
    }


async def _mutate_tenant(
    db_client, tenant_id, body, admin_user, audit_logger, *,
    target=None, quota=None, action, event_type, detail,
):
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            await _lock_operator_write(conn, audit_logger, admin_user)
            current = await conn.fetchrow(
                "SELECT id,subscription_status,minutes_allocated,calling_rules,updated_at "
                "FROM tenants WHERE id=$1 FOR UPDATE", tenant_id,
            )
            if not current:
                raise HTTPException(status_code=404, detail="Tenant not found")
            rules = _calling_rules(current["calling_rules"])
            same = (
                current["subscription_status"] == target if target is not None else
                (quota.minutes_allocated is None or current["minutes_allocated"] == quota.minutes_allocated)
                and (quota.max_concurrent_calls is None or _max_concurrent(rules) == quota.max_concurrent_calls)
            )
            # A lost-response retry reaching the already-desired state is a
            # read acknowledgement. It never overwrites unrelated newer state.
            if same:
                return _mutation_receipt(current, detail="Requested values already saved", changed=False)
            _check_version(current, body.expected_updated_at)
            if target is not None:
                saved = await conn.fetchrow(
                    "UPDATE tenants SET subscription_status=$2, "
                    "updated_at=GREATEST(clock_timestamp(),updated_at+INTERVAL '1 microsecond') "
                    "WHERE id=$1 RETURNING id,subscription_status,minutes_allocated,calling_rules,updated_at",
                    tenant_id, target,
                )
            else:
                saved = await conn.fetchrow(
                    """UPDATE tenants SET minutes_allocated=COALESCE($2::int,minutes_allocated),
                       calling_rules=CASE WHEN $3::int IS NULL THEN calling_rules ELSE
                           jsonb_set(COALESCE(calling_rules,'{}'::jsonb),
                                     '{max_concurrent_calls}',to_jsonb($3::int)) END,
                       updated_at=GREATEST(clock_timestamp(),updated_at+INTERVAL '1 microsecond')
                       WHERE id=$1
                       RETURNING id,subscription_status,minutes_allocated,calling_rules,updated_at""",
                    tenant_id, quota.minutes_allocated, quota.max_concurrent_calls,
                )
            if not saved or saved["id"] != current["id"]:
                raise RuntimeError("Tenant update was not persisted")
            receipt = await audit_logger.log_in_transaction(
                conn, event_type=event_type, action=action,
                actor_id=admin_user.id, actor_role="platform_admin",
                tenant_id=tenant_id, resource_type="tenant", resource_id=tenant_id,
                before_state=dict(current), after_state=dict(saved),
                metadata={"reason": body.reason.strip()} if body.reason and body.reason.strip() else None,
            )
            payload = _mutation_receipt(saved, detail=detail, changed=True, audit_event_id=receipt)
        # Exiting the scope commits both mutation and audit; no success receipt
        # may escape a failed commit.
        return payload
    except HTTPException:
        raise
    except Exception:
        logger.exception("Admin tenant mutation was not confirmed: %s", action)
        raise HTTPException(
            status_code=503,
            detail="Tenant change was not confirmed. Refresh and review before retrying.",
        )


@router.patch("/tenants/{tenant_id}/quota")
async def update_tenant_quota(
    tenant_id: UUID, quota: QuotaUpdateRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    return await _mutate_tenant(
        db_client, tenant_id, quota, admin_user, audit_logger, quota=quota,
        action="tenant_quota_updated", event_type=AuditEvent.LIMITS_CHANGED, detail="Quota updated",
    )


@router.post("/tenants/{tenant_id}/suspend")
async def suspend_tenant(
    tenant_id: UUID, body: TenantVersionRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    return await _mutate_tenant(
        db_client, tenant_id, body, admin_user, audit_logger, target="suspended",
        action="tenant_suspended", event_type=AuditEvent.TENANT_SUSPENDED, detail="Tenant suspended",
    )


@router.post("/tenants/{tenant_id}/resume")
async def resume_tenant(
    tenant_id: UUID, body: TenantVersionRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    return await _mutate_tenant(
        db_client, tenant_id, body, admin_user, audit_logger, target="active",
        action="tenant_resumed", event_type=AuditEvent.TENANT_RESTORED, detail="Tenant resumed",
    )


@router.post("/tenants/{tenant_id}/archive")
async def archive_tenant(
    tenant_id: UUID, body: TenantVersionRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    return await _mutate_tenant(
        db_client, tenant_id, body, admin_user, audit_logger, target=ARCHIVED_STATUS,
        action="tenant_archived", event_type=AuditEvent.TENANT_UPDATED, detail="Tenant archived",
    )
