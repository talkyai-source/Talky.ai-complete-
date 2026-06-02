"""Standardized API error responses.

Every error response shares the same JSON shape:

    {
      "error": {
        "code":      "rate_limited",      # stable machine identifier
        "message":   "Too many requests", # human-readable, safe to show end users
        "details":   {...} | null,        # optional structured context
        "request_id": "<uuid>"            # correlation ID — same as X-Request-ID
      }
    }

Stable `code` values let clients branch on errors without scraping prose.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_id_middleware import get_request_id

logger = logging.getLogger(__name__)


# Map HTTP status codes to stable error codes for HTTPException paths
# that don't supply their own.
_DEFAULT_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}


def _envelope(
    *,
    code: str,
    message: str,
    details: Any | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": get_request_id(),
        }
    }


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Render HTTPException as the canonical envelope.

    `detail` can be either a string or a dict:
      - **str**  → goes into `message`; `code` falls back to the
        status-code default (e.g. "forbidden").
      - **dict** → recognised as a structured error. The handler
        promotes `code` (or legacy `error`) and `message` to the
        top of the envelope and puts every other key into `details`.
        Without this unwrap, dict details were stringified via
        `str(exc.detail)`, producing envelopes like
        `{"error":{"message":"{'error': 'x', ...}"}}` — a Python repr
        inside JSON that clients can't reliably parse.
    """
    default_code = _DEFAULT_CODES.get(exc.status_code, "http_error")
    headers = dict(exc.headers or {})

    detail = exc.detail
    code = default_code
    details: Any | None = None

    if isinstance(detail, dict):
        raw_code = detail.get("code") or detail.get("error")
        if isinstance(raw_code, str) and raw_code.strip():
            code = raw_code.strip()
        raw_message = detail.get("message")
        if isinstance(raw_message, str) and raw_message.strip():
            message = raw_message
        else:
            message = code.replace("_", " ").capitalize()
        leftover = {k: v for k, v in detail.items() if k not in {"code", "error", "message"}}
        details = leftover or None
    else:
        message = str(detail) if detail is not None else default_code

    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(code=code, message=message, details=details),
        headers=headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_envelope(
            code="validation_error",
            message="Request payload failed validation",
            details=exc.errors(),
        ),
    )


async def rate_limit_exception_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    # slowapi exposes the limit string ("5/minute"); we can't compute a precise
    # Retry-After without parsing it, but a conservative default beats nothing.
    # Clients should respect this header per RFC 6585.
    retry_after = _retry_after_seconds(exc)
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=_envelope(
            code="rate_limited",
            message="Too many requests. Slow down and retry after the indicated delay.",
            details={"limit": str(exc.detail) if exc.detail else None},
        ),
        headers={"Retry-After": str(retry_after)},
    )


async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    # Never leak internal error details to clients. Operators get the full
    # traceback in logs (tagged with request_id) and Sentry.
    logger.exception("Unhandled exception in request handler")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(
            code="internal_error",
            message="An unexpected error occurred. Reference the request_id when reporting.",
        ),
    )


def _retry_after_seconds(exc: RateLimitExceeded) -> int:
    """Best-effort parse of slowapi's '<n>/<period>' format → seconds."""
    try:
        raw = str(exc.detail)
        amount, _, period = raw.partition("/")
        period_seconds = {
            "second": 1, "seconds": 1,
            "minute": 60, "minutes": 60,
            "hour": 3600, "hours": 3600,
            "day": 86400, "days": 86400,
        }.get(period.strip().lower(), 60)
        # Suggest waiting the full window; safer than guessing remainder.
        return max(1, period_seconds // max(1, int(amount)))
    except Exception:
        return 60


def register_error_handlers(app: FastAPI) -> None:
    """Wire all error handlers onto the FastAPI app."""
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
