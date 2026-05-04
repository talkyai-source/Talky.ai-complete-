"""Role + role-permission endpoints (5 routes).

  GET    /rbac/roles                     - list roles
  GET    /rbac/roles/{id}                - one role
  GET    /rbac/roles/{id}/permissions    - permissions on a role
  POST   /rbac/roles/{id}/permissions    - assign (platform_admin only)
  DELETE /rbac/roles/{id}/permissions    - revoke (platform_admin only)
"""
from __future__ import annotations

import logging
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.dependencies import (
    CurrentUser,
    UserRole,
    get_current_user,
    get_db_client,
    require_role,
)
from app.core.postgres_adapter import Client

from .schemas import PermissionResponse, RolePermissionResponse, RoleResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["rbac"])


@router.get("/roles", response_model=List[RoleResponse])
async def list_roles(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> List[RoleResponse]:
    """List all available roles."""
    async with db_client.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, description, level, is_system_role, tenant_scoped
            FROM roles
            ORDER BY level DESC
            """
        )

    return [
        RoleResponse(
            id=str(row["id"]),
            name=row["name"],
            description=row["description"],
            level=row["level"],
            is_system_role=row["is_system_role"],
            tenant_scoped=row["tenant_scoped"],
        )
        for row in rows
    ]


@router.get("/roles/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> RoleResponse:
    """Get a specific role by ID."""
    async with db_client.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, name, description, level, is_system_role, tenant_scoped
            FROM roles
            WHERE id = $1
            """,
            role_id,
        )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found",
        )

    return RoleResponse(
        id=str(row["id"]),
        name=row["name"],
        description=row["description"],
        level=row["level"],
        is_system_role=row["is_system_role"],
        tenant_scoped=row["tenant_scoped"],
    )


@router.get("/roles/{role_id}/permissions", response_model=RolePermissionResponse)
async def get_role_permissions(
    role_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> RolePermissionResponse:
    """Get all permissions assigned to a role."""
    async with db_client.pool.acquire() as conn:
        # Get role name
        role_row = await conn.fetchrow(
            "SELECT id, name FROM roles WHERE id = $1",
            role_id,
        )
        if not role_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found",
            )

        # Get permissions
        perm_rows = await conn.fetch(
            """
            SELECT p.id, p.name, p.description, p.resource, p.action
            FROM role_permissions rp
            JOIN permissions p ON p.id = rp.permission_id
            WHERE rp.role_id = $1
            ORDER BY p.resource, p.action
            """,
            role_id,
        )

    return RolePermissionResponse(
        role_id=str(role_row["id"]),
        role_name=role_row["name"],
        permissions=[
            PermissionResponse(
                id=str(row["id"]),
                name=row["name"],
                description=row["description"],
                resource=row["resource"],
                action=row["action"],
            )
            for row in perm_rows
        ],
    )


@router.post(
    "/roles/{role_id}/permissions",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(UserRole.PLATFORM_ADMIN))],
)
async def add_permission_to_role(
    role_id: str,
    permission_id: str,
    db_client: Client = Depends(get_db_client),
) -> Dict[str, str]:
    """
    Assign a permission to a role.

    **Requires platform_admin role.**
    """
    async with db_client.pool.acquire() as conn:
        # Verify role exists
        role_row = await conn.fetchrow(
            "SELECT id, is_system_role FROM roles WHERE id = $1",
            role_id,
        )
        if not role_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found",
            )

        # Verify permission exists
        perm_row = await conn.fetchrow(
            "SELECT id FROM permissions WHERE id = $1",
            permission_id,
        )
        if not perm_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Permission not found",
            )

        # Insert role-permission mapping
        try:
            await conn.execute(
                """
                INSERT INTO role_permissions (role_id, permission_id)
                VALUES ($1, $2)
                ON CONFLICT (role_id, permission_id) DO NOTHING
                """,
                role_id,
                permission_id,
            )
        except Exception as e:
            logger.error(f"Failed to add permission to role: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to assign permission",
            )

    return {"detail": "Permission assigned to role successfully"}


@router.delete(
    "/roles/{role_id}/permissions",
    dependencies=[Depends(require_role(UserRole.PLATFORM_ADMIN))],
)
async def remove_permission_from_role(
    role_id: str,
    permission_id: str,
    db_client: Client = Depends(get_db_client),
) -> Dict[str, str]:
    """
    Remove a permission from a role.

    **Requires platform_admin role.**
    """
    async with db_client.pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM role_permissions
            WHERE role_id = $1 AND permission_id = $2
            """,
            role_id,
            permission_id,
        )

    if result == "DELETE 0":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission assignment not found",
        )

    return {"detail": "Permission removed from role successfully"}
