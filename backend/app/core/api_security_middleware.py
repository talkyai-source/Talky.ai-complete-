"""
API Security Middleware (Day 6)

OWASP API Security Top 10 2023 Protection:
- API6:2023 - Unrestricted Access to Sensitive Business Flows
- API8:2023 - Security Misconfiguration
- API10:2023 - Unsafe Consumption of APIs

Provides:
- Request validation (size, content-type)
- Payload sanitization
- Security headers
- Suspicious pattern detection
"""

import logging
import re
from typing import Optional, Any

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Configuration
MAX_REQUEST_BODY_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_CONTENT_TYPES = {
    "application/json",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
    "text/plain",
}

# Patterns for basic XSS detection
XSS_PATTERNS = [
    re.compile(r"<script[^>]*>[\s\S]*?</script>", re.IGNORECASE),
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"on\w+\s*=", re.IGNORECASE),  # onclick=, onerror=, etc.
]

# Suspicious user agents
SUSPICIOUS_UAS = [
    "sqlmap",
    "nikto",
    "nmap",
    "masscan",
    "zgrab",
    "gobuster",
    "dirbuster",
]


class APISecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware for API security hardening.

    Runs early in the middleware stack to validate requests.
    """

    async def dispatch(self, request: Request, call_next):
        # Skip for public/static paths
        if self._is_exempt_path(request.url.path):
            return await call_next(request)

        # 1. Request size check (Content-Length)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > MAX_REQUEST_BODY_SIZE:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": f"Request body too large (max {MAX_REQUEST_BODY_SIZE} bytes)"}
                    )
            except ValueError:
                pass

        # 2. Content-Type validation and Payload Sanitization
        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if request.method in ("POST", "PUT", "PATCH"):
            # Content-Type validation
            if not self._is_webhook_path(request.url.path):
                if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                    return JSONResponse(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        content={"detail": f"Content-Type '{content_type}' not supported"}
                    )

            # Payload Sanitization (Day 6)
            if content_type == "application/json":
                try:
                    # We need to read the body, sanitize it, and then replace it
                    # because FastAPI reads it once from the stream.
                    body = await request.body()
                    if body:
                        import json
                        data = json.loads(body)
                        sanitized_data = sanitize_json_value(data)
                        
                        # Re-inject the sanitized body
                        async def receive():
                            return {"type": "http.request", "body": json.dumps(sanitized_data).encode()}
                        
                        request._receive = receive
                except Exception as e:
                    logger.warning(f"Failed to sanitize JSON body: {e}")
                    # Continue anyway, let FastAPI's validation handle malformed JSON

        # 3. User-Agent validation (basic bot detection)
        user_agent = request.headers.get("user-agent", "").lower()
        if self._is_suspicious_ua(user_agent):
            logger.warning(f"Suspicious User-Agent blocked: {user_agent[:100]}")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Access denied"}
            )

        # 4. Global Rate Limiting (Day 6)
        # Attempt to apply rate limiting here if not already handled by dependencies
        # This provides a catch-all safety net.
        try:
            from app.core.security.api_security import get_api_rate_limiter
            from app.core.container import get_container
            
            container = get_container()
            if container.is_initialized and container.redis_enabled:
                limiter = get_api_rate_limiter(container.redis)
                
                # We can't easily get user_id/tenant_id yet as auth middleware might run after
                # But we can at least check IP tier early
                ip = request.client.host if request.client else "unknown"
                forwarded = request.headers.get("X-Forwarded-For")
                if forwarded:
                    ip = forwarded.split(",")[0].strip()
                
                allowed, headers, error = await limiter.check_all_tiers(
                    ip=ip,
                    user_id=None, # User/Tenant checked later in dependencies
                    tenant_id=None,
                    endpoint=request.url.path
                )
                
                if not allowed:
                    return JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content={"detail": error},
                        headers=headers
                    )
                
                # Store headers to be added to response later
                request.state.rate_limit_headers = headers
        except Exception as e:
            logger.error(f"Global rate limiting error: {e}")

        # Process request
        response = await call_next(request)

        # 4. Add security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Add rate limit headers if present
        if hasattr(request.state, "rate_limit_headers"):
            for header, value in request.state.rate_limit_headers.items():
                response.headers[header] = str(value)

        # Add idempotency replay header if applicable
        if hasattr(request.state, "idempotency_replay"):
            response.headers["Idempotent-Replay"] = "true"

        return response

    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from security checks."""
        exempt = {
            "/",
            "/health",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
        }
        if path in exempt:
            return True
        return False

    def _is_webhook_path(self, path: str) -> bool:
        """Check if path is a webhook endpoint."""
        return "/webhook" in path.lower()

    def _is_suspicious_ua(self, ua: str) -> bool:
        """Check if user agent is suspicious."""
        for pattern in SUSPICIOUS_UAS:
            if pattern in ua:
                return True
        return False


def sanitize_json_value(value: Any) -> Any:
    """
    Recursively sanitize JSON values to prevent XSS.

    Basic sanitization - removes script tags and event handlers.
    """
    if isinstance(value, str):
        for pattern in XSS_PATTERNS:
            value = pattern.sub("[removed]", value)
        return value
    elif isinstance(value, dict):
        return {k: sanitize_json_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [sanitize_json_value(item) for item in value]
    return value
