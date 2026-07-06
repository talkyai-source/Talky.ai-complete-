"""P0-3 regression guard: admin operator-console routes are platform_admin-only.

Background
==========
The five admin routers below (``admin/tenants.py``, ``admin/calls.py``,
``admin/users.py``, ``admin/connectors.py``, ``admin/usage.py``) all query
**across every tenant** with no filter tied to the caller's own tenant
(e.g. "List all tenants", "all active calls", "all connectors across all
tenants", platform-wide usage). They were previously gated with
``require_admin``, which admits ``tenant_admin`` and ``partner_admin`` — so
ANY tenant_admin could read/mutate every other tenant's data.

The fix (P0-3): these are genuinely global operator-console routes, so they
are gated with ``require_platform_admin``. The admin panel itself runs as
platform_admin, so this does not lock out the real operator; but a
tenant_admin can no longer reach cross-tenant data.

This test locks that in two ways:

1. Behavioural — ``require_platform_admin`` 403s a tenant_admin (and every
   lower role) and passes a platform_admin. This proves the gate fails safe.
2. Wiring — EVERY route in the five routers carries ``require_platform_admin``
   in its resolved dependency tree and does NOT carry the weaker
   ``require_admin``. This proves the routes are actually wired to the strict
   gate, so a future edit that downgrades one back to ``require_admin`` fails
   CI.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser, require_admin, require_platform_admin
from app.api.v1.endpoints.admin import calls, connectors, tenants, usage, users

# The routers this agent owns for P0-3. Every route on each must be
# platform_admin-only.
_ADMIN_ROUTERS = {
    "admin/tenants.py": tenants.router,
    "admin/calls.py": calls.router,
    "admin/users.py": users.router,
    "admin/connectors.py": connectors.router,
    "admin/usage.py": usage.router,
}


def _dep_names(route) -> set[str]:
    """Callable names anywhere in a route's resolved dependency tree."""
    names: set[str] = set()
    stack = list(route.dependant.dependencies)
    seen: set[int] = set()
    while stack:
        dep = stack.pop()
        if id(dep) in seen:
            continue
        seen.add(id(dep))
        name = getattr(getattr(dep, "call", None), "__name__", "")
        if name:
            names.add(name)
        stack.extend(dep.dependencies)
    return names


def _make_user(role: str) -> CurrentUser:
    return CurrentUser(
        id="00000000-0000-0000-0000-000000000001",
        email="who@example.com",
        tenant_id="11111111-1111-1111-1111-111111111111",
        role=role,
    )


# --- 1. behavioural: the gate itself fails safe ----------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin", "user", "readonly"])
async def test_require_platform_admin_denies_non_platform_roles(role):
    """A tenant_admin (and every lower role) is 403'd — this is the leak fix."""
    with pytest.raises(HTTPException) as exc:
        await require_platform_admin(current_user=_make_user(role))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_platform_admin_denies_unknown_role_fails_closed():
    """An unknown / normalization-failed role denies (fails closed)."""
    with pytest.raises(HTTPException) as exc:
        await require_platform_admin(current_user=_make_user("wat-is-this"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_platform_admin_allows_platform_admin():
    """The real operator (platform_admin) passes — flow is not broken."""
    user = _make_user("platform_admin")
    result = await require_platform_admin(current_user=user)
    assert result is user


@pytest.mark.asyncio
async def test_require_admin_would_have_admitted_tenant_admin():
    """Baseline: the OLD gate admitted a tenant_admin — this is what P0-3 closes.

    Guards against someone 'fixing' the leak by weakening require_admin
    instead (which would silently re-open every other require_admin route).
    """
    user = _make_user("tenant_admin")
    result = await require_admin(current_user=user)
    assert result is user


# --- 2. wiring: every admin console route is platform_admin-only -----

def _all_routes():
    for label, router in _ADMIN_ROUTERS.items():
        for route in router.routes:
            methods = sorted(m for m in route.methods if m != "HEAD")
            yield label, methods, route


def test_every_admin_console_route_requires_platform_admin():
    """Every route in the five routers carries require_platform_admin and
    NOT the weaker require_admin."""
    offenders: list[str] = []
    checked = 0
    for label, methods, route in _all_routes():
        checked += 1
        names = _dep_names(route)
        key = f"{label} {methods} {route.path}"
        if "require_platform_admin" not in names:
            offenders.append(f"{key} -> missing require_platform_admin (deps={sorted(names)})")
        if "require_admin" in names:
            offenders.append(f"{key} -> still carries require_admin (leak)")

    assert checked >= 20, f"expected >=20 admin routes, found {checked} (discovery broken)"
    assert not offenders, "admin console routes not gated to platform_admin:\n  " + "\n  ".join(offenders)
