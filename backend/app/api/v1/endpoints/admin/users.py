"""
Admin Users Endpoints
User management: list, create, update (role / block), delete.

Mutations are restricted to platform_admin (the highest tier) so this screen
can't be used to escalate privileges (gap G1) — a tenant_admin cannot mint a
platform_admin here. Listing and single-user reads also require platform_admin
(P0-3): these queries span every tenant's users, so a tenant_admin must not be
able to read them.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_db_client,
    require_platform_admin,
)
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.password import hash_password
from app.domain.services.audit_logger import AuditEvent, AuditLogger

from ._serialization import AdminResponseModel

logger = logging.getLogger(__name__)

router = APIRouter()

_ROLE_PATTERN = r"^(platform_admin|partner_admin|tenant_admin|campaign_manager|user|agent|billing_user|readonly)$"


# =============================================================================
# Models
# =============================================================================

class AdminUserItem(AdminResponseModel):
    """Rich user row for the Users & Roles screen."""
    id: str
    email: str
    name: Optional[str] = None
    role: str
    # role is an assignment (historical for missing membership), not access proof.
    effective_role: Optional[str] = None
    membership_status: Optional[str] = None
    tenant_id: Optional[str] = None
    tenant_name: Optional[str] = None
    is_active: bool = True
    mfa_enabled: bool = False
    is_verified: bool = False
    last_login_at: Optional[str] = None
    created_at: Optional[str] = None


class CreateUserRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)
    role: str = Field("user", pattern=_ROLE_PATTERN)
    tenant_id: Optional[str] = None


class UpdateUserRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    role: Optional[str] = Field(None, pattern=_ROLE_PATTERN)
    is_active: Optional[bool] = None


# =============================================================================
# Helpers
# =============================================================================

_EFFECTIVE_ROLE_SQL = """CASE
    WHEN NOT COALESCE(up.is_active,FALSE) OR NOT COALESCE(up.is_verified,FALSE) THEN NULL
    WHEN up.role='platform_admin' THEN 'platform_admin'
    WHEN tu.status='active' AND r.name IN
         ('partner_admin','tenant_admin','campaign_manager','user','agent','billing_user','readonly')
    THEN r.name ELSE NULL END"""

_USER_SELECT = f"""
    SELECT up.id, up.email, up.name,
           CASE WHEN up.role='platform_admin' THEN up.role
                ELSE COALESCE(r.name,up.role) END AS role,
           {_EFFECTIVE_ROLE_SQL} AS effective_role,
           CASE WHEN up.role='platform_admin' THEN 'not_required'
                ELSE COALESCE(tu.status,'missing') END AS membership_status,
           up.tenant_id,
           t.business_name AS tenant_name,
           up.is_active, up.mfa_enabled, up.is_verified,
           up.last_login_at, up.created_at
    FROM   user_profiles up
    LEFT   JOIN tenants t ON t.id = up.tenant_id
    LEFT   JOIN tenant_users tu ON tu.user_id=up.id AND tu.tenant_id=up.tenant_id
    LEFT   JOIN roles r ON r.id=tu.role_id
"""


async def _fetch_user(conn, user_id: str) -> Optional[AdminUserItem]:
    row = await conn.fetchrow(_USER_SELECT + " WHERE up.id = $1", user_id)
    return AdminUserItem(**dict(row)) if row else None


async def _count_active_platform_admins(conn, *, exclude_id: Optional[str] = None) -> int:
    if exclude_id:
        return await conn.fetchval(
            "SELECT count(*) FROM user_profiles "
            "WHERE role = 'platform_admin' AND is_active = true AND id <> $1",
            exclude_id,
        )
    return await conn.fetchval(
        "SELECT count(*) FROM user_profiles "
        "WHERE role = 'platform_admin' AND is_active = true"
    )


async def _safe_audit(audit_logger: AuditLogger, **kwargs) -> None:
    """Audit logging must never break the actual operation."""
    try:
        await audit_logger.log(**kwargs)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("audit log failed: %s", exc)


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/users", response_model=List[AdminUserItem])
async def list_users(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    search: Optional[str] = Query(None, description="Match email or name"),
    role: Optional[str] = Query(None, description="Filter by current effective role"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List users with role, tenant, status, MFA and timestamps (admin only)."""
    conditions: list[str] = []
    params: list = []

    if search:
        params.append(f"%{search}%")
        idx = len(params)
        conditions.append(f"(up.email ILIKE ${idx} OR up.name ILIKE ${idx})")
    if role:
        params.append(role)
        conditions.append(f"({_EFFECTIVE_ROLE_SQL}) = ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    limit_idx = len(params)
    params.append(offset)
    offset_idx = len(params)

    query = (
        _USER_SELECT
        + f" {where} ORDER BY up.created_at DESC NULLS LAST, up.email "
        + f"LIMIT ${limit_idx} OFFSET ${offset_idx}"
    )

    # This platform-admin directory intentionally spans every tenant.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        rows = await conn.fetch(query, *params)

    return [AdminUserItem(**dict(r)) for r in rows]


@router.get("/users/{user_id}", response_model=AdminUserItem)
async def get_user(
    user_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    """Get a single user by id (admin only)."""
    # The platform-admin route resolves an ID before its tenant is known.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        user = await _fetch_user(conn, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/users", response_model=AdminUserItem, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Create a user with an admin-set temporary password (platform_admin only).

    The account is created verified + active so the user can sign in immediately
    with the password you set and change it afterwards.
    """
    email = body.email.strip().lower()
    name = body.name.strip()
    if body.role != "platform_admin" and not body.tenant_id:
        raise HTTPException(status_code=400, detail="Select a tenant for this account role.")
    pw_hash = hash_password(body.password)

    # Platform admins may create a tenant-less platform user; otherwise scope
    # every tenant-owned read/write to the explicitly requested tenant.
    async with acquire_with_tenant(
        db_client.pool, str(body.tenant_id) if body.tenant_id else None
    ) as conn:
        if body.tenant_id:
            tenant = await conn.fetchrow("SELECT id FROM tenants WHERE id = $1", body.tenant_id)
            if not tenant:
                raise HTTPException(status_code=404, detail="Tenant not found")

        role_id = None
        if body.role != "platform_admin":
            role_id = await conn.fetchval("SELECT id FROM roles WHERE name=$1", body.role)
            if role_id is None:
                raise HTTPException(status_code=503, detail="Account role configuration is unavailable.")
        existing = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE lower(email) = $1", email
        )
        if existing:
            raise HTTPException(status_code=409, detail="A user with this email already exists")

        try:
            new_id = await conn.fetchval(
                """
                INSERT INTO user_profiles
                    (email, name, role, password_hash, tenant_id,
                     is_active, is_verified, mfa_enabled,
                     email_verified_at, password_changed_at)
                VALUES (lower($1), $2, $3, $4, $5, true, true, false, NOW(), NOW())
                RETURNING id
                """,
                email, name, body.role, pw_hash, body.tenant_id,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail="A user with this email already exists")

        if role_id is not None:
            await conn.execute(
                """INSERT INTO tenant_users
                   (user_id,tenant_id,role_id,is_primary,status,joined_at)
                   VALUES($1,$2,$3,TRUE,'active',NOW())""",
                new_id, body.tenant_id, role_id,
            )
        user = await _fetch_user(conn, str(new_id))

    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.USER_CREATED,
        actor_id=admin_user.id,
        actor_type="user",
        tenant_id=body.tenant_id,
        resource_type="user",
        resource_id=str(new_id),
        action="user_created",
        description=f"Admin created user {email} with role {body.role}",
        metadata={"email": email, "role": body.role},
    )
    return user


@router.patch("/users/{user_id}", response_model=AdminUserItem)
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Update a user's name, role, or active status (block/unblock) — platform_admin only."""
    if body.name is None and body.role is None and body.is_active is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    # The platform-admin route resolves an ID before its tenant is known.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        current = await conn.fetchrow(
            "SELECT id, role, is_active, tenant_id FROM user_profiles WHERE id = $1 FOR UPDATE", user_id
        )
        if not current:
            raise HTTPException(status_code=404, detail="User not found")

        is_self = str(current["id"]) == str(admin_user.id)

        # Self-protection: can't block or demote yourself out of platform_admin.
        if is_self and body.is_active is False:
            raise HTTPException(status_code=400, detail="You cannot block your own account")
        if is_self and body.role is not None and body.role != current["role"]:
            raise HTTPException(status_code=400, detail="You cannot change your own role")

        # Don't strand the platform: keep at least one active platform_admin.
        demoting = (
            current["role"] == "platform_admin"
            and (
                (body.role is not None and body.role != "platform_admin")
                or body.is_active is False
            )
        )
        if demoting and await _count_active_platform_admins(conn, exclude_id=user_id) == 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove the last active platform admin",
            )

        if body.role is not None and body.role != "platform_admin":
            # Existing admin role edits must change the same membership used by
            # current authorization. Do not silently create or reactivate a
            # historical missing/suspended membership as a side effect.
            role_id = await conn.fetchval("SELECT id FROM roles WHERE name=$1", body.role)
            if role_id is None:
                raise HTTPException(status_code=503, detail="Account role configuration is unavailable.")
            updated = await conn.fetchval(
                """UPDATE tenant_users SET role_id=$1,updated_at=NOW()
                   WHERE user_id=$2 AND tenant_id=$3 AND status='active' RETURNING id""",
                role_id, user_id, current["tenant_id"],
            )
            if updated is None:
                raise HTTPException(status_code=409, detail="Resolve this account's active tenant membership before changing its role.")

        sets: list[str] = []
        values: list = []
        if body.name is not None:
            values.append(body.name.strip())
            sets.append(f"name = ${len(values)}")
        if body.role is not None:
            values.append(body.role)
            sets.append(f"role = ${len(values)}")
        if body.is_active is not None:
            values.append(body.is_active)
            sets.append(f"is_active = ${len(values)}")

        sets.append("updated_at = NOW()")
        values.append(user_id)
        await conn.execute(
            f"UPDATE user_profiles SET {', '.join(sets)} WHERE id = ${len(values)}",
            *values,
        )
        user = await _fetch_user(conn, user_id)

    # Pick the most descriptive audit event.
    if body.is_active is False:
        event = AuditEvent.USER_SUSPENDED
    elif body.is_active is True:
        event = AuditEvent.USER_RESTORED
    else:
        event = AuditEvent.USER_UPDATED
    await _safe_audit(
        audit_logger,
        event_type=event,
        actor_id=admin_user.id,
        actor_type="user",
        resource_type="user",
        resource_id=user_id,
        action="user_updated",
        description=f"Admin updated user {user.email if user else user_id}",
        metadata={
            "new_role": body.role,
            "is_active": body.is_active,
            "name_changed": body.name is not None,
        },
    )
    return user


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Delete a user (platform_admin only).

    Attempts a hard delete. If the user is referenced by other records
    (calls, audit logs, …) the FK constraint makes a hard delete unsafe, so we
    fall back to deactivating the account and report that in the response.
    """
    # The platform-admin route resolves an ID before its tenant is known.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        current = await conn.fetchrow(
            "SELECT id, email, role FROM user_profiles WHERE id = $1", user_id
        )
        if not current:
            raise HTTPException(status_code=404, detail="User not found")

        if str(current["id"]) == str(admin_user.id):
            raise HTTPException(status_code=400, detail="You cannot delete your own account")

        if current["role"] == "platform_admin" and await _count_active_platform_admins(
            conn, exclude_id=user_id
        ) == 0:
            raise HTTPException(
                status_code=400, detail="Cannot delete the last active platform admin"
            )

        # tenant_users membership is owned by this user — clearing it is safe and
        # removes the most common FK blocker before the hard delete. Wrap both in
        # one transaction so an FK fallback rolls the membership delete back too.
        try:
            async with conn.transaction():
                await conn.execute("DELETE FROM tenant_users WHERE user_id = $1", user_id)
                await conn.execute("DELETE FROM user_profiles WHERE id = $1", user_id)
            hard_deleted = True
        except asyncpg.ForeignKeyViolationError:
            # Has linked records elsewhere — deactivate instead of orphaning them.
            await conn.execute(
                "UPDATE user_profiles SET is_active = false, updated_at = NOW() WHERE id = $1",
                user_id,
            )
            hard_deleted = False

    await _safe_audit(
        audit_logger,
        event_type=AuditEvent.USER_DELETED,
        actor_id=admin_user.id,
        actor_type="user",
        resource_type="user",
        resource_id=user_id,
        action="user_deleted" if hard_deleted else "user_deactivated",
        description=f"Admin {'deleted' if hard_deleted else 'deactivated'} user {current['email']}",
        metadata={"hard_deleted": hard_deleted},
    )

    if hard_deleted:
        return {"detail": "User deleted successfully"}
    return {
        "detail": "User had linked records and was deactivated instead of hard-deleted."
    }
