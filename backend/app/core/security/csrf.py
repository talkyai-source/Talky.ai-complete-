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

Endpoints that establish or refresh auth state (``/auth/login*``,
``/auth/signup*``, ``/auth/refresh``) are also exempt: pre-login there
is no auth state to forge, and ``/auth/refresh`` is itself protected by
SameSite=Strict on the refresh cookie's restricted path.
"""
from __future__ import annotations

from typing import Iterable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import get_settings

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_EXEMPT_PATH_PREFIXES = (
    "/api/v1/auth/login",
    "/api/v1/auth/signup",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/passkey",
)


class CSRFMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, allowed_origins: Iterable[str] | None = None):
        super().__init__(app)
        self._allowed = (
            set(allowed_origins)
            if allowed_origins is not None
            else set(get_settings().allowed_origins)
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method not in _UNSAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(prefix) for prefix in _EXEMPT_PATH_PREFIXES):
            return await call_next(request)

        # Legacy Bearer clients (mobile, CLI, server-to-server) bypass CSRF —
        # Authorization headers aren't auto-attached by browsers, so they're
        # not forgeable.
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            return await call_next(request)

        origin = request.headers.get("origin")
        if origin is None:
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing Origin header for state-changing request."},
            )
        if origin not in self._allowed:
            return JSONResponse(
                status_code=403,
                content={"detail": "Origin not permitted."},
            )

        return await call_next(request)
