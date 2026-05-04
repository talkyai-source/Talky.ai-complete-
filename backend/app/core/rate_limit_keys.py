"""Rate-limit key resolvers for slowapi.

By default slowapi keys limits by remote IP — fine for /login, useless for
authenticated APIs where many users share a NAT/egress IP. These resolvers
let endpoints opt in to **per-tenant** or **per-user** limits.

Usage in an endpoint:

    from app.core.rate_limit_keys import tenant_key
    @router.post("/foo")
    @limiter.limit("60/minute", key_func=tenant_key)
    async def foo(request: Request): ...

Falls back to remote IP if no tenant context is present, so unauthenticated
routes still get protection.
"""
from __future__ import annotations

from slowapi.util import get_remote_address
from starlette.requests import Request


def _tenant_id(request: Request) -> str | None:
    # TenantMiddleware should populate request.state.tenant_id once it has
    # decoded the JWT / session. We read it defensively — the attribute may
    # not exist on unauthenticated routes.
    return getattr(request.state, "tenant_id", None)


def _user_id(request: Request) -> str | None:
    return getattr(request.state, "user_id", None)


def tenant_key(request: Request) -> str:
    """Per-tenant bucket. Falls back to IP for unauthenticated traffic."""
    tid = _tenant_id(request)
    if tid:
        return f"tenant:{tid}"
    return f"ip:{get_remote_address(request)}"


def user_key(request: Request) -> str:
    """Per-user bucket. Falls back to tenant, then IP."""
    uid = _user_id(request)
    if uid:
        return f"user:{uid}"
    return tenant_key(request)


def tenant_and_route_key(request: Request) -> str:
    """Per-tenant + per-route. Lets you set a tight limit on one endpoint
    without that endpoint's traffic eating a tenant's global quota."""
    tid = _tenant_id(request) or f"ip:{get_remote_address(request)}"
    return f"{tid}:{request.url.path}"
