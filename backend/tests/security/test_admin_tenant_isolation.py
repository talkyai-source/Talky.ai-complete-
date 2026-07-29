"""Cross-tenant isolation regression for the /admin/* operator routers.

Background
==========
``require_admin`` admits ``{tenant_admin, partner_admin, platform_admin}`` and
10 of 11 real accounts are ``tenant_admin``. Behind that gate, seven admin
routers queried platform-wide tables with NO tenant predicate, e.g.

    db_client.table("tenant_ai_credentials").select("*").execute()

RLS is BYPASSRLS for the application role in production, so the app-level
filter is the ONLY defence. Any customer's admin could therefore read, update
and DELETE any other customer's rows.

The fix splits the routers by what they actually operate on, because one blanket
answer would be wrong in one direction or the other:

TENANT-SCOPED (stay admin-level, filter by ``current_user.tenant_id``)
    rate_limits, webhooks_admin, api_keys, blocked_entities, call_guards.
    These tables are tenant-owned (``tenant_id`` on every row) and managing them
    is a legitimate customer self-service feature — raising them to
    platform_admin would lock customers out of their own configuration.

PLATFORM-WIDE (raised to ``require_platform_admin``)
    call_limits, abuse_monitoring. These name their target in the path/query
    (``/tenants/{tenant_id}/call-limits``, ``/partners/{partner_id}/limits``,
    ``?tenant_id=``), mutate commercial terms and quotas, and CRUD global
    (``tenant_id IS NULL``) DNC entries and fraud-detection rules. A tenant
    filter would be attacker-chosen rather than caller-derived, so the correct
    fix is the higher bar, not a filter. Customers keep the tenant-scoped DNC
    equivalent at ``/api/v1/dnc`` (endpoints/dnc.py).

``require_admin`` itself is deliberately UNCHANGED — ~30 other routes rely on
its current semantics (see test_require_admin_semantics_unchanged below).

Non-vacuity
===========
Every read test asserts BOTH that tenant B's row is absent AND that tenant A's
own row is present. That matters because these endpoints wrap their queries in
``except Exception: return []`` — a test that only checked "B not in result"
would pass even if the query blew up and returned nothing at all.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import (
    CurrentUser,
    require_admin,
    require_admin_tenant,
    require_platform_admin,
)
from app.api.v1.endpoints import (
    abuse_monitoring,
    api_keys,
    blocked_entities,
    call_guards,
    call_limits,
    rate_limits,
    webhooks_admin,
)

TENANT_A = str(uuid4())  # the caller
TENANT_B = str(uuid4())  # the victim


# ---------------------------------------------------------------------------
# Fake postgrest-style client (app.core.postgres_adapter.Client surface)
# ---------------------------------------------------------------------------

class _Result:
    """Mirrors _ExecutionResult: endpoints read .data and .error."""

    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    """Fluent builder honouring the exact `.eq()` predicates production emits.

    Emulating AND-ed equality filters is the whole point: if an endpoint omits
    `.eq("tenant_id", ...)`, this fake returns the other tenant's rows exactly
    as Postgres would with BYPASSRLS, and the test fails.
    """

    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._filters = []
        self._op = "select"
        self._payload = None
        self._limit = None

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, data):
        self._op = "insert"
        self._payload = data
        return self

    def update(self, data):
        self._op = "update"
        self._payload = data
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters.append((col, None if val is None else str(val)))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _match(self, row):
        for col, val in self._filters:
            rv = row.get(col)
            rv = None if rv is None else str(rv)
            if rv != val:
                return False
        return True

    def execute(self):
        rows = self._db.tables.setdefault(self._table, [])
        if self._op == "select":
            hits = [dict(r) for r in rows if self._match(r)]
            if self._limit is not None:
                hits = hits[: self._limit]
            return _Result(hits)
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for p in payload:
                rows.append(dict(p))
            return _Result([dict(p) for p in payload])
        if self._op == "update":
            changed = []
            for r in rows:
                if self._match(r):
                    r.update(self._payload)
                    changed.append(dict(r))
            return _Result(changed)
        if self._op == "delete":
            kept, removed = [], []
            for r in rows:
                (removed if self._match(r) else kept).append(r)
            self._db.tables[self._table] = kept
            return _Result([dict(r) for r in removed])
        return _Result([])


class FakeDB:
    def __init__(self, tables=None):
        self.tables = tables or {}

    def table(self, name):
        return _Query(self, name)


def _admin(tenant_id=TENANT_A, role="tenant_admin") -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="admin@tenant-a.example",
        tenant_id=tenant_id,
        role=role,
    )


def _two_tenant_rows(**extra):
    """One row for each tenant, sharing a shape; ids are stable per tenant."""
    base_a = {"id": "row-a", "tenant_id": TENANT_A}
    base_b = {"id": "row-b", "tenant_id": TENANT_B}
    base_a.update(extra)
    base_b.update(extra)
    return [base_a, base_b]


# ===========================================================================
# 1. rate_limits.py — tenant_telephony_threshold_policies
# ===========================================================================

def _rate_limit_db():
    return FakeDB({
        "tenant_telephony_threshold_policies": _two_tenant_rows(
            name="policy", calls_per_minute=60, calls_per_hour=1000,
            calls_per_day=10000, active=True, created_at="2026-07-01",
        )
    })


class TestRateLimits:
    async def test_list_returns_only_callers_tenant(self):
        out = await rate_limits.list_rate_limits(
            current_user=_admin(), db_client=_rate_limit_db()
        )
        ids = {r.id for r in out}
        assert "row-a" in ids, "caller lost access to their own policy (vacuous test guard)"
        assert "row-b" not in ids, "LEAK: another tenant's rate-limit policy returned"

    async def test_update_cannot_touch_other_tenant_row(self):
        db = _rate_limit_db()
        with pytest.raises(HTTPException) as ei:
            await rate_limits.update_rate_limit(
                rule_id="row-b",
                request=rate_limits.UpdateRateLimitRequest(active=False),
                current_user=_admin(),
                db_client=db,
            )
        assert ei.value.status_code == 404
        victim = [r for r in db.tables["tenant_telephony_threshold_policies"]
                  if r["id"] == "row-b"][0]
        assert victim["active"] is True, "LEAK: cross-tenant rate-limit write landed"

    async def test_update_own_row_still_works(self):
        db = _rate_limit_db()
        out = await rate_limits.update_rate_limit(
            rule_id="row-a",
            request=rate_limits.UpdateRateLimitRequest(active=False),
            current_user=_admin(),
            db_client=db,
        )
        assert out.id == "row-a" and out.active is False


# ===========================================================================
# 2. webhooks_admin.py — webhook_endpoints / webhook_deliveries
# ===========================================================================

def _webhook_db():
    return FakeDB({
        "webhook_endpoints": _two_tenant_rows(
            url="https://hook.example/secret-token", events=["call.done"],
            active=True, created_at="2026-07-01",
        ),
        "webhook_deliveries": [
            {"id": "d-a", "tenant_id": TENANT_A, "webhook_id": "row-a",
             "event": "call.done", "status": "ok", "created_at": "2026-07-01"},
            {"id": "d-b", "tenant_id": TENANT_B, "webhook_id": "row-b",
             "event": "call.done", "status": "ok", "created_at": "2026-07-01"},
        ],
    })


class TestWebhooksAdmin:
    async def test_list_returns_only_callers_webhooks(self):
        out = await webhooks_admin.list_webhooks(
            current_user=_admin(), db_client=_webhook_db()
        )
        ids = {r.id for r in out}
        assert "row-a" in ids
        assert "row-b" not in ids, "LEAK: another tenant's webhook URL returned"

    async def test_delete_cannot_remove_other_tenant_webhook(self):
        db = _webhook_db()
        with pytest.raises(HTTPException) as ei:
            await webhooks_admin.delete_webhook(
                webhook_id="row-b", current_user=_admin(), db_client=db
            )
        assert ei.value.status_code == 404
        assert any(r["id"] == "row-b" for r in db.tables["webhook_endpoints"]), \
            "LEAK: cross-tenant webhook was deleted"

    async def test_delete_own_webhook_still_works(self):
        db = _webhook_db()
        assert (await webhooks_admin.delete_webhook(
            webhook_id="row-a", current_user=_admin(), db_client=db
        ))["success"] is True
        assert not any(r["id"] == "row-a" for r in db.tables["webhook_endpoints"])

    async def test_test_webhook_cannot_probe_other_tenant(self):
        with pytest.raises(HTTPException) as ei:
            await webhooks_admin.test_webhook(
                webhook_id="row-b", current_user=_admin(), db_client=_webhook_db()
            )
        assert ei.value.status_code == 404

    async def test_deliveries_scoped_even_with_attacker_supplied_webhook_id(self):
        # Own tenant sees its own history...
        mine = await webhooks_admin.list_webhook_deliveries(
            webhook_id=None, current_user=_admin(), db_client=_webhook_db()
        )
        assert {d.id for d in mine} == {"d-a"}
        # ...and naming the victim's webhook_id cannot pivot across tenants.
        pivot = await webhooks_admin.list_webhook_deliveries(
            webhook_id="row-b", current_user=_admin(), db_client=_webhook_db()
        )
        assert pivot == [], "LEAK: webhook_id param pivoted to another tenant"


# ===========================================================================
# 3. api_keys.py — tenant_ai_credentials  (highest-impact leak)
# ===========================================================================

def _api_key_db():
    return FakeDB({
        "tenant_ai_credentials": _two_tenant_rows(
            name="prod-key", provider="openai",
            created_at="2026-07-01", revoked_at=None,
        )
    })


class TestApiKeys:
    async def test_list_returns_only_callers_credentials(self):
        out = await api_keys.list_api_keys(
            current_user=_admin(), db_client=_api_key_db()
        )
        ids = {r.id for r in out}
        assert "row-a" in ids
        assert "row-b" not in ids, "LEAK: another tenant's AI credential returned"

    async def test_revoke_cannot_kill_other_tenants_credential(self):
        db = _api_key_db()
        with pytest.raises(HTTPException) as ei:
            await api_keys.revoke_api_key(
                key_id="row-b", current_user=_admin(), db_client=db
            )
        assert ei.value.status_code == 404
        victim = [r for r in db.tables["tenant_ai_credentials"] if r["id"] == "row-b"][0]
        assert victim["revoked_at"] is None, \
            "LEAK: cross-tenant credential revocation succeeded (DoS)"

    async def test_revoke_own_credential_still_works(self):
        db = _api_key_db()
        assert (await api_keys.revoke_api_key(
            key_id="row-a", current_user=_admin(), db_client=db
        ))["success"] is True

    async def test_create_stamps_callers_tenant(self):
        db = FakeDB({"tenant_ai_credentials": []})
        await api_keys.create_api_key(
            request=api_keys.CreateApiKeyRequest(name="k", provider="openai"),
            current_user=_admin(),
            db_client=db,
        )
        assert db.tables["tenant_ai_credentials"][0]["tenant_id"] == TENANT_A


# ===========================================================================
# 4. blocked_entities.py — dnc_entries
# ===========================================================================

def _blocked_db():
    rows = _two_tenant_rows(
        phone_number="+15551230000", normalized_number="+15551230000",
        reason="req", source="manual", created_at="2026-07-01",
    )
    # A GLOBAL suppression (tenant_id NULL) must not be deletable by a tenant.
    rows.append({
        "id": "row-global", "tenant_id": None,
        "phone_number": "+15559999999", "normalized_number": "+15559999999",
        "reason": "platform", "source": "manual", "created_at": "2026-07-01",
    })
    return FakeDB({"dnc_entries": rows})


class TestBlockedEntities:
    async def test_list_returns_only_callers_entries(self):
        out = await blocked_entities.list_blocked_entities(
            current_user=_admin(), db_client=_blocked_db()
        )
        ids = {r.id for r in out}
        assert "row-a" in ids
        assert "row-b" not in ids, "LEAK: another tenant's DNC entry returned"

    async def test_unblock_cannot_remove_other_tenant_entry(self):
        db = _blocked_db()
        with pytest.raises(HTTPException) as ei:
            await blocked_entities.unblock_entity(
                entity_id="row-b", current_user=_admin(), db_client=db
            )
        assert ei.value.status_code == 404
        assert any(r["id"] == "row-b" for r in db.tables["dnc_entries"]), \
            "LEAK: cross-tenant DNC deletion (compliance breach)"

    async def test_unblock_cannot_remove_global_entry(self):
        db = _blocked_db()
        with pytest.raises(HTTPException):
            await blocked_entities.unblock_entity(
                entity_id="row-global", current_user=_admin(), db_client=db
            )
        assert any(r["id"] == "row-global" for r in db.tables["dnc_entries"]), \
            "LEAK: a tenant deleted a PLATFORM-WIDE DNC suppression"

    async def test_unblock_own_entry_still_works(self):
        db = _blocked_db()
        assert (await blocked_entities.unblock_entity(
            entity_id="row-a", current_user=_admin(), db_client=db
        ))["success"] is True


# ===========================================================================
# 5. call_guards.py — call_guard_decisions
# ===========================================================================

def _guard_db():
    return FakeDB({
        "call_guard_decisions": _two_tenant_rows(
            decision="block", reason="velocity", enabled=True,
            created_at="2026-07-01",
        )
    })


class TestCallGuards:
    async def test_list_returns_only_callers_decisions(self):
        out = await call_guards.list_call_guards(
            current_user=_admin(), db_client=_guard_db()
        )
        ids = {r.id for r in out}
        assert "row-a" in ids
        assert "row-b" not in ids, "LEAK: another tenant's call-guard decision returned"

    async def test_toggle_cannot_disable_other_tenants_guard(self):
        db = _guard_db()
        with pytest.raises(HTTPException) as ei:
            await call_guards.toggle_call_guard(
                rule_id="row-b",
                request=call_guards.ToggleCallGuardRequest(enabled=False),
                current_user=_admin(),
                db_client=db,
            )
        assert ei.value.status_code == 404
        victim = [r for r in db.tables["call_guard_decisions"] if r["id"] == "row-b"][0]
        assert victim["enabled"] is True, \
            "LEAK: one tenant disabled another tenant's fraud guard"

    async def test_toggle_own_guard_still_works(self):
        db = _guard_db()
        out = await call_guards.toggle_call_guard(
            rule_id="row-a",
            request=call_guards.ToggleCallGuardRequest(enabled=False),
            current_user=_admin(),
            db_client=db,
        )
        assert out.id == "row-a" and out.enabled is False


# ===========================================================================
# 6. Platform-wide routers — gate raised to platform_admin
# ===========================================================================

def _dep_names(route) -> set:
    """Callable names anywhere in a route's resolved dependency tree."""
    names, seen = set(), set()
    stack = list(route.dependant.dependencies)
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


_PLATFORM_ROUTERS = {
    "call_limits.py": call_limits.router,
    "abuse_monitoring.py": abuse_monitoring.router,
}


def test_platform_routers_are_platform_admin_only():
    """Every route on the two platform-wide routers carries
    require_platform_admin and NOT the weaker require_admin."""
    offenders, checked = [], 0
    for label, router in _PLATFORM_ROUTERS.items():
        for route in router.routes:
            checked += 1
            names = _dep_names(route)
            key = f"{label} {sorted(m for m in route.methods if m != 'HEAD')} {route.path}"
            if "require_platform_admin" not in names:
                offenders.append(f"{key} -> missing require_platform_admin (deps={sorted(names)})")
            if "require_admin" in names:
                offenders.append(f"{key} -> still carries require_admin (LEAK)")
    assert checked >= 15, f"expected >=15 platform routes, found {checked} (discovery broken)"
    assert not offenders, "platform routes not gated:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin", "user", "readonly", "nonsense"])
async def test_platform_gate_denies_everyone_below_platform_admin(role):
    with pytest.raises(HTTPException) as ei:
        await require_platform_admin(current_user=_admin(role=role))
    assert ei.value.status_code == 403


async def test_platform_gate_admits_the_real_operator():
    user = _admin(role="platform_admin")
    assert await require_platform_admin(current_user=user) is user


# ===========================================================================
# 7. require_admin_tenant — the tenant-scoped gate, and the blast-radius guard
# ===========================================================================

async def test_require_admin_tenant_admits_tenant_admin_with_tenant():
    """Customer admins keep access to their own tenant's config."""
    user = _admin()
    assert await require_admin_tenant(current_user=user) is user


async def test_require_admin_tenant_fails_closed_without_tenant():
    """A NULL tenant_id would otherwise produce an unfiltered platform-wide
    query — the exact fail-open this gate closes."""
    with pytest.raises(HTTPException) as ei:
        await require_admin_tenant(current_user=_admin(tenant_id=None))
    assert ei.value.status_code == 403


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin", "platform_admin"])
async def test_require_admin_semantics_unchanged(role):
    """BLAST-RADIUS GUARD.

    ``require_admin`` is imported by ~30 routes (dnc.py mutations,
    telephony_providers, tenant_ai_credentials, admin/health/*, admin/base.py,
    admin/actions.py, ...) that legitimately need tenant_admin. This fix must
    therefore change the ROUTERS, never the shared gate. If someone 'fixes' a
    future leak by tightening require_admin instead, this fails — and so do
    tests/unit/test_core_mutation_role_gates.py and
    tests/unit/test_admin_cross_tenant_scoping.py, which assert the same.
    """
    user = _admin(role=role)
    assert await require_admin(current_user=user) is user
