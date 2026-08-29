"""Admin tenant lifecycle: create, archive, pagination and the list N+1 guard.

Why this file exists
====================
`goals.md` §12 ("Client Management and 200-Tenant Validation") could not
start: `admin/tenants.py` exposed list / detail / quota / suspend / resume
only. There was **no way to create a tenant** through the API, so a seeder
had nothing to call, and no archive verb for the "Archive/cancel a tenant"
acceptance item.

Two further §12 items were unmet by the list endpoint:

* "Master admin can search, filter and **paginate** 200 tenants" — the list
  endpoint had no `limit`/`offset` and loaded every tenant into memory.
* "Identify **N+1 queries**" — the old implementation issued one query for
  the tenant page plus *two per tenant* (user count + campaign count), i.e.
  ``1 + 2N`` = **401 queries for 200 tenants**. The rewrite folds both
  counts into correlated subqueries, so the endpoint issues a constant
  **2** queries (one COUNT for the total, one page SELECT) no matter how
  many tenants come back. ``test_list_query_count_is_bounded_*`` is the
  regression guard for exactly that.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_current_user,
    get_db_client,
    require_platform_admin,
)
from app.api.v1.endpoints.admin import tenants as tenants_module

PLATFORM_ADMIN = CurrentUser(
    id="00000000-0000-0000-0000-0000000000aa",
    email="ops@talky.ai",
    tenant_id="00000000-0000-0000-0000-0000000000bb",
    role="platform_admin",
)

TENANT_ADMIN = CurrentUser(
    id="00000000-0000-0000-0000-0000000000cc",
    email="boss@client.example",
    tenant_id="00000000-0000-0000-0000-0000000000dd",
    role="tenant_admin",
)

DEFAULT_CALLING_RULES = {
    "max_concurrent_calls": 10,
    "timezone": "America/New_York",
}


# ---------------------------------------------------------------------------
# Fake asyncpg pool that records every statement it is asked to run.
# ---------------------------------------------------------------------------

class FakeConn:
    """Records (method, sql, args) for every call; answers from `responder`."""

    def __init__(self, responder):
        self._responder = responder
        self.queries: list[tuple[str, str, tuple]] = []

    def _record(self, method: str, sql: str, args: tuple):
        self.queries.append((method, " ".join(sql.split()), args))
        return self._responder(method, sql, args)

    async def fetch(self, sql, *args):
        return self._record("fetch", sql, args)

    async def fetchrow(self, sql, *args):
        return self._record("fetchrow", sql, args)

    async def fetchval(self, sql, *args):
        return self._record("fetchval", sql, args)

    async def execute(self, sql, *args):
        return self._record("execute", sql, args)

    def transaction(self):
        return _FakeTx()

    # -- helpers for assertions --------------------------------------------
    def sql_text(self) -> str:
        return "\n".join(q[1] for q in self.queries)

    def statements_matching(self, needle: str) -> list[str]:
        return [q[1] for q in self.queries if needle.lower() in q[1].lower()]


class _FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


class FakeAuditLogger:
    def __init__(self):
        self.events: list[dict] = []

    async def log(self, **kwargs):
        self.events.append(kwargs)
        return "00000000-0000-0000-0000-00000000000e"


def make_client(responder, *, user: CurrentUser | None = PLATFORM_ADMIN):
    """Mount the tenants router on a bare app with a recording fake pool."""
    conn = FakeConn(responder)
    audit = FakeAuditLogger()
    app = FastAPI()
    app.include_router(tenants_module.router, prefix="/api/v1/admin")
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=FakePool(conn))
    app.dependency_overrides[get_audit_logger] = lambda: audit
    if user is not None:
        # Override the *authentication* dependency, not the authorization
        # gate: require_platform_admin still runs for real, so these tests
        # exercise the actual 403 behaviour for a non-platform role.
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False), conn, audit


# ---------------------------------------------------------------------------
# Responders
# ---------------------------------------------------------------------------

def tenant_row(idx: int, *, status: str = "active", tid: str | None = None) -> dict:
    return {
        "id": tid or f"00000000-0000-0000-0000-{idx:012d}",
        "business_name": f"Tenant {idx:03d}",
        "plan_id": "free",
        "plan_name": "Free",
        "minutes_used": 0,
        "minutes_allocated": 30,
        "subscription_status": status,
        "calling_rules": dict(DEFAULT_CALLING_RULES),
        "created_at": "2026-08-29T00:00:00+00:00",
        "user_count": 1,
        "campaign_count": 2,
    }


def list_responder(total: int, page_size_cap: int = 10_000):
    """Answers the two statements the list endpoint issues."""

    def _respond(method, sql, args):
        low = sql.lower()
        if method == "fetchval" and "count(*)" in low:
            return total
        if method == "fetch":
            # emulate LIMIT/OFFSET against a `total`-row table
            limit, offset = args[-2], args[-1]
            n = max(0, min(limit, total - offset, page_size_cap))
            return [tenant_row(offset + i) for i in range(n)]
        raise AssertionError(f"unexpected statement: {method} {sql}")

    return _respond


NEW_TENANT_ID = "00000000-0000-0000-0000-0000000f0001"


def create_responder(*, existing: dict | None = None, plan_minutes: int = 30):
    """Answers the statements POST /tenants issues."""

    def _respond(method, sql, args):
        low = " ".join(sql.lower().split())
        if "from plans" in low:
            return {
                "id": args[0],
                "name": "Free",
                "minutes": plan_minutes,
                "concurrent_calls": 3,
            }
        if "pg_advisory_xact_lock" in low:
            return None
        if low.startswith("select") and "from tenants" in low:
            return existing
        if "insert into tenants" in low:
            return {
                "id": NEW_TENANT_ID,
                "business_name": args[0],
                "plan_id": args[1],
                "minutes_allocated": args[2],
                "minutes_used": 0,
                "subscription_status": args[3],
                "calling_rules": dict(DEFAULT_CALLING_RULES),
                "created_at": "2026-08-29T00:00:00+00:00",
            }
        if "update tenants" in low:
            rules = dict(DEFAULT_CALLING_RULES)
            rules["max_concurrent_calls"] = args[1]
            return {"calling_rules": rules}
        if "insert into tenant_quotas" in low:
            return None
        raise AssertionError(f"unexpected statement: {method} {sql}")

    return _respond


ARCHIVE_ID = "00000000-0000-0000-0000-0000000a0001"


def archive_responder(*, found: bool = True, status: str = "active"):
    def _respond(method, sql, args):
        low = " ".join(sql.lower().split())
        if low.startswith("select"):
            if not found:
                return None
            return {"id": ARCHIVE_ID, "subscription_status": status}
        if "update tenants" in low:
            return {"id": ARCHIVE_ID, "subscription_status": "cancelled"}
        raise AssertionError(f"unexpected statement: {method} {sql}")

    return _respond


# ===========================================================================
# 1. Create
# ===========================================================================

def test_create_tenant_returns_usable_tenant_with_defaults():
    """POST /admin/tenants creates a tenant on the default plan with quota."""
    client, conn, audit = make_client(create_responder())

    r = client.post("/api/v1/admin/tenants", json={"business_name": "Acme Ltd"})

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == NEW_TENANT_ID
    assert body["business_name"] == "Acme Ltd"
    assert body["created"] is True
    # default plan + quota derived from the plan row, not invented here
    assert body["plan_id"] == "free"
    assert body["plan_name"] == "Free"
    assert body["minutes_allocated"] == 30
    assert body["minutes_used"] == 0
    assert body["status"] == "active"
    # concurrency limit falls back to the plan's concurrent_calls
    assert body["max_concurrent_calls"] == 3

    sql = conn.sql_text().lower()
    assert "insert into tenants" in sql
    # limits row for the new tenant, keyed by an explicit tenant_id
    assert conn.statements_matching("insert into tenant_quotas")
    assert any(NEW_TENANT_ID in str(q[2]) for q in conn.queries), (
        "tenant_quotas insert must bind the new tenant id explicitly"
    )
    assert audit.events, "tenant creation must be audited (goals.md §12)"
    assert audit.events[0]["event_type"].value == "tenant_created"


def test_create_tenant_honours_explicit_plan_and_limits():
    client, conn, _ = make_client(create_responder(plan_minutes=5000))
    r = client.post(
        "/api/v1/admin/tenants",
        json={
            "business_name": "Big Co",
            "plan_id": "enterprise",
            "minutes_allocated": 9000,
            "max_concurrent_calls": 42,
            "subscription_status": "trialing",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["minutes_allocated"] == 9000
    assert body["max_concurrent_calls"] == 42
    assert body["status"] == "trialing"


def test_create_tenant_is_idempotent_on_business_name():
    """Re-running a seeder must not duplicate the tenant."""
    already = {
        "id": "00000000-0000-0000-0000-0000000f0002",
        "business_name": "Acme Ltd",
        "plan_id": "free",
        "plan_name": "Free",
        "minutes_allocated": 30,
        "minutes_used": 7,
        "subscription_status": "active",
        "calling_rules": dict(DEFAULT_CALLING_RULES),
        "created_at": "2026-08-01T00:00:00+00:00",
    }
    client, conn, _ = make_client(create_responder(existing=already))

    r = client.post("/api/v1/admin/tenants", json={"business_name": "Acme Ltd"})

    assert r.status_code == 200, r.text  # 200, not 201 — nothing was created
    body = r.json()
    assert body["created"] is False
    assert body["id"] == already["id"]
    assert not conn.statements_matching("INSERT INTO tenants"), (
        "idempotent replay must not insert a second tenant row"
    )


def test_create_tenant_rejects_unknown_plan():
    def _respond(method, sql, args):
        if "from plans" in sql.lower():
            return None
        raise AssertionError(f"unexpected statement after unknown plan: {sql}")

    client, conn, _ = make_client(_respond)
    r = client.post(
        "/api/v1/admin/tenants",
        json={"business_name": "Ghost", "plan_id": "does-not-exist"},
    )
    assert r.status_code == 400
    assert not conn.statements_matching("INSERT INTO tenants")


def test_create_tenant_rejects_blank_business_name():
    client, conn, _ = make_client(create_responder())
    r = client.post("/api/v1/admin/tenants", json={"business_name": "   "})
    assert r.status_code == 422
    assert conn.queries == []


# ===========================================================================
# 2. Pagination + stable total
# ===========================================================================

def test_list_paginates_and_reports_a_stable_total():
    client, conn, _ = make_client(list_responder(total=200))

    first = client.get("/api/v1/admin/tenants", params={"limit": 50, "offset": 0})
    assert first.status_code == 200, first.text
    assert len(first.json()) == 50
    assert first.headers["X-Total-Count"] == "200"

    client2, _, _ = make_client(list_responder(total=200))
    third = client2.get("/api/v1/admin/tenants", params={"limit": 50, "offset": 150})
    assert third.status_code == 200
    assert len(third.json()) == 50
    # total is the count of matching rows, NOT the size of the page
    assert third.headers["X-Total-Count"] == "200"

    client3, _, _ = make_client(list_responder(total=200))
    past_end = client3.get("/api/v1/admin/tenants", params={"limit": 50, "offset": 200})
    assert past_end.json() == []
    assert past_end.headers["X-Total-Count"] == "200"


def test_list_limit_offset_are_validated():
    client, conn, _ = make_client(list_responder(total=5))
    assert client.get("/api/v1/admin/tenants", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/admin/tenants", params={"offset": -1}).status_code == 422
    assert client.get("/api/v1/admin/tenants", params={"limit": 100000}).status_code == 422


def test_list_row_shape_is_unchanged():
    """The Admin console consumes a bare array of these fields — keep it."""
    client, _, _ = make_client(list_responder(total=1))
    r = client.get("/api/v1/admin/tenants")
    assert r.status_code == 200, r.text
    item = r.json()[0]
    assert set(item) == {
        "id", "business_name", "plan_id", "plan_name", "minutes_used",
        "minutes_allocated", "status", "user_count", "campaign_count",
        "max_concurrent_calls", "created_at",
    }
    assert item["user_count"] == 1
    assert item["campaign_count"] == 2
    assert item["max_concurrent_calls"] == 10


# ===========================================================================
# 3. The N+1 regression guard
# ===========================================================================

@pytest.mark.parametrize("n", [1, 10, 200])
def test_list_query_count_is_bounded_regardless_of_tenant_count(n):
    """Before: 1 + 2N queries (401 at N=200). After: a constant 2."""
    client, conn, _ = make_client(list_responder(total=n))
    r = client.get("/api/v1/admin/tenants", params={"limit": 1000})
    assert r.status_code == 200, r.text
    assert len(r.json()) == n
    assert len(conn.queries) == 2, (
        f"list_tenants issued {len(conn.queries)} queries for {n} tenants: "
        f"{[q[1][:60] for q in conn.queries]}"
    )


def test_list_does_not_issue_per_tenant_count_queries():
    client, conn, _ = make_client(list_responder(total=200))
    client.get("/api/v1/admin/tenants", params={"limit": 1000})
    # the old shape: one `select id from user_profiles where tenant_id = ..`
    # and one for campaigns, per tenant.
    per_tenant = [
        s for s in conn.statements_matching("user_profiles")
        if "count(*)" in s.lower() and "from tenants" not in s.lower()
    ]
    assert len(per_tenant) <= 1, f"per-tenant count queries survived: {per_tenant}"


# ===========================================================================
# 4. Archive
# ===========================================================================

def test_archive_tenant_marks_it_cancelled_and_audits():
    client, conn, audit = make_client(archive_responder())
    r = client.post(f"/api/v1/admin/tenants/{ARCHIVE_ID}/archive")
    assert r.status_code == 200, r.text
    body = r.json()
    # 'cancelled' (two Ls) is the spelling CallGuard blocks on, so an
    # archived tenant genuinely cannot place calls.
    assert body["status"] == "cancelled"

    update = conn.statements_matching("UPDATE tenants")
    assert update, "archive must update the tenant row"
    assert "cancelled" in json.dumps([q[2] for q in conn.queries], default=str) \
        or "cancelled" in update[0].lower()
    # no child rows are deleted — archive is a soft, reversible state change
    assert not conn.statements_matching("DELETE FROM")
    assert audit.events and audit.events[0]["event_type"].value == "tenant_updated"


def test_archive_is_idempotent():
    client, conn, _ = make_client(archive_responder(status="cancelled"))
    r = client.post(f"/api/v1/admin/tenants/{ARCHIVE_ID}/archive")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    assert not conn.statements_matching("UPDATE tenants")


def test_archive_unknown_tenant_is_404():
    client, conn, _ = make_client(archive_responder(found=False))
    r = client.post(f"/api/v1/admin/tenants/{ARCHIVE_ID}/archive")
    assert r.status_code == 404
    assert not conn.statements_matching("UPDATE tenants")


def test_archive_is_reversible_through_the_existing_resume_verb():
    """`resume` is this file's un-do vocabulary; archive must not bypass it."""
    seen = {}

    def _respond(method, sql, args):
        low = " ".join(sql.lower().split())
        if low.startswith("select"):
            return {"id": ARCHIVE_ID, "subscription_status": "cancelled"}
        if "update tenants" in low:
            seen["update"] = low
            return {"id": ARCHIVE_ID, "subscription_status": "active"}
        raise AssertionError(sql)

    client, conn, _ = make_client(_respond)
    r = client.post(f"/api/v1/admin/tenants/{ARCHIVE_ID}/resume")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"


def test_archived_tenants_are_excluded_from_the_default_list():
    client, conn, _ = make_client(list_responder(total=3))
    client.get("/api/v1/admin/tenants")
    where = conn.sql_text().lower()
    assert "cancelled" in where or "cancelled" in str(
        [q[2] for q in conn.queries]
    ).lower(), "default list must filter archived tenants out"


def test_archived_tenants_are_visible_when_explicitly_requested():
    client, conn, _ = make_client(list_responder(total=3))
    client.get("/api/v1/admin/tenants", params={"include_archived": "true"})
    assert "cancelled" not in str([q[2] for q in conn.queries]).lower()

    client2, conn2, _ = make_client(list_responder(total=3))
    client2.get("/api/v1/admin/tenants", params={"status": "cancelled"})
    # an explicit status filter wins over the default exclusion
    assert "cancelled" in str([q[2] for q in conn2.queries]).lower()
    assert "is distinct from" not in conn2.sql_text().lower()


# ===========================================================================
# 5. Permission gates
# ===========================================================================

@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/v1/admin/tenants"),
        ("post", "/api/v1/admin/tenants"),
        ("post", f"/api/v1/admin/tenants/{ARCHIVE_ID}/archive"),
        ("post", f"/api/v1/admin/tenants/{ARCHIVE_ID}/suspend"),
        ("post", f"/api/v1/admin/tenants/{ARCHIVE_ID}/resume"),
    ],
)
def test_non_platform_admin_is_denied(method, path):
    client, conn, _ = make_client(list_responder(total=1), user=TENANT_ADMIN)
    kwargs = {"json": {"business_name": "Sneaky"}} if method == "post" else {}
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 403, r.text
    assert conn.queries == [], "a denied caller must not reach the database"


def test_new_routes_carry_the_platform_admin_gate():
    """Wiring guard: no new tenants route may ship without the strict gate."""
    for route in tenants_module.router.routes:
        names = set()
        stack = list(route.dependant.dependencies)
        while stack:
            dep = stack.pop()
            name = getattr(getattr(dep, "call", None), "__name__", "")
            if name:
                names.add(name)
            stack.extend(dep.dependencies)
        assert "require_platform_admin" in names, f"{route.path} is not gated"
        assert "require_admin" not in names, f"{route.path} uses the weak gate"
