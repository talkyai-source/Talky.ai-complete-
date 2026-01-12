"""
Multi-Tenant Middleware
Enabled: Extracts tenant_id from JWT tokens

Security:
- In PRODUCTION: Verifies JWT signature using SUPABASE_JWT_SECRET
- In DEVELOPMENT: Skips signature verification for local testing (logs warning)
"""
import os
import logging
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional
import jwt

logger = logging.getLogger(__name__)

# Cache environment settings at module load
_ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

# Log warning if production mode without JWT secret
if _ENVIRONMENT == "production" and not _JWT_SECRET:
    logger.warning(
        "PRODUCTION MODE: SUPABASE_JWT_SECRET not set! "
        "JWT signature verification will be DISABLED. "
        "This is a SECURITY RISK - set SUPABASE_JWT_SECRET in your .env file."
    )


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract and validate tenant_id from JWT token
    
    Usage:
    1. Add to main.py: app.add_middleware(TenantMiddleware)
    2. Access tenant via request.state.tenant_id in endpoints
    
    Security:
    - Production: Verifies JWT signature (requires SUPABASE_JWT_SECRET)
    - Development: Skips verification for local testing
    """
    
    async def dispatch(self, request: Request, call_next):
        # Skip tenant check for public endpoints
        public_paths = ["/", "/health", "/docs", "/openapi.json", "/redoc"]
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
            # Allow unauthenticated requests to pass through
            # Individual endpoints can enforce auth via dependencies
            request.state.tenant_id = None
            return await call_next(request)
        
        token = auth_header.split(" ")[1]
        
        try:
            # Decode JWT with environment-aware signature verification
            if _ENVIRONMENT == "production" and _JWT_SECRET:
                # PRODUCTION: Verify JWT signature
                payload = jwt.decode(
                    token,
                    _JWT_SECRET,
                    algorithms=["HS256"],
                    options={"verify_aud": False}  # Supabase doesn't always set audience
                )
            else:
                # DEVELOPMENT: Skip signature verification (for local testing only)
                if _ENVIRONMENT == "production":
                    logger.warning(
                        "JWT verification DISABLED in production! "
                        "Set SUPABASE_JWT_SECRET to enable."
                    )
                payload = jwt.decode(
                    token,
                    options={"verify_signature": False}
                )
            
            tenant_id = payload.get("tenant_id") or payload.get("user_metadata", {}).get("tenant_id")
            
            # Attach tenant_id to request state
            request.state.tenant_id = tenant_id
            
        except jwt.ExpiredSignatureError:
            # Token expired - let individual endpoints handle auth
            logger.debug("JWT token expired")
            request.state.tenant_id = None
        except jwt.InvalidTokenError as e:
            # Invalid token - let individual endpoints handle auth
            logger.debug(f"Invalid JWT token: {e}")
            request.state.tenant_id = None
        except Exception as e:
            logger.warning(f"JWT decode error: {e}")
            request.state.tenant_id = None
        
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
