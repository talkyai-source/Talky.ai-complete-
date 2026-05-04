"""Security headers middleware.

Adds standard hardening headers to every HTTP response:
  - Content-Security-Policy           (XSS / data exfiltration)
  - Strict-Transport-Security (HSTS)  (TLS downgrade — production only)
  - X-Frame-Options                   (clickjacking)
  - X-Content-Type-Options            (MIME sniffing)
  - Referrer-Policy                   (URL/PII leakage in Referer)
  - Permissions-Policy                (disable unused browser features)
  - X-XSS-Protection                  (explicitly disable legacy filter)

CSP is relaxed for the auto-generated docs routes (/docs, /redoc) which
load Swagger/ReDoc assets from a CDN; everywhere else CSP is strict.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import get_settings

_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

_STRICT_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
_DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)
_PERMISSIONS_POLICY = (
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
    "accelerometer=(), gyroscope=(), magnetometer=(), midi=(), "
    "interest-cohort=()"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._is_production = get_settings().environment.lower() == "production"

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        path = request.url.path
        is_docs = any(path.startswith(p) for p in _DOCS_PATHS)
        response.headers["Content-Security-Policy"] = _DOCS_CSP if is_docs else _STRICT_CSP

        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
        response.headers["X-XSS-Protection"] = "0"
        response.headers.pop("Server", None)
        response.headers.pop("X-Powered-By", None)

        if self._is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        return response
