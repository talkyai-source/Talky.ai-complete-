"""Tenant-membership endpoints (4 routes).

  GET    /rbac/tenant-users          - list members
  POST   /rbac/tenant-users          - add user to tenant
  PATCH  /rbac/tenant-users/{id}     - update role / status / primary flag
  DELETE /rbac/tenant-users/{id}     - soft-delete (status=removed)

NOTE: `status` (the FastAPI status-codes module) is shadowed by the
`status` query param inside `list_tenant_members`; this is intentional
and matches the pre-split file's behaviour.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.dependencies import (
    CurrentUser,
    UserRole,
    get_audit_logger,
    get_db_client,
    get_current_user,
    require_role,
)
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.rbac import normalize_role, get_user_role_in_tenant
from app.domain.services.audit_logger import AuditEvent, AuditLogger

from .schemas import (
    AddTenantUserRequest,
    RoleResponse,
    TenantMemberResponse,
    TenantUserResponse,
    UpdateTenantUserRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rbac"])


async def _current_target_admin(conn, current_user, tenant_id):
    """Recheck the exact target membership before membership mutations."""
    if normalize_role(current_user.role) == UserRole.PLATFORM_ADMIN:
        return UserRole.PLATFORM_ADMIN
    role = await get_user_role_in_tenant(conn, current_user.id, str(tenant_id))
    if role not in {UserRole.TENANT_ADMIN, UserRole.PARTNER_ADMIN}:
        raise HTTPException(status_code=403, detail="Current administrator access to this tenant is required")
    return role


def _check_role_ceiling(actor_role, target_role):
    if target_role and normalize_role(target_role, strict=True).level > actor_role.level:
        raise HTTPException(status_code=403, detail="Cannot modify a member above your own tier")


def _membership_state(row):
    if not row:
        return None
    return {name: row[name] for name in
            ("id", "user_id", "tenant_id", "role_id", "is_primary", "status")}


# QA F1-15: the Team screen's limits, held here for every route.
# is_primary marks a member's home workspace (one per person,
# idx_tu_user_primary), not an owner; only the platform team changes it.
_HOME_WORKSPACE_DETAIL = "Only the platform team can change a member's home workspace."
# The roles that administer a workspace: what _current_target_admin accepts.
_WORKSPACE_ADMIN_ROLES = (UserRole.TENANT_ADMIN.value, UserRole.PARTNER_ADMIN.value)


def _refuse_own_membership(row, current_user):
    # A slip would leave the workspace without an administrator; another one does it.
    if row and str(row["user_id"]) == str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own membership. Ask another administrator.",
        )


async def _keep_an_administrator(conn, row, *, role_name=None, new_status=None, removing=False):
    """Refuse a change that would leave the workspace with no active administrator."""
    if row["status"] != "active" or row["current_role"] not in _WORKSPACE_ADMIN_ROLES:
        return
    if not removing and (role_name is None or role_name in _WORKSPACE_ADMIN_ROLES) and (
        new_status is None or new_status == "active"
    ):
        return
    others = await conn.fetch(
        """
        SELECT tu.id FROM tenant_users tu
        JOIN roles r ON r.id = tu.role_id
        WHERE tu.tenant_id = $1 AND tu.id <> $2
          AND tu.status = 'active' AND r.name = ANY($3::text[])
        FOR UPDATE OF tu
        """,
        row["tenant_id"],
        row["id"],
        list(_WORKSPACE_ADMIN_ROLES),
    )
    if not others:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This is the workspace's last active administrator. Make someone else an administrator first.",
        )


@router.get("/tenant-users", response_model=List[TenantMemberResponse])
async def list_tenant_members(
    tenant_id: Optional[str] = Query(None, description="Tenant ID (defaults to user's tenant)"),
    status: Optional[str] = Query("active", pattern="^(pending|active|suspended|removed)$"),
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> List[TenantMemberResponse]:
    """
    List all members of a tenant.

    **Requires tenant membership.**
    Tenant admins+ can see all members; regular users can see active members only.
    """
    # Determine tenant to query
    query_tenant_id = tenant_id or current_user.tenant_id
    if not query_tenant_id:
        raise HTTPException(status_code=400, detail="Tenant ID required")

    async with acquire_with_tenant(db_client.pool, str(query_tenant_id)) as conn:
        # Authorize the exact queried tenant, not a different header/default.
        user_role = normalize_role(current_user.role)
        if user_role != UserRole.PLATFORM_ADMIN:
            user_role = await get_user_role_in_tenant(conn, current_user.id, str(query_tenant_id))
            if user_role is None:
                raise HTTPException(status_code=403, detail="Access denied to this tenant")
        is_admin = user_role in {UserRole.TENANT_ADMIN, UserRole.PARTNER_ADMIN, UserRole.PLATFORM_ADMIN}
        effective_status = [status] if is_admin else ["active"]
        rows = await conn.fetch(
            """
            SELECT
                tu.id,
                up.id as user_id,
                up.email,
                up.name,
                r.name as role_name,
                tu.is_primary,
                tu.status,
                tu.joined_at
            FROM tenant_users tu
            JOIN user_profiles up ON up.id = tu.user_id
            JOIN roles r ON r.id = tu.role_id
            WHERE tu.tenant_id = $1
              AND tu.status = ANY($2)
            ORDER BY tu.is_primary DESC, up.email
            """,
            query_tenant_id,
            effective_status,
        )

    return [
        TenantMemberResponse(
            id=str(row["id"]),
            email=row["email"],
            name=row["name"],
            role=row["role_name"],
            is_primary=row["is_primary"],
            status=row["status"],
            joined_at=row["joined_at"].isoformat() if row["joined_at"] else None,
        )
        for row in rows
    ]


@router.post(
    "/tenant-users",
    response_model=TenantUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_user_to_tenant(
    request: AddTenantUserRequest,
    current_user: CurrentUser = Depends(require_role(UserRole.TENANT_ADMIN)),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> TenantUserResponse:
    """
    Add a user to a tenant with a specific role.

    **Requires tenant_admin role for the target tenant.**
    """
    # Can only add to own tenant (unless platform admin)
    user_role = normalize_role(current_user.role)
    if user_role != UserRole.PLATFORM_ADMIN:
        if str(request.tenant_id) != str(current_user.tenant_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Can only add users to your own tenant",
            )

    # Escalation ceiling: can't grant a role above your own level.
    requested_role = normalize_role(request.role_name)
    if requested_role.level > user_role.level:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot grant a role above your own tier",
        )
    if request.is_primary and user_role != UserRole.PLATFORM_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_HOME_WORKSPACE_DETAIL)

    async with acquire_with_tenant(db_client.pool, str(request.tenant_id)) as conn:
        await audit_logger.lock_transaction(conn)
        user_role = await _current_target_admin(conn, current_user, request.tenant_id)
        _check_role_ceiling(user_role, request.role_name)
        before = await conn.fetchrow(
            """SELECT tu.*, r.name AS current_role FROM tenant_users tu JOIN roles r ON r.id=tu.role_id
               WHERE tu.user_id=$1 AND tu.tenant_id=$2 FOR UPDATE OF tu""",
            request.user_id, request.tenant_id,
        )
        _check_role_ceiling(user_role, before["current_role"] if before else None)
        # An existing membership is re-activated with the requested role.
        _refuse_own_membership(before, current_user)
        if before:
            await _keep_an_administrator(conn, before, role_name=request.role_name, new_status="active")
        # Verify user exists
        user_row = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE id = $1",
            request.user_id,
        )
        if not user_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )

        # Verify tenant exists
        tenant_row = await conn.fetchrow(
            "SELECT id FROM tenants WHERE id = $1",
            request.tenant_id,
        )
        if not tenant_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            )

        # Get role ID
        role_row = await conn.fetchrow(
            "SELECT id FROM roles WHERE name = $1",
            request.role_name,
        )
        if not role_row:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role: {request.role_name}",
            )

        # Insert tenant user
        try:
            result = await conn.fetchrow(
                """
                INSERT INTO tenant_users
                    (user_id, tenant_id, role_id, is_primary, status, invited_by, invited_at)
                VALUES ($1, $2, $3, $4, 'active', $5, NOW())
                ON CONFLICT (user_id, tenant_id) DO UPDATE SET
                    role_id = EXCLUDED.role_id,
                    status = 'active',
                    updated_at = NOW()
                RETURNING id, user_id, tenant_id, role_id, is_primary, status, invited_at, joined_at
                """,
                request.user_id,
                request.tenant_id,
                role_row["id"],
                request.is_primary,
                current_user.id,
            )
        except Exception as e:
            logger.error(f"Failed to add user to tenant: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to add user to tenant",
            )

        # Get role details for response
        role_details = await conn.fetchrow(
            "SELECT id, name, description, level, is_system_role, tenant_scoped FROM roles WHERE id = $1",
            result["role_id"],
        )
        if _membership_state(before) != _membership_state(result):
            await audit_logger.log_in_transaction(
                conn, event_type=AuditEvent.USER_UPDATED, action="rbac.member.add",
                actor_id=current_user.id, actor_role=current_user.role,
                tenant_id=result["tenant_id"], resource_type="tenant_user", resource_id=result["id"],
                before_state=_membership_state(before), after_state=_membership_state(result),
            )

    return TenantUserResponse(
        id=str(result["id"]),
        user_id=str(result["user_id"]),
        tenant_id=str(result["tenant_id"]),
        role=RoleResponse(
            id=str(role_details["id"]),
            name=role_details["name"],
            description=role_details["description"],
            level=role_details["level"],
            is_system_role=role_details["is_system_role"],
            tenant_scoped=role_details["tenant_scoped"],
        ),
        is_primary=result["is_primary"],
        status=result["status"],
        invited_at=result["invited_at"].isoformat() if result["invited_at"] else None,
        joined_at=result["joined_at"].isoformat() if result["joined_at"] else None,
    )


@router.patch("/tenant-users/{tenant_user_id}", response_model=TenantUserResponse)
async def update_tenant_user(
    tenant_user_id: str,
    request: UpdateTenantUserRequest,
    current_user: CurrentUser = Depends(require_role(UserRole.TENANT_ADMIN)),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> TenantUserResponse:
    """
    Update a user's role or status in a tenant.

    **Requires tenant_admin role.**
    """
    user_role = normalize_role(current_user.role)
    # Platform admins may target any membership; all other callers are pinned
    # to the tenant carried by their authenticated identity.
    rls_tenant_id = (
        None
        if user_role == UserRole.PLATFORM_ADMIN
        else str(current_user.tenant_id)
    )
    async with acquire_with_tenant(db_client.pool, rls_tenant_id) as conn:
        await audit_logger.lock_transaction(conn)
        # Get current record
        current = await conn.fetchrow(
            """
            SELECT tu.*, t.id as tenant_id, r.name AS current_role
            FROM tenant_users tu
            JOIN tenants t ON t.id = tu.tenant_id
            JOIN roles r ON r.id = tu.role_id
            WHERE tu.id = $1
              AND ($2::uuid IS NULL OR tu.tenant_id = $2::uuid)
            FOR UPDATE OF tu
            """,
            tenant_user_id,
            rls_tenant_id,
        )

        if not current:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant user not found",
            )

        # Check permissions
        if user_role != UserRole.PLATFORM_ADMIN:
            if str(current["tenant_id"]) != str(current_user.tenant_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Can only modify users in your own tenant",
                )

        user_role = await _current_target_admin(conn, current_user, current["tenant_id"])
        _check_role_ceiling(user_role, current["current_role"])

        if request.is_primary is not None and user_role != UserRole.PLATFORM_ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_HOME_WORKSPACE_DETAIL)
        if request.role_name is not None or request.status is not None or request.is_primary is not None:
            _refuse_own_membership(current, current_user)

        # Build update
        updates = []
        values = []
        param_idx = 1

        if request.role_name:
            # Escalation ceiling: can't grant a role above your own level.
            requested_role = normalize_role(request.role_name)
            if requested_role.level > user_role.level:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot grant a role above your own tier",
                )

            # Get role ID
            role_row = await conn.fetchrow(
                "SELECT id FROM roles WHERE name = $1",
                request.role_name,
            )
            if not role_row:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid role: {request.role_name}",
                )
            updates.append(f"role_id = ${param_idx}")
            values.append(role_row["id"])
            param_idx += 1

        if request.is_primary is not None:
            updates.append(f"is_primary = ${param_idx}")
            values.append(request.is_primary)
            param_idx += 1

        if request.status:
            updates.append(f"status = ${param_idx}")
            values.append(request.status)
            param_idx += 1

        if not updates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update",
            )
        await _keep_an_administrator(conn, current, role_name=request.role_name, new_status=request.status)

        updates.append("updated_at = NOW()")
        values.append(tenant_user_id)
        values.append(current["tenant_id"])

        # Execute update
        query = f"""
            UPDATE tenant_users
            SET {', '.join(updates)}
            WHERE id = ${param_idx} AND tenant_id = ${param_idx + 1}
            RETURNING id, user_id, tenant_id, role_id, is_primary, status, invited_at, joined_at
        """

        result = await conn.fetchrow(query, *values)

        # Get role details for response
        role_details = await conn.fetchrow(
            "SELECT id, name, description, level, is_system_role, tenant_scoped FROM roles WHERE id = $1",
            result["role_id"],
        )

        await audit_logger.log_in_transaction(
            conn,
            event_type=AuditEvent.USER_UPDATED,
            actor_id=current_user.id,
            actor_role=current_user.role,
            tenant_id=result["tenant_id"],
            resource_type="tenant_user",
            resource_id=result["id"],
            action="rbac.member.update",
            before_state=_membership_state(current), after_state=_membership_state(result),
        )

    return TenantUserResponse(
        id=str(result["id"]),
        user_id=str(result["user_id"]),
        tenant_id=str(result["tenant_id"]),
        role=RoleResponse(
            id=str(role_details["id"]),
            name=role_details["name"],
            description=role_details["description"],
            level=role_details["level"],
            is_system_role=role_details["is_system_role"],
            tenant_scoped=role_details["tenant_scoped"],
        ),
        is_primary=result["is_primary"],
        status=result["status"],
        invited_at=result["invited_at"].isoformat() if result["invited_at"] else None,
        joined_at=result["joined_at"].isoformat() if result["joined_at"] else None,
    )


@router.delete("/tenant-users/{tenant_user_id}")
async def remove_user_from_tenant(
    tenant_user_id: str,
    current_user: CurrentUser = Depends(require_role(UserRole.TENANT_ADMIN)),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
) -> Dict[str, str]:
    """
    Remove a user from a tenant (soft delete - sets status to 'removed').

    **Requires tenant_admin role.**
    """
    user_role = normalize_role(current_user.role)
    # Platform admins may target any membership; all other callers are pinned
    # to the tenant carried by their authenticated identity.
    rls_tenant_id = (
        None
        if user_role == UserRole.PLATFORM_ADMIN
        else str(current_user.tenant_id)
    )
    async with acquire_with_tenant(db_client.pool, rls_tenant_id) as conn:
        await audit_logger.lock_transaction(conn)
        # Get current record
        current = await conn.fetchrow(
            """
            SELECT tu.*, t.id as tenant_id, r.name AS current_role
            FROM tenant_users tu
            JOIN tenants t ON t.id = tu.tenant_id
            JOIN roles r ON r.id = tu.role_id
            WHERE tu.id = $1
              AND ($2::uuid IS NULL OR tu.tenant_id = $2::uuid)
            FOR UPDATE OF tu
            """,
            tenant_user_id,
            rls_tenant_id,
        )

        if not current:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant user not found",
            )

        # Check permissions
        if user_role != UserRole.PLATFORM_ADMIN:
            if str(current["tenant_id"]) != str(current_user.tenant_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Can only remove users from your own tenant",
                )

        _refuse_own_membership(current, current_user)
        user_role = await _current_target_admin(conn, current_user, current["tenant_id"])
        _check_role_ceiling(user_role, current["current_role"])
        await _keep_an_administrator(conn, current, removing=True)

        # Soft delete
        await conn.execute(
            """
            UPDATE tenant_users
            SET status = 'removed', updated_at = NOW()
            WHERE id = $1 AND tenant_id = $2
            """,
            tenant_user_id,
            current["tenant_id"],
        )
        if current["status"] != "removed":
            await audit_logger.log_in_transaction(
                conn, event_type=AuditEvent.USER_UPDATED, action="rbac.member.remove",
                actor_id=current_user.id, actor_role=current_user.role,
                tenant_id=current["tenant_id"], resource_type="tenant_user", resource_id=current["id"],
                before_state=_membership_state(current),
                after_state={**_membership_state(current), "status": "removed"},
            )

    return {"detail": "User removed from tenant successfully"}
