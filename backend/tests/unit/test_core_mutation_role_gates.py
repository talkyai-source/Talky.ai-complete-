"""P1-13 regression: core mutation routes enforce the ALREADY-DEFINED RBAC tiers.

Two properties are asserted:

1. **Behavioral** — the ``require_permission(...)`` dependency factory (and the
   ``require_admin`` role dep used for DNC) DENIES a principal whose role lacks
   the permission (403) and ADMITS one that holds it, per
   ``ROLE_DEFAULT_PERMISSIONS`` in ``app/core/security/rbac.py``. Unknown roles
   fail SAFE (normalize -> readonly -> denied).

2. **Wiring** — every mutation route listed in the P1-13 mapping actually
   carries the expected gate in its resolved FastAPI ``dependant`` tree, and the
   read-only routes we intentionally left open (preview-prompt, GET list) do
   NOT carry a mutation gate. This proves the decorator was applied to the right
   route, not just that the dependency exists in the module.

Real users are ``tenant_admin``, which holds every permission gated here, so
none of these gates lock a legitimate tenant admin out of their own tenant.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser, require_admin
from app.core.security.rbac import Permission, require_permission

# Routers under test (imported standalone; APIRoute builds .dependant eagerly).
from app.api.v1.endpoints.campaigns import router as campaigns_router
from app.api.v1.endpoints.calls import router as calls_router
from app.api.v1.endpoints.billing import router as billing_router
from app.api.v1.endpoints.dnc import router as dnc_router
from app.api.v1.endpoints.connectors import router as connectors_router


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _user(role: str) -> CurrentUser:
    return CurrentUser(id="u-1", email="u@example.com", tenant_id="tenant-1", role=role)


class _FakeRequest:
    """Minimal stand-in for starlette Request used by the permission checker.

    ``headers``/``path_params``/``query_params`` are plain dicts so ``.get``
    returns ``None`` (not a truthy MagicMock) — the checker then falls back to
    the user's own tenant_id, matching production behavior.
    """

    def __init__(self) -> None:
        self.headers: dict = {}
        self.path_params: dict = {}
        self.query_params: dict = {}


async def _run_permission(perm: Permission, role: str) -> bool:
    """Invoke the require_permission checker directly; True = admitted."""
    checker = require_permission(perm)
    try:
        result = await checker(request=_FakeRequest(), user=_user(role))
    except HTTPException as exc:
        assert exc.status_code == 403
        return False
    assert isinstance(result, CurrentUser)
    return True


async def _run_require_admin(role: str) -> bool:
    try:
        await require_admin(current_user=_user(role))
    except HTTPException as exc:
        assert exc.status_code == 403
        return False
    return True


def _gates_for(router, method: str, path: str):
    """Return (set of enforced Permission, has_require_admin) for a route by
    walking its resolved dependant tree."""
    perms: set = set()
    has_admin = False
    seen: set = set()

    def walk(dep):
        nonlocal has_admin
        if id(dep) in seen:
            return
        seen.add(id(dep))
        call = getattr(dep, "call", None)
        name = getattr(call, "__name__", "")
        if name == "permission_checker" and getattr(call, "__closure__", None):
            fv = call.__code__.co_freevars
            vals = {n: c.cell_contents for n, c in zip(fv, call.__closure__)}
            p = vals.get("permission")
            if isinstance(p, Permission):
                perms.add(p)
        if name == "require_admin":
            has_admin = True
        for d in getattr(dep, "dependencies", []) or []:
            walk(d)

    for route in router.routes:
        if route.path == path and method in route.methods:
            for d in route.dependant.dependencies:
                walk(d)
            return perms, has_admin
    raise AssertionError(f"route not found: {method} {path}")


# --------------------------------------------------------------------------
# (1) BEHAVIORAL — the model's tiers, faithfully enforced
# --------------------------------------------------------------------------

# (permission, role, expected_admit) per ROLE_DEFAULT_PERMISSIONS.
_BEHAVIOR_CASES = [
    # campaigns:* — USER and admins hold create/update; readonly does not.
    (Permission.CAMPAIGNS_CREATE, "readonly", False),
    (Permission.CAMPAIGNS_CREATE, "user", True),
    (Permission.CAMPAIGNS_CREATE, "tenant_admin", True),
    (Permission.CAMPAIGNS_UPDATE, "readonly", False),
    (Permission.CAMPAIGNS_UPDATE, "user", True),
    (Permission.CAMPAIGNS_UPDATE, "tenant_admin", True),
    # calls:delete — NOT held by USER (model design); tenant_admin holds it.
    (Permission.CALLS_DELETE, "readonly", False),
    (Permission.CALLS_DELETE, "user", False),
    (Permission.CALLS_DELETE, "tenant_admin", True),
    # connectors:* — USER holds create/update/delete.
    (Permission.CONNECTORS_CREATE, "readonly", False),
    (Permission.CONNECTORS_CREATE, "user", True),
    (Permission.CONNECTORS_UPDATE, "user", True),
    (Permission.CONNECTORS_DELETE, "user", True),
    (Permission.CONNECTORS_DELETE, "tenant_admin", True),
    # billing:* — NOT held by USER; tenant_admin holds both.
    (Permission.BILLING_UPDATE, "readonly", False),
    (Permission.BILLING_UPDATE, "user", False),
    (Permission.BILLING_UPDATE, "tenant_admin", True),
    (Permission.BILLING_ADMIN, "user", False),
    (Permission.BILLING_ADMIN, "tenant_admin", True),
]


@pytest.mark.parametrize("perm,role,expected", _BEHAVIOR_CASES)
async def test_require_permission_matches_role_defaults(perm, role, expected):
    assert await _run_permission(perm, role) is expected


@pytest.mark.parametrize(
    "perm",
    [Permission.CAMPAIGNS_CREATE, Permission.CALLS_DELETE, Permission.BILLING_ADMIN],
)
async def test_platform_admin_bypasses_all_gates(perm):
    assert await _run_permission(perm, "platform_admin") is True


@pytest.mark.parametrize(
    "perm",
    [Permission.CAMPAIGNS_CREATE, Permission.CALLS_DELETE, Permission.BILLING_UPDATE],
)
async def test_unknown_role_fails_safe_denied(perm):
    # normalize_role() maps garbage -> readonly (fail-safe), which holds none
    # of these -> 403.
    assert await _run_permission(perm, "wat-is-this-role") is False


@pytest.mark.parametrize(
    "role,expected",
    [
        ("readonly", False),
        ("user", False),
        ("tenant_admin", True),
        ("partner_admin", True),
        ("platform_admin", True),
        ("garbage-role", False),  # fail-safe
    ],
)
async def test_require_admin_gates_dnc_mutations(role, expected):
    assert await _run_require_admin(role) is expected


# --------------------------------------------------------------------------
# (2) WIRING — each mapped route carries the expected gate
# --------------------------------------------------------------------------

# (router, method, path, expected Permission)
_PERMISSION_ROUTES = [
    (campaigns_router, "POST", "/campaigns/", Permission.CAMPAIGNS_CREATE),
    (campaigns_router, "POST", "/campaigns/apply-tts-config", Permission.CAMPAIGNS_UPDATE),
    (campaigns_router, "PUT", "/campaigns/{campaign_id}", Permission.CAMPAIGNS_UPDATE),
    (campaigns_router, "POST", "/campaigns/{campaign_id}/start", Permission.CAMPAIGNS_UPDATE),
    (campaigns_router, "POST", "/campaigns/{campaign_id}/pause", Permission.CAMPAIGNS_UPDATE),
    (campaigns_router, "POST", "/campaigns/{campaign_id}/stop", Permission.CAMPAIGNS_UPDATE),
    (campaigns_router, "POST", "/campaigns/{campaign_id}/contacts", Permission.CAMPAIGNS_UPDATE),
    (campaigns_router, "PATCH", "/campaigns/{campaign_id}/contacts/{contact_id}", Permission.CAMPAIGNS_UPDATE),
    (campaigns_router, "DELETE", "/campaigns/{campaign_id}/contacts/{contact_id}", Permission.CAMPAIGNS_UPDATE),
    (calls_router, "POST", "/calls/{call_id}/hangup", Permission.CALLS_DELETE),
    (connectors_router, "POST", "/connectors/{type}/disconnect", Permission.CONNECTORS_DELETE),
    (connectors_router, "POST", "/connectors/authorize", Permission.CONNECTORS_CREATE),
    (connectors_router, "DELETE", "/connectors/{connector_id}", Permission.CONNECTORS_DELETE),
    (connectors_router, "POST", "/connectors/{connector_id}/refresh", Permission.CONNECTORS_UPDATE),
    (billing_router, "POST", "/billing/create-checkout-session", Permission.BILLING_UPDATE),
    (billing_router, "POST", "/billing/portal", Permission.BILLING_UPDATE),
    (billing_router, "POST", "/billing/cancel", Permission.BILLING_ADMIN),
]


@pytest.mark.parametrize("router,method,path,perm", _PERMISSION_ROUTES)
def test_route_carries_expected_permission_gate(router, method, path, perm):
    perms, _ = _gates_for(router, method, path)
    assert perm in perms, f"{method} {path} missing {perm}; found {perms}"


_ADMIN_ROUTES = [
    (dnc_router, "POST", "/dnc/"),
    (dnc_router, "POST", "/dnc/bulk-import"),
    (dnc_router, "POST", "/dnc/caller-opt-out"),
    (dnc_router, "DELETE", "/dnc/{entry_id}"),
]


@pytest.mark.parametrize("router,method,path", _ADMIN_ROUTES)
def test_dnc_mutation_routes_require_admin(router, method, path):
    _, has_admin = _gates_for(router, method, path)
    assert has_admin, f"{method} {path} missing require_admin gate"


# Guardrail: routes intentionally left open must NOT carry a mutation gate,
# so we don't silently lock out readonly principals from reads.
_OPEN_ROUTES = [
    (campaigns_router, "POST", "/campaigns/preview-prompt"),
    (campaigns_router, "GET", "/campaigns/"),
    (dnc_router, "GET", "/dnc/"),
    (dnc_router, "GET", "/dnc/check"),
]


@pytest.mark.parametrize("router,method,path", _OPEN_ROUTES)
def test_open_routes_have_no_mutation_gate(router, method, path):
    perms, has_admin = _gates_for(router, method, path)
    assert not perms and not has_admin, (
        f"{method} {path} unexpectedly gated: perms={perms} admin={has_admin}"
    )
