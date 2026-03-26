"""
RBAC Management Endpoints

Provides API for managing roles, permissions, and tenant memberships.

Official References:
  NIST RBAC Standard (ANSI/INCITS 359-2004):
    https://csrc.nist.gov/projects/role-based-access-control
  OWASP Access Control Cheat Sheet:
    https://cheatsheetseries.owasp.org/cheatsheets/Access_Control_Cheat_Sheet.html

Endpoints:
  Roles:
    GET    /rbac/roles                    List all roles
    GET    /rbac/roles/{id}               Get role details
    GET    /rbac/roles/{id}/permissions   Get role permissions
    POST   /rbac/roles/{id}/permissions   Assign permission to role (platform admin)
    DELETE /rbac/roles/{id}/permissions   Remove permission from role (platform admin)

  User Management:
    GET    /rbac/users/{id}/permissions   Get user effective permissions
    GET    /rbac/users/{id}/tenants       Get user's tenant memberships

  Tenant Membership:
    GET    /rbac/tenant-users             List tenant members (tenant admin+)
    POST   /rbac/tenant-users             Add user to tenant (tenant admin+)
    PATCH  /rbac/tenant-users/{id}        Update user role in tenant (tenant admin+)
    DELETE /rbac/tenant-users/{id}        Remove user from tenant (tenant admin+)

  Permissions:
    GET    /rbac/permissions              List all permissions
"""

from __future__ import annotations

import logging
from typing import List, Optional, Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field

from app.api.v1.dependencies import (
    CurrentUser,
    get_current_user,
    require_role,
    require_tenant_member,
    load_user_permissions,
    UserRole,
    Permission,
    get_user_permissions,
    get_user_tenants,
    get_db_client,
)
from app.core.postgres_adapter import Client
from app.core.security.rbac import (
    normalize_role,
    ROLE_DEFAULT_PERMISSIONS,
    get_user_role_in_tenant,
)
from app.core.security.tenant_isolation import validate_tenant_access, get_user_primary_tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rbac", tags=["rbac"])


# =============================================================================
# Request/Response Models
# =============================================================================

class RoleResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    level: int
    is_system_role: bool
    tenant_scoped: bool


class PermissionResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    resource: str
    action: str


class RolePermissionResponse(BaseModel):
    role_id: str
    role_name: str
    permissions: List[PermissionResponse]


class UserPermissionResponse(BaseModel):
    user_id: str
    tenant_id: Optional[str]
    permissions: List[str]
    role: str
    grant_type: str  # "role" or "direct"


class TenantUserResponse(BaseModel):
    id: str
    user_id: str
    tenant_id: str
    role: RoleResponse
    is_primary: bool
    status: str
    invited_at: Optional[str]
    joined_at: Optional[str]


class AddTenantUserRequest(BaseModel):
    user_id: str
    tenant_id: str
    role_name: str = Field(default="user", pattern="^(platform_admin|partner_admin|tenant_admin|user|readonly)$")
    is_primary: bool = False


class UpdateTenantUserRequest(BaseModel):
    role_name: Optional[str] = Field(None, pattern="^(platform_admin|partner_admin|tenant_admin|user|readonly)$")
    is_primary: Optional[bool] = None
    status: Optional[str] = Field(None, pattern="^(pending|active|suspended|removed)$")


class TenantMemberResponse(BaseModel):
    id: str
    email: str
    name: Optional[str]
    role: str
    is_primary: bool
    status: str
    joined_at: Optional[str]


# =============================================================================
# Roles Endpoints
# =============================================================================

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


# =============================================================================
# Permissions Endpoints
# =============================================================================

@router.get("/permissions", response_model=List[PermissionResponse])
async def list_permissions(
    current_user: CurrentUser = Depends(get_current_user),
    db_client: Client = Depends(get_db_client),
    resource: Optional[str] = Query(None, description="Filter by resource"),
) -> List[PermissionResponse]:
    """List all available permissions."""
    async with db_client.pool.acquire() as conn:
        if resource:
            rows = await conn.fetch(
                """
                SELECT id, name, description, resource, action
                FROM permissions
                WHERE resource = $1
                ORDER BY resource, action
                """,
                resource,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, name, description, resource, action
                FROM permissions
                ORDER BY resource, action
                """
            )

    return [
        PermissionResponse(
            id=str(row["id"]),
            name=row["name"],
            description=row["description"],
            resource=row["resource"],
            action=row["action"],
        )
        for row in rows
    ]


# =============================================================================
# User Permissions Endpoints
# =============================================================================

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


# =============================================================================
# Tenant Users (Membership) Endpoints
# =============================================================================

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

        updates.append(f"updated_at = NOW()")
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
