"""Request ID / correlation middleware.

For every incoming HTTP request:
  - Reuses the upstream `X-Request-ID` header if a load balancer / proxy
    already assigned one (so traces stitch end-to-end), otherwise generates
    a fresh UUID4.
  - Stores it in a contextvar so any code path on the same async task —
    log calls, downstream service clients, error handlers — can read it
    without threading it through every function signature.
  - Echoes it back on the response so clients/operators can quote the ID
    in bug reports.

Pair with `RequestIdLogFilter` to inject the ID into every log record.
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

_HEADER = "X-Request-ID"
_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """Return the current request's ID, or '-' if outside a request."""
    return _request_id_ctx.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(_HEADER)
        # Trust upstream IDs only if they look like a UUID — otherwise an
        # attacker could poison logs with arbitrary content.
        if incoming and _looks_like_uuid(incoming):
            request_id = incoming
        else:
            request_id = str(uuid.uuid4())

        token = _request_id_ctx.set(request_id)
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)

        response.headers[_HEADER] = request_id
        return response


def _looks_like_uuid(value: str) -> bool:
    if len(value) > 64:
        return False
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


class RequestIdLogFilter(logging.Filter):
    """Inject `request_id` into every LogRecord so format strings can use it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True


# ---------------------------------------------------------------------------
# call_id correlation (voice path)
# ---------------------------------------------------------------------------
# The request-id middleware above only runs for HTTP requests — WebSockets
# (the voice path) never pass through BaseHTTPMiddleware.dispatch, so voice
# logs showed `req=-` with no way to grep a single call's logs together.
# This mirrors the exact same ContextVar + logging.Filter pattern, but is
# set explicitly at call/WS-start (see voice_pipeline_service.start_pipeline)
# instead of by middleware, since there's no ASGI middleware hook for it.
_call_id_ctx: ContextVar[str] = ContextVar("call_id", default="-")


def get_call_id() -> str:
    """Return the current call's ID, or '-' if outside a call context."""
    return _call_id_ctx.get()


def set_call_id(call_id: str) -> None:
    """Set the current task's call_id so log records tag it.

    Safe to call redundantly (e.g. once per call at pipeline start) —
    it does not need to be reset/popped like a request-scoped middleware
    value because each call's context tree ends when its tasks complete.
    """
    _call_id_ctx.set(call_id)


class CallIdLogFilter(logging.Filter):
    """Inject `call_id` into every LogRecord so format strings can use it.

    Many call sites already pass ``extra={"call_id": ...}`` explicitly on
    individual log calls (e.g. the telephony hot path) — that value lands
    on the record before filters run. Only fill in the contextvar's value
    when the record doesn't already carry one, so we add coverage for the
    common case (no explicit call_id) without clobbering deliberate values.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "call_id", None):
            record.call_id = _call_id_ctx.get()
        return True
