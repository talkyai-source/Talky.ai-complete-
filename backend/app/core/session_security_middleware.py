"""
Session Security Middleware (Day 5)

Validates session binding on every request and detects anomalies.
Integrates with existing TenantMiddleware and authentication system.

OWASP Session Management Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html

NIST SP 800-63B (Session Security):
  https://pages.nist.gov/800-63-3/sp800-63b.html

Purpose:
  Provides defense-in-depth session security by:
  1. Validating session binding (IP + device fingerprint) on each request
  2. Detecting potential session hijacking attempts
  3. Requiring re-authentication for suspicious sessions
  4. Logging security events for audit and monitoring

Integration:
  This middleware runs AFTER authentication (TenantMiddleware/dependencies).
  It reads the session cookie and validates binding but does not replace
  the existing authentication flow.
"""

from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.container import get_db_pool_from_container
from app.core.security.device_fingerprint import generate_device_fingerprint
from app.core.security.sessions import (
    SESSION_COOKIE_NAME,
    SESSION_STRICT_BINDING,
    hash_session_token,
    validate_session,
)

logger = logging.getLogger(__name__)


def _session_cookie_secure() -> bool:
    override = os.getenv("SESSION_COOKIE_SECURE", "").strip().lower()
    if override:
        return override in {"1", "true", "yes", "on"}
    return get_settings().environment.lower() == "production"

# Paths that are exempt from session binding checks
_PUBLIC_PATHS = {
    "/",
    "/health",
    "/metrics",
    "/api/v1/health",
    "/api/v1/health/detailed",
    "/docs",
    "/openapi.json",
    "/redoc",
}

# Paths that skip session security (auth endpoints, webhooks)
_EXEMPT_PATH_PREFIXES = [
    "/api/v1/auth",  # Auth endpoints handle their own session creation
    "/api/v1/webhooks",  # Webhooks use different auth mechanism
    "/api/v1/plans",  # Public plans endpoint
    "/api/v1/connectors/callback",  # OAuth callbacks
]


class SessionSecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce session binding and detect hijacking.

    Performs on every authenticated request:
    1. Extract session cookie
    2. Generate current device fingerprint
    3. Validate session with binding checks
    4. Handle suspicious activity (log, optionally block)

    Configuration:
    - SESSION_STRICT_BINDING: If True, revokes sessions on binding violation
    - If False, marks suspicious but allows request (recommended for UX)
    """

    async def dispatch(self, request: Request, call_next):
        # Skip public paths
        if self._is_exempt_path(request.url.path):
            return await call_next(request)

        # Get session cookie
        session_cookie = request.cookies.get(SESSION_COOKIE_NAME)
        if not session_cookie:
            # No session cookie - let downstream auth handle it
            return await call_next(request)

        # Extract client info
        ip_address = self._get_client_ip(request)
        fingerprint = generate_device_fingerprint(request)

        # Validate session with binding
        try:
            pool = get_db_pool_from_container()
            async with pool.acquire() as conn:
                session = await validate_session(
                    conn,
                    session_cookie,
                    current_ip=ip_address,
                    current_fingerprint=fingerprint,
                    strict_binding=SESSION_STRICT_BINDING,
                )

                if session is None:
                    # Session invalid - clear cookie to prevent loops
                    response = await call_next(request)
                    response.delete_cookie(
                        key=SESSION_COOKIE_NAME,
                        httponly=True,
                        secure=_session_cookie_secure(),
                        samesite="strict",
                        path="/",
                    )
                    return response

                # Check for suspicious activity
                if session.get("is_suspicious"):
                    await self._handle_suspicious_session(
                        request, session, ip_address, fingerprint
                    )

                # Check if verification required
                if session.get("requires_verification"):
                    return self._require_verification_response()

                # Store session info in request state for endpoints
                request.state.session_id = session.get("id")
                request.state.session_user_id = session.get("user_id")
                request.state.session_is_suspicious = session.get("is_suspicious")
                request.state.session_device_name = session.get("device_name")

        except Exception as e:
            # Log but don't block on middleware errors
            logger.error(f"Session security middleware error: {e}")

        return await call_next(request)

    def _is_exempt_path(self, path: str) -> bool:
        """Check if path is exempt from session security checks."""
        if path in _PUBLIC_PATHS:
            return True

        for prefix in _EXEMPT_PATH_PREFIXES:
            if path.startswith(prefix):
                return True

        return False

    def _get_client_ip(self, request: Request) -> str:
        """
        Extract the real client IP from the request.

        Respects X-Forwarded-For when behind a trusted reverse proxy.
        Falls back to the direct connection IP.
        """
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # X-Forwarded-For: client, proxy1, proxy2 — take the leftmost
            return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    async def _handle_suspicious_session(
        self,
        request: Request,
        session: dict,
        current_ip: str,
        current_fingerprint: str,
    ) -> None:
        """
        Handle detection of suspicious session activity.

        Logs security event for monitoring. Does not block the request
        unless strict mode is enabled (handled in validate_session).
        """
        reason = session.get("suspicious_reason", "unknown")

        logger.warning(
            "Suspicious session activity detected: "
            "session_id=%s user_id=%s reason=%s ip=%s",
            session.get("id"),
            session.get("user_id"),
            reason,
            current_ip,
            extra={
                "security_event": "suspicious_session",
                "session_id": str(session.get("id")),
                "user_id": str(session.get("user_id")),
                "suspicious_reason": reason,
                "ip_address": current_ip,
                "path": request.url.path,
            },
        )

        # In future: could emit to security monitoring system
        # await security_monitor.emit("suspicious_session", {...})

    def _require_verification_response(self) -> JSONResponse:
        """
        Return response requiring session verification.

        Client should prompt user to re-authenticate.
        """
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "detail": "Session verification required",
                "code": "SESSION_VERIFICATION_REQUIRED",
                "action": "reauthenticate",
                "message": "Suspicious activity detected. Please log in again to continue.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_session_info_from_request(request: Request) -> Optional[dict]:
    """
    Extract session info from request state.

    Use in endpoints that need session metadata:

        @router.get("/some-endpoint")
        async def some_endpoint(request: Request):
            session_info = get_session_info_from_request(request)
            if session_info:
                print(f"Session: {session_info['device_name']}")
    """
    if not hasattr(request.state, "session_id"):
        return None

    return {
        "session_id": request.state.session_id,
        "is_suspicious": getattr(request.state, "session_is_suspicious", False),
        "device_name": getattr(request.state, "session_device_name", None),
    }
