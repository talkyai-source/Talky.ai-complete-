"""FastAPI application bootstrap helpers."""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints.auth import limiter
from app.core.api_security_middleware import APISecurityMiddleware
from app.core.config import get_settings
from app.core.error_handlers import register_error_handlers
from app.core.request_id_middleware import (
    CallIdLogFilter,
    RequestIdLogFilter,
    RequestIdMiddleware,
)
from app.core.security_headers_middleware import SecurityHeadersMiddleware
from app.core.session_security_middleware import SessionSecurityMiddleware
from app.core.security.csrf import CSRFMiddleware
from app.core.tenant_middleware import TenantMiddleware


def configure_logging() -> None:
    """Configure application and noisy third-party loggers."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s [%(name)s] [req=%(request_id)s] [call=%(call_id)s] %(message)s",
        datefmt="%H:%M:%S",
        # force=True reclaims the root logger even if an earlier import (or
        # uvicorn) already attached a handler. Without it, basicConfig is a
        # silent no-op and our INFO level + format never take effect — which is
        # why the telephony pipeline's INFO logs (BRIDGE, originate, hangup,
        # the concurrency lease lifecycle) never reached journald and made the
        # 10/10 leak so hard to diagnose. This runs at import, BEFORE Sentry's
        # logging handler is added in lifespan, so Sentry capture is unaffected.
        force=True,
    )
    request_id_filter = RequestIdLogFilter()
    call_id_filter = CallIdLogFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(request_id_filter)
        handler.addFilter(call_id_filter)
    for noisy in (
        "httpcore",
        "httpx",
        "hpack",
        "urllib3",
        "websockets",
        "opentelemetry",
        "groq._base_client",
        "groq",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def configure_middleware(app: FastAPI) -> None:
    """Register middleware in the required outermost-first order."""
    settings = get_settings()

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Requested-With",
            "Idempotency-Key",
            "X-Request-ID",
            "X-CSRF-Token",
        ],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(CSRFMiddleware, allowed_origins=settings.allowed_origins)
    app.add_middleware(TenantMiddleware)
    app.add_middleware(SessionSecurityMiddleware)
    app.add_middleware(APISecurityMiddleware)

    app.state.limiter = limiter
    # Standardized error envelope + Retry-After on 429
    register_error_handlers(app)
