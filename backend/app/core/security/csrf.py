"""Origin-based CSRF defence for cookie-authenticated requests.

For SPA + same-origin REST API the standard 2026 defence is:

  - Cookies are SameSite=Strict (set in cookies.py).
  - Every state-changing request (POST/PUT/PATCH/DELETE) must carry an
    ``Origin`` header in the configured allow-list.

That combination defeats every classic CSRF vector: forms can't reach
the API without a matching Origin, fetch() always emits one, and
SameSite=Strict prevents the cookie from being attached on cross-site
navigations.

Bearer-authenticated requests are exempt — Authorization headers are not
auto-attached by browsers, so they're not forgeable via CSRF. This
preserves backward-compat for any clients still on the legacy header
path during the migration window.

Endpoints that establish or refresh auth state (``/auth/login``,
the explicit signup steps, ``/auth/refresh``) are also exempt: pre-login there
is no auth state to forge, and ``/auth/refresh`` is itself protected by
SameSite=Strict on the refresh cookie's restricted path.

Internal service-to-service calls (the dialer worker originating a call
against the API process that owns the ARI adapter) carry an
``X-Internal-Service-Token`` shared secret instead of a browser Origin.
These are not browser-driven and cannot be CSRF-forged, so a valid
token exempts the request. This replaces the earlier hack where the
dialer spoofed ``Origin: <FRONTEND_URL>`` to slip past this check —
a backend service should authenticate as itself, not impersonate the
browser origin. The bypass is fail-safe: when ``INTERNAL_SERVICE_TOKEN``
is unset, no token is ever accepted.
"""
from __future__ import annotations

import os
import re
import secrets
from typing import Iterable
from fastapi import HTTPException

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import get_settings
from app.core.error_handlers import http_exception_handler

_INTERNAL_TOKEN_HEADER = "x-internal-service-token"

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXEMPT_POST_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/signup/start",
    "/api/v1/auth/signup/verify-code",
    "/api/v1/auth/signup/complete",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/passkey-check",
    "/api/v1/auth/passkeys/login/begin",
    "/api/v1/auth/passkeys/login/complete",
    # Only signature-verified callbacks, never webhook administration routes.
    "/api/v1/billing/webhooks",
    "/api/v1/webhooks/call/goal-achieved",
    "/api/v1/webhooks/call/mark-spam",
    "/api/v1/webhooks/secure/call/goal-achieved",
    "/api/v1/webhooks/secure/call/mark-spam",
    "/api/v1/webhooks/secure/idempotent-example",
})
_EXEMPT_POST_PATTERNS = (
    # Internal C++ voice-gateway audio callback (Asterisk path). The gateway
    # POSTs ~50 PCMU frames/sec/call to /sip/telephony/audio/{session_id} as a
    # same-host service — no browser, no cookie auth, so CSRF (which defends
    # cookie-authenticated browser requests) provides nothing here. Without
    # this exemption every frame 403s ("Missing Origin") and the agent is
    # silent on every call. Hardening follow-up: have the gateway present
    # X-Internal-Service-Token so this can be authenticated rather than exempt.
    re.compile(r"/api/v1/sip/telephony/audio/[^/]+"),
    # Salesforce -> Talky webhooks (Flow HTTP callout / Outbound Message).
    # Server-to-server POSTs: no browser, no cookie, no Origin header. They
    # authenticate with a per-tenant secret token in the URL (SHA-256 compared
    # in constant time by the endpoint), so CSRF protection adds nothing here.
    re.compile(r"/api/v1/connectors/salesforce/callback-requests/[^/]+/[^/]+"),
    re.compile(r"/api/v1/connectors/salesforce/outbound-message/[^/]+/[^/]+"),
)


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, allowed_origins: Iterable[str] | None = None):
        super().__init__(app)
        self._allowed = (
            set(allowed_origins)
            if allowed_origins is not None
            else set(get_settings().allowed_origins)
        )
        # Shared secret for internal service-to-service calls. Read once
        # at startup; empty/unset means the internal-token bypass is
        # disabled entirely (fail-safe).
        self._internal_token = os.getenv("INTERNAL_SERVICE_TOKEN", "").strip()

    def _is_valid_internal_token(self, request: Request) -> bool:
        if not self._internal_token:
            return False
        presented = request.headers.get(_INTERNAL_TOKEN_HEADER, "")
        if not presented:
            return False
        # Constant-time compare so a timing side-channel can't be used to
        # recover the token byte-by-byte.
        return secrets.compare_digest(presented, self._internal_token)

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method not in _UNSAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        # Exempt only the actual POST routes, not neighboring paths, passkey
        # management operations, or future routes that share an auth prefix.
        if request.method == "POST" and (
            path in _EXEMPT_POST_PATHS
            or any(pattern.fullmatch(path) for pattern in _EXEMPT_POST_PATTERNS)
        ):
            return await call_next(request)

        # Legacy Bearer clients (mobile, CLI, server-to-server) bypass CSRF —
        # Authorization headers aren't auto-attached by browsers, so they're
        # not forgeable.
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            return await call_next(request)

        # Internal service-to-service calls (dialer worker → originate)
        # authenticate with a shared-secret header, not a browser Origin.
        if self._is_valid_internal_token(request):
            return await call_next(request)

        origin = request.headers.get("origin")
        if origin is None:
            return await http_exception_handler(request, HTTPException(
                status_code=403, detail="Missing Origin header for state-changing request.",
            ))
        if origin not in self._allowed:
            return await http_exception_handler(request, HTTPException(
                status_code=403, detail="Origin not permitted.",
            ))

        return await call_next(request)
