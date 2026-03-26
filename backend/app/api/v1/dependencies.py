"""
Shared API Dependencies

Provides dependency injection for:
- Database connection (get_db) — via asyncpg pool from ServiceContainer
- Current authenticated user (get_current_user) — via JWT
- Admin authorization (require_admin)
- RBAC authorization (require_role, require_permission)
- Tenant isolation (require_tenant_access, require_tenant_member)
- Optional user (get_optional_user)

Uses local JWT verification.

Day 4 RBAC Additions:
- Role-based access control with hierarchy
- Permission-based access control
- Tenant membership validation
- Cross-tenant access logging
"""
import logging
from typing import Optional, Set
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Header, status, Request
import asyncpg

from app.core.container import get_db_pool_from_container
from app.core.jwt_security import JWTValidationError, decode_and_validate_token

logger = logging.getLogger(__name__)


@dataclass
class CurrentUser:
    """User context extracted from JWT token"""
    id: str
    email: str
    tenant_id: Optional[str] = None
    role: str = "user"
    name: Optional[str] = None
    business_name: Optional[str] = None
    minutes_remaining: int = 0

    # Day 4: RBAC cache
    _permissions: Optional[Set[str]] = field(default=None, repr=False)

    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission (uses cached permissions if available)."""
        if self._permissions is not None:
            return permission in self._permissions
        # Fallback to role-based check
        from app.core.security.rbac import UserRole, ROLE_DEFAULT_PERMISSIONS, normalize_role
        user_role = normalize_role(self.role)
        user_perms = ROLE_DEFAULT_PERMISSIONS.get(user_role, set())
        return permission in {p.value for p in user_perms}

    def set_permissions(self, permissions: Set[str]) -> None:
        """Cache user's effective permissions."""
        self._permissions = permissions


def get_db_pool() -> asyncpg.Pool:
    """
    FastAPI dependency — returns the asyncpg pool from the ServiceContainer.
    """
    return get_db_pool_from_container()


from app.core.postgres_adapter import Client

# Backward-compat alias
def get_db_client(pool: asyncpg.Pool = Depends(get_db_pool)) -> Client:
    """
    Backward-compat alias -> returns Postgres adapter client wrapping asyncpg pool.
    Shim allows legacy code using .table() to work.
    """
    # When called outside FastAPI dependency injection, `pool` can be a
    # Depends marker instead of an asyncpg pool. Resolve from container.
    if not hasattr(pool, "acquire"):
        pool = get_db_pool()
    return Client(pool)


async def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db_client: Client = Depends(get_db_client),
) -> CurrentUser:
    """
    Extract and validate JWT token from Authorization header.

    Returns CurrentUser with user info and tenant context.
    JWT is signed with JWT_SECRET (HS256).
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
        )

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization format. Expected: Bearer <token>",
        )

    token = parts[1]

    try:
        payload = decode_and_validate_token(token)
    except JWTValidationError as e:
        if e.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            logger.error(e.detail)
        else:
            logger.warning("Token verification failed: %s", e.detail)
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail,
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing subject",
        )

    # Fetch user profile with tenant info from PostgreSQL
    try:
        async with db_client.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT up.id, up.email, up.name, up.role, up.tenant_id,
                       t.business_name, t.minutes_allocated, t.minutes_used
                FROM user_profiles up
                LEFT JOIN tenants t ON t.id = up.tenant_id
                WHERE up.id = $1
                """,
                user_id,
            )
    except Exception as e:
        logger.warning(f"Failed to fetch user profile: {e}")
        row = None

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User profile not found",
        )

    minutes_remaining = max(
        0,
        (row["minutes_allocated"] or 0) - (row["minutes_used"] or 0)
    )

    return CurrentUser(
        id=str(row["id"]),
        email=row["email"] or "",
        tenant_id=str(row["tenant_id"]) if row["tenant_id"] else None,
        role=row["role"] or "user",
        name=row["name"],
        business_name=row["business_name"],
        minutes_remaining=minutes_remaining,
    )


async def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Require admin role for endpoint access."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def get_optional_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db_client: Client = Depends(get_db_client),
) -> Optional[CurrentUser]:
    """
    Get current user if authenticated, otherwise return None.
    Useful for endpoints that work both with and without auth.
    """
    if not authorization:
        return None
    try:
        return await get_current_user(authorization=authorization, db_client=db_client)
    except HTTPException:
        return None


# =============================================================================
# Day 4: RBAC Dependencies
# =============================================================================

# Re-export RBAC dependencies for convenience
from app.core.security.rbac import (
    UserRole,
    Permission,
    require_role,
    require_permission,
    get_user_permissions,
    get_user_role_in_tenant,
    get_user_tenants,
    normalize_role,
)
from app.core.security.tenant_isolation import (
    TenantContext,
    require_tenant_access,
    get_tenant_context_dependency,
    get_current_tenant_id,
    validate_tenant_access,
)


async def require_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Require admin role for endpoint access.

    Day 4: Updated to use new RBAC system.
    Accepts: tenant_admin, partner_admin, platform_admin
    """
    user_role = normalize_role(current_user.role)

    # Allow all admin levels
    allowed_roles = {UserRole.TENANT_ADMIN, UserRole.PARTNER_ADMIN, UserRole.PLATFORM_ADMIN}

    if user_role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def require_platform_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Require platform_admin role (highest privilege level)."""
    user_role = normalize_role(current_user.role)

    if user_role != UserRole.PLATFORM_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access required",
        )
    return current_user


async def require_tenant_member(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Require that the user is a member of the tenant they're accessing.

    Extracts tenant_id from header (X-Tenant-ID), path, or falls back to user's default tenant.
    Platform admins bypass this check.
    """
    # Extract tenant_id from various sources
    tenant_id = (
        request.headers.get("X-Tenant-ID")
        or request.path_params.get("tenant_id")
        or current_user.tenant_id
    )

    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant ID required",
        )

    # Platform admins can access all tenants
    user_role = normalize_role(current_user.role)
    if user_role == UserRole.PLATFORM_ADMIN:
        # Set context for the rest of the request
        from app.core.security.tenant_isolation import set_current_tenant_id, set_bypass_rls
        set_current_tenant_id(tenant_id)
        set_bypass_rls(True)
        return current_user

    # Validate tenant membership
    async with get_db_client().pool.acquire() as conn:
        has_access = await validate_tenant_access(conn, current_user.id, tenant_id)

    if not has_access:
        logger.warning(
            f"Tenant membership check failed: user={current_user.id} tenant={tenant_id}"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this tenant",
        )

    # Set context for the rest of the request
    from app.core.security.tenant_isolation import set_current_tenant_id, set_bypass_rls
    set_current_tenant_id(tenant_id)
    set_bypass_rls(False)

    return current_user


async def load_user_permissions(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """
    Dependency that loads and caches user permissions.

    Use this when you need permission checking within endpoint logic.
    """
    tenant_id = current_user.tenant_id

    # Skip DB query for platform admins (they have all permissions)
    user_role = normalize_role(current_user.role)
    if user_role == UserRole.PLATFORM_ADMIN:
        all_perms = {p.value for p in Permission}
        current_user.set_permissions(all_perms)
        return current_user

    # Load permissions from database
    if tenant_id:
        async with get_db_client().pool.acquire() as conn:
            perms = await get_user_permissions(conn, current_user.id, tenant_id)
            current_user.set_permissions({p.value for p in perms})

    return current_user


# Re-export all for convenience
__all__ = [
    "CurrentUser",
    "get_db_pool",
    "get_db_client",
    "get_current_user",
    "get_optional_user",
    "require_admin",
    "require_platform_admin",
    "require_tenant_member",
    "load_user_permissions",
    # RBAC exports
    "UserRole",
    "Permission",
    "require_role",
    "require_permission",
    "require_tenant_access",
    "TenantContext",
    "get_tenant_context_dependency",
]


# =============================================================================
# Day 8: Multi-permission check helper
# =============================================================================

def require_permissions(required_perms: list[str]):
    """
    Factory that creates a dependency requiring ANY of the listed permissions.

    Usage:
        @router.get("/", dependencies=[Depends(require_permissions(["read:logs", "admin:full"]))])
    """
    async def _check_permissions(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        user_perms = current_user._permissions or set()

        # Load permissions if not cached
        if not user_perms and current_user.tenant_id:
            async with get_db_client().pool.acquire() as conn:
                perms = await get_user_permissions(conn, current_user.id, current_user.tenant_id)
                user_perms = {p.value for p in perms}
                current_user.set_permissions(user_perms)

        # Platform admin has all permissions
        user_role = normalize_role(current_user.role)
        if user_role == UserRole.PLATFORM_ADMIN:
            return current_user

        # Check if user has any of the required permissions
        if not any(perm in user_perms for perm in required_perms):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {', '.join(required_perms)}",
            )

        return current_user

    return _check_permissions


# =============================================================================
# Day 8: Service factory functions for dependency injection
# =============================================================================

from app.domain.services.audit_logger import AuditLogger
from app.domain.services.suspension_service import SuspensionService
from app.domain.services.secrets_manager import SecretsManager
from app.core.security.emergency_access import EmergencyAccess


async def get_audit_logger(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> AuditLogger:
    """Factory for AuditLogger service"""
    return AuditLogger(db_pool)


async def get_suspension_service(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> SuspensionService:
    """Factory for SuspensionService"""
    return SuspensionService(db_pool)


async def get_secrets_manager(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> SecretsManager:
    """Factory for SecretsManager"""
    return SecretsManager(db_pool)


async def get_emergency_access(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> EmergencyAccess:
    """Factory for EmergencyAccess service"""
    return EmergencyAccess(db_pool)
