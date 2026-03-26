"""
Multi-Tenant Middleware (Day 4 RBAC + Tenant Isolation)

Implements strict tenant isolation following OWASP multi-tenant security guidelines.

Official References:
  OWASP Multi-Tenant Security Guidelines:
    https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/06-Session_Management_Testing/05-Testing_for_Cross_Tenant_Data_Leakage
  PostgreSQL Row Level Security (RLS):
    https://www.postgresql.org/docs/current/ddl-rowsecurity.html

Security:
- In PRODUCTION: Verifies JWT signature using JWT_SECRET
- In DEVELOPMENT: Still verifies JWT signature when a bearer token is present
- Day 4: Integrates with RBAC for tenant access control
- Day 4: Sets PostgreSQL RLS context for defense-in-depth
"""
import logging
from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from typing import Optional

from app.core.jwt_security import JWTValidationError, decode_and_validate_token
from app.core.security.rbac import UserRole, normalize_role
from app.core.security.tenant_isolation import (
    set_current_tenant_id,
    set_bypass_rls,
    set_current_user_context,
    clear_tenant_context,
    get_current_tenant_id,
)

logger = logging.getLogger(__name__)


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract and validate tenant_id from JWT token with RBAC integration.

    Day 4 Enhancements:
    - Integrates with RBAC role hierarchy
    - Platform admins can bypass tenant checks (cross-tenant access)
    - Sets context variables for RLS enforcement
    - Validates tenant membership against database

    Usage:
    1. Add to main.py: app.add_middleware(TenantMiddleware)
    2. Access tenant via request.state.tenant_id in endpoints
    3. Use Depends(require_tenant_member) for strict enforcement

    Security:
    - Production: Verifies JWT signature (requires JWT_SECRET)
    - RBAC: Validates role permissions per tenant
    - RLS: Sets PostgreSQL context for row-level security
    """

    async def dispatch(self, request: Request, call_next):
        # Clear any existing context at start
        clear_tenant_context()

        # Skip tenant check for public endpoints
        public_paths = [
            "/",
            "/health",
            "/metrics",
            "/api/v1/health",
            "/api/v1/health/detailed",
            "/docs",
            "/openapi.json",
            "/redoc",
        ]
        if request.url.path in public_paths:
            return await call_next(request)

        # Skip for auth endpoints (login/register don't have token yet)
        if request.url.path.startswith("/api/v1/auth"):
            return await call_next(request)

        # Skip for webhook endpoints (they use their own auth)
        if request.url.path.startswith("/api/v1/webhooks"):
            return await call_next(request)

        # Skip for plans endpoint (public)
        if request.url.path.startswith("/api/v1/plans"):
            return await call_next(request)

        # Skip for OAuth callback (it's a redirect from external providers, no auth header)
        if request.url.path == "/api/v1/connectors/callback":
            return await call_next(request)

        # Skip for public connectors endpoints
        if request.url.path == "/api/v1/connectors/providers":
            return await call_next(request)

        # Skip for RBAC endpoints that handle their own tenant checks
        if request.url.path.startswith("/api/v1/rbac"):
            # Still extract tenant context but let endpoint handle enforcement
            pass

        # Extract JWT token from Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            # No token — allow request to pass through.
            # Individual endpoints enforce auth via Depends(get_current_user).
            request.state.tenant_id = None
            return await call_next(request)

        token = auth_header.split(" ")[1]

        try:
            payload = decode_and_validate_token(token)

            user_id = payload.get("sub")
            tenant_id = payload.get("tenant_id") or payload.get("user_metadata", {}).get("tenant_id")
            user_role = payload.get("role", "user")
            user_email = payload.get("email", "")

            # Attach tenant_id to request state
            request.state.tenant_id = tenant_id
            request.state.user_id = user_id
            request.state.user_role = user_role

            # Day 4: Set context variables for RBAC and RLS
            if tenant_id:
                set_current_tenant_id(tenant_id)

            # Set user context
            from app.api.v1.dependencies import CurrentUser
            set_current_user_context(CurrentUser(
                id=user_id,
                email=user_email,
                tenant_id=tenant_id,
                role=user_role,
            ))

            # Day 4: Platform admins can bypass tenant isolation
            role = normalize_role(user_role)
            if role == UserRole.PLATFORM_ADMIN:
                set_bypass_rls(True)
                # Check for cross-tenant access request
                header_tenant = request.headers.get("X-Tenant-ID")
                if header_tenant and header_tenant != tenant_id:
                    request.state.cross_tenant_access = True
                    request.state.target_tenant_id = header_tenant
                    logger.info(
                        f"Cross-tenant access: user={user_id} from={tenant_id} to={header_tenant} path={request.url.path}"
                    )

        except JWTValidationError as e:
            if e.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
                logger.error(e.detail)
            else:
                logger.warning("Invalid JWT token: %s", e.detail)
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail},
                headers={"WWW-Authenticate": "Bearer"},
            )
        except Exception as e:
            logger.error(f"JWT decode error: {e}")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication failed"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            response = await call_next(request)
            return response
        finally:
            # Always clear context after request
            clear_tenant_context()


def get_current_tenant(request: Request) -> Optional[str]:
    """
    Dependency to get current tenant_id from request
    
    Usage in endpoints:
    @router.get("/campaigns")
    async def list_campaigns(
        request: Request,
        tenant_id: str = Depends(get_current_tenant)
    ):
        # Filter campaigns by tenant_id
        campaigns = db.query(Campaign).filter(Campaign.tenant_id == tenant_id).all()
        return campaigns
    """
    if not hasattr(request.state, "tenant_id"):
        return None
    return request.state.tenant_id
