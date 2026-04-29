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

from fastapi import Cookie, Depends, HTTPException, Header, status, Request
import asyncpg

from app.core.container import get_db_pool_from_container
from app.core.jwt_security import JWTValidationError, decode_and_validate_token
from app.core.security.device_fingerprint import generate_device_fingerprint
from app.core.security.sessions import (
    SESSION_COOKIE_NAME,
    get_session_by_id,
    validate_session,
)

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

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


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


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _resolve_cookie_session(
    request: Request,
    raw_session_token: str,
    db_client: Client,
) -> Optional[dict]:
    session_id = getattr(request.state, "session_id", None)
    session_user_id = getattr(request.state, "session_user_id", None)
    if session_id and session_user_id:
        return {
            "id": str(session_id),
            "user_id": str(session_user_id),
        }

    fingerprint = generate_device_fingerprint(request)
    async with db_client.pool.acquire() as conn:
        return await validate_session(
            conn,
            raw_session_token,
            current_ip=_get_client_ip(request),
            current_fingerprint=fingerprint,
        )


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    db_client: Client = Depends(get_db_client),
) -> CurrentUser:
    """
    Extract and validate JWT token from Authorization header.

    Returns CurrentUser with user info and tenant context.
    JWT is signed with JWT_SECRET (HS256).
    """
    user_id: Optional[str] = None

    if authorization:
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

        token_session_id = payload.get("sid")
        if isinstance(token_session_id, str) and token_session_id.strip():
            request_session_id = getattr(request.state, "session_id", None)
            if request_session_id is not None:
                if str(request_session_id) != token_session_id:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Session mismatch",
                    )
                request_session_user_id = getattr(request.state, "session_user_id", None)
                if request_session_user_id is not None and str(request_session_user_id) != user_id:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Session mismatch",
                    )
            else:
                async with db_client.pool.acquire() as conn:
                    session = await get_session_by_id(conn, token_session_id, user_id=user_id)
                if not session:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or expired token",
                    )
        elif session_cookie:
            session = await _resolve_cookie_session(request, session_cookie, db_client)
            if not session or str(session.get("user_id")) != user_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired token",
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session-bound token required",
            )
    elif session_cookie:
        session = await _resolve_cookie_session(request, session_cookie, db_client)
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session has expired or is invalid",
            )
        user_id = str(session["user_id"])
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
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

    resolved_tenant_id = str(row["tenant_id"]) if row["tenant_id"] else None

    # Defensively set the RLS contextvar from the user profile we just fetched.
    # TenantMiddleware also sets this from the JWT payload, but if the JWT was
    # issued before tenant_id was added to the claim — or if BaseHTTPMiddleware
    # task isolation strips contextvar updates from inner middleware — the
    # postgres_adapter would otherwise see no tenant and set
    # app.current_tenant_id to a sentinel UUID, making every INSERT/UPDATE
    # against tenant-scoped tables fail the RLS WITH-CHECK clause.
    if resolved_tenant_id:
        try:
            from app.core.security.tenant_isolation import set_current_tenant_id
            set_current_tenant_id(resolved_tenant_id)
        except Exception:
            # Never block the request on a context-setting hiccup.
            pass

    return CurrentUser(
        id=str(row["id"]),
        email=row["email"] or "",
        tenant_id=resolved_tenant_id,
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
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    session_cookie: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME),
    db_client: Client = Depends(get_db_client),
) -> Optional[CurrentUser]:
    """
    Get current user if authenticated, otherwise return None.
    Useful for endpoints that work both with and without auth.
    """
    if not authorization:
        if not session_cookie:
            return None
    try:
        return await get_current_user(
            request=request,
            authorization=authorization,
            session_cookie=session_cookie,
            db_client=db_client,
        )
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
from app.core.config import get_settings


_audit_logger_service: AuditLogger | None = None
_suspension_service_instance: SuspensionService | None = None
_secrets_manager_service: SecretsManager | None = None
_emergency_access_service: EmergencyAccess | None = None


async def get_audit_logger(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> AuditLogger:
    """Factory for AuditLogger service"""
    global _audit_logger_service
    if _audit_logger_service is None:
        settings = get_settings()
        signing_key = (
            get_settings().effective_jwt_secret
            if settings.environment.lower() != "production"
            else None
        )
        _audit_logger_service = AuditLogger(db_pool, signing_key=signing_key)
    return _audit_logger_service


async def get_suspension_service(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> SuspensionService:
    """Factory for SuspensionService"""
    global _suspension_service_instance
    if _suspension_service_instance is None:
        _suspension_service_instance = SuspensionService(db_pool)
    return _suspension_service_instance


async def get_secrets_manager(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> SecretsManager:
    """Factory for SecretsManager"""
    global _secrets_manager_service
    if _secrets_manager_service is None:
        settings = get_settings()
        master_key = (
            settings.effective_jwt_secret.encode()
            if settings.environment.lower() != "production" and settings.effective_jwt_secret
            else None
        )
        _secrets_manager_service = SecretsManager(db_pool, master_key=master_key)
    return _secrets_manager_service


async def get_emergency_access(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
) -> EmergencyAccess:
    """Factory for EmergencyAccess service"""
    global _emergency_access_service
    if _emergency_access_service is None:
        _emergency_access_service = EmergencyAccess(db_pool)
    return _emergency_access_service
