"""Request / response Pydantic models for RBAC endpoints."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


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
