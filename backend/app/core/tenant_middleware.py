"""
Multi-Tenant Middleware
Enabled: Extracts tenant_id from JWT tokens
"""
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional
import jwt


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract and validate tenant_id from JWT token
    
    Usage:
    1. Add to main.py: app.add_middleware(TenantMiddleware)
    2. Access tenant via request.state.tenant_id in endpoints
    """
    
    async def dispatch(self, request: Request, call_next):
        # Skip tenant check for public endpoints
        public_paths = ["/", "/health", "/docs", "/openapi.json", "/redoc"]
        if request.url.path in public_paths:
            return await call_next(request)
        
        # Skip for auth endpoints (login/register don't have token yet)
        if request.url.path.startswith("/api/v1/auth"):
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
            # Decode JWT and extract tenant_id
            # Note: In production, verify signature with your secret
            payload = jwt.decode(
                token,
                options={"verify_signature": False}
            )
            tenant_id = payload.get("tenant_id") or payload.get("user_metadata", {}).get("tenant_id")
            
            # Attach tenant_id to request state
            request.state.tenant_id = tenant_id
            
        except jwt.InvalidTokenError:
            # Invalid token - let individual endpoints handle auth
            request.state.tenant_id = None
        except Exception:
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
