"""
Multi-Tenant Middleware
Enabled: Extracts tenant_id from JWT tokens

Security:
- In PRODUCTION: Verifies JWT signature using JWT_SECRET
- In DEVELOPMENT: Still verifies JWT signature when a bearer token is present
"""
import logging
from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from typing import Optional

from app.core.jwt_security import JWTValidationError, decode_and_validate_token

logger = logging.getLogger(__name__)


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract and validate tenant_id from JWT token
    
    Usage:
    1. Add to main.py: app.add_middleware(TenantMiddleware)
    2. Access tenant via request.state.tenant_id in endpoints
    
    Security:
    - Production: Verifies JWT signature (requires JWT_SECRET)
    - Development: Verifies signature using the same JWT_SECRET when configured
    """
    
    async def dispatch(self, request: Request, call_next):
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

            tenant_id = payload.get("tenant_id") or payload.get("user_metadata", {}).get("tenant_id")

            # Attach tenant_id to request state
            request.state.tenant_id = tenant_id

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

        return await call_next(request)


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
