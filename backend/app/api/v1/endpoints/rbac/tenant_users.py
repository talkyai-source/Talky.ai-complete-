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
    require_role,
    require_tenant_member,
)
from app.core.postgres_adapter import Client
from app.core.security.rbac import normalize_role
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


@router.get("/tenant-users", response_model=List[TenantMemberResponse])
async def list_tenant_members(
    tenant_id: Optional[str] = Query(None, description="Tenant ID (defaults to user's tenant)"),
    status: Optional[str] = Query("active", pattern="^(pending|active|suspended|removed)$"),
    current_user: CurrentUser = Depends(require_tenant_member),
    db_client: Client = Depends(get_db_client),
) -> List[TenantMemberResponse]:
    """
    List all members of a tenant.

    **Requires tenant membership.**
    Tenant admins+ can see all members; regular users can see active members only.
    """
    # Determine tenant to query
    query_tenant_id = tenant_id or current_user.tenant_id

    # Check permissions
    user_role = normalize_role(current_user.role)
    is_admin = user_role in {UserRole.TENANT_ADMIN, UserRole.PARTNER_ADMIN, UserRole.PLATFORM_ADMIN}

    # Regular users can only see active members
    effective_status = ["active"] if not is_admin else [status]

    async with db_client.pool.acquire() as conn:
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

    async with db_client.pool.acquire() as conn:
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
    async with db_client.pool.acquire() as conn:
        # Get current record
        current = await conn.fetchrow(
            """
            SELECT tu.*, t.id as tenant_id
            FROM tenant_users tu
            JOIN tenants t ON t.id = tu.tenant_id
            WHERE tu.id = $1
            """,
            tenant_user_id,
        )

        if not current:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant user not found",
            )

        # Check permissions
        user_role = normalize_role(current_user.role)
        if user_role != UserRole.PLATFORM_ADMIN:
            if str(current["tenant_id"]) != str(current_user.tenant_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Can only modify users in your own tenant",
                )

        # Build update
        updates = []
        values = []
        param_idx = 1

        if request.role_name:
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

        updates.append("updated_at = NOW()")
        values.append(tenant_user_id)

        # Execute update
        query = f"""
            UPDATE tenant_users
            SET {', '.join(updates)}
            WHERE id = ${param_idx}
            RETURNING id, user_id, tenant_id, role_id, is_primary, status, invited_at, joined_at
        """

        result = await conn.fetchrow(query, *values)

        # Get role details for response
        role_details = await conn.fetchrow(
            "SELECT id, name, description, level, is_system_role, tenant_scoped FROM roles WHERE id = $1",
            result["role_id"],
        )

    # --- log role change event (Day 8) -----------------------------------------
    if request.role_name:
        await audit_logger.log(
            event_type=AuditEvent.USER_UPDATED,
            actor_id=current_user.id,
            actor_type="user",
            tenant_id=current_user.tenant_id,
            resource_type="user",
            resource_id=str(result["user_id"]),
            action="role_updated",
            description=f"User role updated to {request.role_name}",
            metadata={"tenant_user_id": tenant_user_id, "new_role": request.role_name},
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
) -> Dict[str, str]:
    """
    Remove a user from a tenant (soft delete - sets status to 'removed').

    **Requires tenant_admin role.**
    """
    async with db_client.pool.acquire() as conn:
        # Get current record
        current = await conn.fetchrow(
            """
            SELECT tu.*, t.id as tenant_id
            FROM tenant_users tu
            JOIN tenants t ON t.id = tu.tenant_id
            WHERE tu.id = $1
            """,
            tenant_user_id,
        )

        if not current:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant user not found",
            )

        # Check permissions
        user_role = normalize_role(current_user.role)
        if user_role != UserRole.PLATFORM_ADMIN:
            if str(current["tenant_id"]) != str(current_user.tenant_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Can only remove users from your own tenant",
                )

            # Can't remove yourself if you're the primary admin
            if str(current["user_id"]) == current_user.id and current["is_primary"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot remove yourself as primary tenant member",
                )

        # Soft delete
        await conn.execute(
            """
            UPDATE tenant_users
            SET status = 'removed', updated_at = NOW()
            WHERE id = $1
            """,
            tenant_user_id,
        )

    return {"detail": "User removed from tenant successfully"}
