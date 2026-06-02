"""Canonical error helper for backend endpoints.

Prefer raising :class:`ApiError` over a bare ``HTTPException(detail=...)``
when an endpoint wants to surface a stable machine code, a user-safe
message, and structured ``details``. The handler in ``error_handlers.py``
unwraps both styles, but this class is the obvious primitive — new code
should reach for it first.

Example::

    raise ApiError(
        code="caller_id_not_verified",
        message=(
            "The caller_id is not registered and verified under this "
            "tenant. Register it at POST /api/v1/tenant-phone-numbers."
        ),
        details={"caller_id": caller_id, "tenant_id": str(tenant_id)},
        status=403,
    )

The renderer turns that into::

    {"error": {
        "code": "caller_id_not_verified",
        "message": "The caller_id is not registered and verified ...",
        "details": {"caller_id": "+17789249977", "tenant_id": "..."},
        "request_id": "<uuid>"
    }}

with HTTP status 403.
"""
from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException


class ApiError(HTTPException):
    """HTTPException subclass that always renders as the canonical envelope.

    Use ``ApiError`` for any 4xx/5xx where a caller may want to branch on
    the error programmatically. Don't use it for genuinely opaque errors —
    a plain ``HTTPException(status_code=500, detail="…")`` is fine and
    still produces a clean envelope.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status: int = 400,
        details: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if not code or not code.replace("_", "").isalnum():
            # Codes are part of our public contract. Forbid spaces, symbols,
            # or empty values so the contract stays grep-able and stable.
            raise ValueError(f"ApiError.code must be snake_case alnum, got {code!r}")
        payload: dict[str, Any] = {"code": code, "message": message}
        if details:
            payload.update(dict(details))
        super().__init__(
            status_code=status,
            detail=payload,
            headers=dict(headers) if headers else None,
        )
