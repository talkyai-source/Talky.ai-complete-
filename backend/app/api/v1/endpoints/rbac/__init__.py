"""RBAC Management Endpoints — roles, permissions, tenant memberships.

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
    GET    /rbac/users/me/permissions     Get current user permissions
    GET    /rbac/users/me/tenants         Get current user's tenant memberships
    GET    /rbac/users/{id}/permissions   Get user effective permissions

  Tenant Membership:
    GET    /rbac/tenant-users             List tenant members (tenant admin+)
    POST   /rbac/tenant-users             Add user to tenant (tenant admin+)
    PATCH  /rbac/tenant-users/{id}        Update user role in tenant (tenant admin+)
    DELETE /rbac/tenant-users/{id}        Remove user from tenant (tenant admin+)

  Permissions:
    GET    /rbac/permissions              List all permissions

Public surface mirrors the previous single-file `rbac.py` — `router`
and the schema classes are re-exported.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    permissions as _permissions_mod,
    roles as _roles_mod,
    tenant_users as _tenant_users_mod,
    users as _users_mod,
)
from .schemas import (
    AddTenantUserRequest,
    PermissionResponse,
    RolePermissionResponse,
    RoleResponse,
    TenantMemberResponse,
    TenantUserResponse,
    UpdateTenantUserRequest,
    UserPermissionResponse,
)

router = APIRouter(prefix="/rbac", tags=["rbac"])
router.include_router(_roles_mod.router)
router.include_router(_permissions_mod.router)
router.include_router(_users_mod.router)
router.include_router(_tenant_users_mod.router)


__all__ = [
    "router",
    # Schemas
    "AddTenantUserRequest",
    "PermissionResponse",
    "RolePermissionResponse",
    "RoleResponse",
    "TenantMemberResponse",
    "TenantUserResponse",
    "UpdateTenantUserRequest",
    "UserPermissionResponse",
]
