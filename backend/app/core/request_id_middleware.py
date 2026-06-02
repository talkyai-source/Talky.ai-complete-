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
