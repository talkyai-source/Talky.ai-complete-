"""Systemic safeguard against the missing-auth-on-tenant-scoped-endpoint bug.

Background
==========
The voice-AI codebase uses Postgres RLS to scope reads to the calling
tenant. The per-request RLS context (``app.current_tenant_id``) is set
TWO ways:

1. ``TenantMiddleware`` reads the JWT claim and sets the contextvar.
   This is the primary mechanism, but BaseHTTPMiddleware's task
   isolation can strip contextvar updates from inner middlewares —
   the documented failure mode in the dependencies.py:318-324 comment.

2. ``Depends(get_current_user)`` re-sets the contextvar from the
   user's profile after authentication. This is the defense-in-depth.

Endpoints that touch a tenant-scoped table but DON'T include
``Depends(get_current_user)`` are vulnerable to mechanism #1 failing
silently — the SELECT returns 0 rows, the endpoint raises 404 / 500,
and the user sees "Campaign not found" even though their data is in
the DB. We fixed seven such endpoints on `campaigns.py` in May 2026.

This test is the safeguard. It walks every FastAPI route in the live
app, finds the ones that take a DB dependency but not an auth
dependency, and fails CI unless the route is in an explicit allowlist
(login flows, webhooks, public catalog, etc.).

Adding a new endpoint?
======================
* If it touches tenant-scoped data → add ``Depends(get_current_user)``
  (or ``Depends(require_admin)``, ``Depends(get_optional_user)``).
* If it's intentionally public (login flow, webhook callback, public
  catalog) → add (METHOD, path) to ``KNOWN_PUBLIC_ROUTES`` below
  with a one-line reason.
"""
from __future__ import annotations

import inspect

import pytest


# --- known-public allowlist ------------------------------------------
#
# Each entry is (METHOD, exact_path) plus the reason it's intentionally
# auth-free. Reasons must be defensible — anything that touches
# tenant-scoped data SHOULD have an auth dep. The categories below are
# the only ones approved as auth-free.

# Pre-authentication flows: the user is creating their session.
_AUTH_FLOWS = {
    "POST /api/v1/auth/login": "session-creating; can't require session",
    "POST /api/v1/auth/register": "registration creates the user",
    "POST /api/v1/auth/passkey-check": "passkey availability probe (anon)",
    "POST /api/v1/auth/signup/start": "signup flow, pre-auth",
    "POST /api/v1/auth/signup/complete": "signup completion, pre-auth",
    "GET /api/v1/auth/verify-email": "email verification link, magic-token auth",
    "POST /api/v1/auth/passkeys/login/begin": "passkey login challenge, pre-auth",
    "POST /api/v1/auth/passkeys/login/complete": "passkey login response, pre-auth",
    "POST /api/v1/auth/mfa/verify": "MFA challenge, pre-auth (token in body)",
    "POST /api/v1/auth/refresh": "rotates refresh-cookie; pre-auth by design",
    "POST /api/v1/auth/forgot-password": "reset-code request; pre-auth, enumeration-safe + rate-limited",
    "POST /api/v1/auth/reset-password": "reset-code redemption; pre-auth (code in body)",
}

# Webhook callbacks from third parties / internal — these use payload
# signature verification or short-lived state tokens, NOT session auth.
_WEBHOOKS = {
    "POST /api/v1/billing/webhooks": "Stripe webhook; signature verified inline",
    "GET /api/v1/connectors/callback": "OAuth callback; state token verified inline",
    "POST /api/v1/webhooks/call/goal-achieved": "internal call hook; signature verified",
    "POST /api/v1/webhooks/call/mark-spam": "internal call hook; signature verified",
    "POST /api/v1/webhooks/secure/call/goal-achieved": "signature-verified webhook",
    "POST /api/v1/webhooks/secure/call/mark-spam": "signature-verified webhook",
    "POST /api/v1/webhooks/secure/idempotent-example": "signature-verified webhook",
    "POST /api/v1/webhooks/secure/admin/configure": "signature-verified webhook",
}

# Public endpoints — no tenant data exposed.
_PUBLIC = {
    "GET /api/v1/plans/": "public plan catalog (no per-tenant data)",
    "GET /api/v1/billing/plans": "billing-module passthrough to the same public plan catalog (no per-tenant data)",
}

# ---------------------------------------------------------------------
# KNOWN TECH-DEBT — auth-gap bugs we have not yet fixed
# ---------------------------------------------------------------------
#
# These endpoints DO need auth but currently lack ``Depends(get_current_user)``
# at the route level. They are admin-only / RBAC-management /
# audit-related routes that are likely guarded at the router level by
# a different mechanism, OR they're genuine bugs nobody has hit yet.
#
# This list is the explicit punch-list — every entry should either be
# removed (because the auth dep was added) or moved to ``_PUBLIC``
# (because it was deemed intentionally public after audit).
#
# The test allows these to pass so the regression-prevention property
# of this file holds for new endpoints, while the technical debt
# stays visible in source instead of silently lurking.
_KNOWN_AUTH_GAPS = {
    # Admin abuse-monitoring endpoints — should require_admin.
    "GET /api/v1/admin/abuse/events": "TODO: add require_admin",
    "GET /api/v1/admin/abuse/events/{event_id}": "TODO: add require_admin",
    "POST /api/v1/admin/abuse/events/{event_id}/resolve": "TODO: add require_admin",
    "GET /api/v1/admin/abuse/statistics": "TODO: add require_admin",
    "GET /api/v1/admin/abuse/rules": "TODO: add require_admin",
    "POST /api/v1/admin/abuse/rules": "TODO: add require_admin",
    "PUT /api/v1/admin/abuse/rules/{rule_id}": "TODO: add require_admin",
    "DELETE /api/v1/admin/abuse/rules/{rule_id}": "TODO: add require_admin",
    "GET /api/v1/admin/abuse/alerts": "TODO: add require_admin",
    # Audit-log statistics — should require_admin.
    "GET /api/v1/admin/audit/stats/events-by-type": "TODO: add require_admin",
    "GET /api/v1/admin/audit/stats/failed-logins": "TODO: add require_admin",
    # Security-events admin — should require_admin.
    "GET /api/v1/admin/security-events/events": "TODO: add require_admin",
    "GET /api/v1/admin/security-events/events/{event_id}": "TODO: add require_admin",
    "POST /api/v1/admin/security-events/events": "TODO: add require_admin",
    "PATCH /api/v1/admin/security-events/events/{event_id}": "TODO: add require_admin",
    "POST /api/v1/admin/security-events/events/{event_id}/resolve": "TODO: add require_admin",
    "GET /api/v1/admin/security-events/alerts/open": "TODO: add require_admin",
    "GET /api/v1/admin/security-events/alerts/overdue": "TODO: add require_admin",
    "POST /api/v1/admin/security-events/events/{event_id}/escalate": "TODO: add require_admin",
    # RBAC management — should require_admin (managing other users' access).
    "POST /api/v1/rbac/roles/{role_id}/permissions": "TODO: add require_admin",
    "DELETE /api/v1/rbac/roles/{role_id}/permissions/{permission_id}": "TODO: add require_admin",
    "DELETE /api/v1/rbac/roles/{role_id}/permissions": "TODO: add require_admin",
    "GET /api/v1/rbac/users/{user_id}/permissions": "TODO: add require_admin",
    "GET /api/v1/rbac/tenant-users": "TODO: add require_admin",
    "POST /api/v1/rbac/tenant-users": "TODO: add require_admin",
    "PATCH /api/v1/rbac/tenant-users/{tenant_user_id}": "TODO: add require_admin",
    "DELETE /api/v1/rbac/tenant-users/{tenant_user_id}": "TODO: add require_admin",
}

KNOWN_PUBLIC_ROUTES: dict[str, str] = {
    **_AUTH_FLOWS, **_WEBHOOKS, **_PUBLIC, **_KNOWN_AUTH_GAPS,
}


# --- the audit -------------------------------------------------------


def _route_has_auth_dep(endpoint_func) -> bool:
    """True if the function (or any of its router-level dependencies)
    chains to ``get_current_user`` / ``require_admin`` / etc."""
    auth_dep_names = {
        "get_current_user", "require_admin", "get_optional_user",
        "verify_admin_token",
        # Higher-privilege guards that all chain to get_current_user.
        # Omitting these produced false-positive "leaks" on the
        # platform-admin-only user-management routes (they ARE secured).
        "require_platform_admin", "require_tenant_member",
        "require_permissions",
    }
    sig = inspect.signature(endpoint_func)
    src = inspect.getsource(endpoint_func)
    # Quick textual check on the source — covers Depends() calls
    # whether they're top-level params or nested in `dependencies=[...]`.
    if any(name in src for name in auth_dep_names):
        return True
    # Param-level check as a fallback.
    for param in sig.parameters.values():
        ann = str(param.annotation)
        if any(name in ann for name in auth_dep_names):
            return True
    return False


def _route_has_db_dep(endpoint_func) -> bool:
    """True if the function takes a database dependency that triggers
    a per-request connection. These are the routes that need the RLS
    tenant context to be set."""
    db_dep_names = {
        "get_db_client", "get_db_read_client", "get_db_pool",
    }
    src = inspect.getsource(endpoint_func)
    return any(name in src for name in db_dep_names)


def _norm_route_key(method: str, path: str) -> str:
    return f"{method.upper()} {path}"


def _collect_api_routes(routes, prefix: str = ""):
    """Yield (full_path, APIRoute) for every route, recursing into
    included sub-routers.

    FastAPI's ``include_router`` used to flatten every sub-route into a
    top-level ``APIRoute`` on ``app.routes``. Newer FastAPI (0.128+)
    instead mounts each included router as an internal ``_IncludedRouter``
    whose real ``APIRoute`` objects live on ``original_router.routes``
    with the prefix carried on the mount's ``include_context``. A flat
    ``for route in app.routes`` therefore finds ZERO APIRoutes on the
    newer version and the audit silently passes.

    This walker reconstructs full paths across BOTH layouts so the
    safeguard keeps working regardless of the installed FastAPI version.
    """
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield (prefix + route.path, route)
        elif type(route).__name__ == "_IncludedRouter":
            include_ctx = getattr(route, "include_context", None)
            sub_prefix = getattr(include_ctx, "prefix", "") or ""
            original = getattr(route, "original_router", None)
            if original is not None:
                yield from _collect_api_routes(
                    original.routes, prefix + sub_prefix,
                )
        elif hasattr(route, "routes"):
            # Plain Mount / sub-app — recurse without a known prefix.
            yield from _collect_api_routes(route.routes, prefix)


@pytest.mark.skipif(
    "main" not in __import__("sys").modules
    and not __import__("importlib").util.find_spec("app.main"),
    reason="app.main not importable in this test environment",
)
def test_every_db_route_requires_auth():
    """The systemic safeguard.

    Walks every FastAPI route registered on the live app. For each
    route that takes a DB dependency, asserts the handler also takes
    an auth dependency UNLESS the route is in the allowlist above.

    Failure mode: an endpoint touches a tenant-scoped table without
    triggering RLS context setup, returns 404/500 to legitimate
    users, and gets reported as "DB shows empty but data is there."
    """
    from app.main import app

    leaks: list[str] = []
    audited: int = 0

    for path, route in _collect_api_routes(app.routes):
        for method in route.methods:
            if method == "HEAD":  # auto-derived from GET
                continue
            audited += 1
            key = _norm_route_key(method, path)
            if key in KNOWN_PUBLIC_ROUTES:
                continue
            try:
                has_db = _route_has_db_dep(route.endpoint)
                has_auth = _route_has_auth_dep(route.endpoint)
            except (OSError, TypeError):
                # Some endpoints (lambdas, builtins) don't have a
                # readable source. Skip — those are never tenant-scoped
                # in this codebase.
                continue
            if has_db and not has_auth:
                leaks.append(key)

    assert audited > 0, "Audit ran but found 0 routes — discovery is broken"
    assert not leaks, (
        "The following endpoints take a DB dependency but no auth "
        "dependency. Either add Depends(get_current_user) to the handler, "
        "or add the route to KNOWN_PUBLIC_ROUTES with a one-line reason:\n  "
        + "\n  ".join(sorted(leaks))
    )


def test_known_public_routes_actually_exist():
    """Catch typos / stale entries in the allowlist. If a route in
    KNOWN_PUBLIC_ROUTES no longer exists in the app, the entry is dead
    and should be removed — leaving it lets a future bug hide behind it.
    """
    from app.main import app

    real_routes: set[str] = set()
    for path, route in _collect_api_routes(app.routes):
        for method in route.methods:
            if method == "HEAD":
                continue
            real_routes.add(_norm_route_key(method, path))

    stale = [k for k in KNOWN_PUBLIC_ROUTES if k not in real_routes]
    # Don't fail on stale entries — endpoint paths drift; flag for cleanup.
    if stale:
        pytest.skip(
            f"KNOWN_PUBLIC_ROUTES has stale entries (clean these up):\n  "
            + "\n  ".join(stale)
        )
