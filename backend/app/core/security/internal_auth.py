"""Dual-path authentication for internal service ↔ authenticated-user routes.

Some endpoints (notably the telephony bridge's outbound-call origination)
have TWO legitimate callers:

  1. The dialer worker — a separate backend process that originates calls
     on behalf of many tenants. It authenticates as *itself* with a
     shared-secret ``X-Internal-Service-Token`` header (the same secret
     the CSRF middleware trusts — see ``core/security/csrf``). On this
     trusted path the request MAY name any tenant in its body, because
     the dialer legitimately dials for every tenant.

  2. A logged-in user — authenticated by the JWT that ``TenantMiddleware``
     validated, which sets ``request.state.tenant_id``. On this path the
     effective tenant is ALWAYS the JWT's tenant; a client-supplied
     ``tenant_id`` may never override it (that was the cross-tenant
     origination vulnerability). A body tenant that disagrees with the
     JWT is a 403.

Anything else — no valid token AND no authenticated tenant — is a 401.

The token compare is constant-time (``secrets.compare_digest``) and the
bypass is fail-safe: when ``INTERNAL_SERVICE_TOKEN`` is unset/empty no
token is ever accepted, exactly mirroring the CSRF middleware so the two
never disagree about what counts as a valid internal caller.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request

_INTERNAL_TOKEN_HEADER = "x-internal-service-token"


@dataclass(frozen=True)
class CallerContext:
    """Who authenticated this request.

    ``is_internal`` — a valid internal service token was presented (the
    trusted dialer path). ``tenant_id`` is the JWT-derived tenant on the
    user path and ``None`` on the internal path.
    """

    is_internal: bool
    tenant_id: Optional[str]


def _valid_internal_token(request: Request) -> bool:
    """Constant-time check of the X-Internal-Service-Token header.

    Fail-safe: an unset/empty ``INTERNAL_SERVICE_TOKEN`` means the
    internal path is disabled and no token is ever accepted.
    """
    configured = os.getenv("INTERNAL_SERVICE_TOKEN", "").strip()
    if not configured:
        return False
    presented = request.headers.get(_INTERNAL_TOKEN_HEADER, "")
    if not presented:
        return False
    return secrets.compare_digest(presented, configured)


def require_internal_or_tenant(request: Request) -> CallerContext:
    """Gate a mutating route to EITHER a valid internal token OR a JWT user.

    Returns the authenticated :class:`CallerContext`. Raises ``401`` when
    the caller is neither an internal service nor an authenticated tenant.
    """
    if _valid_internal_token(request):
        return CallerContext(is_internal=True, tenant_id=None)

    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id:
        return CallerContext(is_internal=False, tenant_id=str(tenant_id))

    raise HTTPException(
        status_code=401,
        detail="Authentication required: present a valid session or internal service token.",
    )


def resolve_call_tenant(
    request: Request,
    body_tenant_id: Optional[str],
    ctx: Optional[CallerContext] = None,
) -> Optional[str]:
    """Return the tenant a call may be originated for, after auth.

    - Internal path: the caller is trusted; the body tenant is honoured
      as-is (the dialer originates on behalf of tenants). May be ``None``.
    - User path: the effective tenant is the JWT tenant. A body tenant
      that is present and disagrees with the JWT tenant is a ``403``; the
      client value can never override the JWT.

    Pass ``ctx`` when the route already called
    :func:`require_internal_or_tenant` to avoid re-checking the token.

    Raises ``401`` (no auth) or ``403`` (cross-tenant attempt).
    """
    if ctx is None:
        ctx = require_internal_or_tenant(request)
    if ctx.is_internal:
        return str(body_tenant_id) if body_tenant_id is not None else None

    if body_tenant_id is not None and str(body_tenant_id) != ctx.tenant_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tenant_mismatch",
                "message": (
                    "body.tenant_id does not match the authenticated tenant; "
                    "a user may only originate calls for their own tenant."
                ),
            },
        )
    return ctx.tenant_id
