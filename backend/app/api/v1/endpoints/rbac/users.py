"""User-permission endpoints — caller's own permissions/tenants and (admin) lookup of others.

  GET /rbac/users/me/permissions       - effective permissions in current tenant
  GET /rbac/users/me/tenants           - all tenant memberships
  GET /rbac/users/{user_id}/permissions - admin lookup (tenant_admin+)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.dependencies import (
    CurrentUser,
    UserRole,
    get_current_user,
    get_db_client,
    get_user_permissions,
    get_user_tenants,
    load_user_permissions,
    require_role,
)
from app.core.postgres_adapter import Client
from app.core.security.rbac import get_user_role_in_tenant, normalize_role
from app.core.security.tenant_isolation import validate_tenant_access

from .schemas import UserPermissionResponse

router = APIRouter(tags=["rbac"])


@router.get("/users/me/permissions", response_model=UserPermissionResponse)
async def get_my_permissions(
    current_user: CurrentUser = Depends(load_user_permissions),
) -> UserPermissionResponse:
    """Get current user's effective permissions in the current tenant."""
    return UserPermissionResponse(
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        permissions=list(current_user._permissions or []),
        role=current_user.role,
        grant_type="role",
    )


@router.get("/users/me/tenants", response_model=List[Dict[str, Any]])
async def get_my_tenants(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
) -> List[Dict[str, Any]]:
    """Get all tenants the current user belongs to."""
    async with db_client.pool.acquire() as conn:
        tenants = await get_user_tenants(conn, current_user.id, include_pending=True)

    return tenants


@router.get("/users/{user_id}/permissions", response_model=UserPermissionResponse)
async def get_user_permissions_endpoint(
    user_id: str,
    tenant_id: Optional[str] = Query(None, description="Tenant context"),
    current_user: CurrentUser = Depends(require_role(UserRole.TENANT_ADMIN)),
    db_client: Client = Depends(get_db_client),
) -> UserPermissionResponse:
    """
    Get a user's effective permissions.

    **Requires tenant_admin role.**
    Can only query users in the same tenant (or any tenant for platform admins).
    """
    # Determine which tenant to query
    query_tenant_id = tenant_id or current_user.tenant_id

    # Check permissions
    current_role = normalize_role(current_user.role)

    if current_role != UserRole.PLATFORM_ADMIN:
        # Can only query own tenant
        if str(query_tenant_id) != str(current_user.tenant_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Can only query users in your tenant",
            )

        # Verify target user is in the tenant
        async with db_client.pool.acquire() as conn:
            target_in_tenant = await validate_tenant_access(conn, user_id, query_tenant_id)

        if not target_in_tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found in this tenant",
            )

    # Get user's role and permissions
    async with db_client.pool.acquire() as conn:
        user_role = await get_user_role_in_tenant(conn, user_id, query_tenant_id)
        perms = await get_user_permissions(conn, user_id, query_tenant_id)

    return UserPermissionResponse(
        user_id=user_id,
        tenant_id=query_tenant_id,
        permissions=[p.value for p in perms],
        role=user_role.value if user_role else "unknown",
        grant_type="role",
    )
