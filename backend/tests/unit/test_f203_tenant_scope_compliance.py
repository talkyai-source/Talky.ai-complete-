"""F2-03 (compliance slice): enforcement and abuse queries name their tenant.

Production has run its application role with BYPASSRLS, so a statement that
reads or writes tenant-owned rows has to carry its own ``tenant_id`` predicate.
Each fake connection below records the (sql, args) of every statement, so a
test proves both that the predicate is in the SQL and that the right tenant
value is bound to it.

Three statements gained a predicate or lost an f-string:

* GET /admin/security-events/events/{id} compared the row's tenant only after
  reading it, so a tenant caller's statement could return another tenant's row.
* Holding a tenant's members updated ``tenant_users`` by row id alone.
* ``_check_rapid_calls`` formatted its window into the SQL text, which left a
  statement that already filtered by tenant impossible to review.

The remaining tests pin the claims the baseline exceptions rest on: tenant
callers always carry the predicate on the shared security-event routes, and
the suspension routes that read platform-wide state are platform-admin only.
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import security_events as se
from app.api.v1.endpoints import suspensions
from app.domain.services import abuse_detection
from app.domain.services.suspension_service import SuspensionService

TENANT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
EVENT_ID = UUID("11111111-1111-4111-8111-111111111111")
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)


def _squash(sql):
    return re.sub(r"\s+", " ", sql).strip()


def bound(sql, args, column):
    """The value bound to ``column = $n`` in sql, or None when there is no such predicate."""
    match = re.search(rf"(?<![\w.])(?:\w+\.)?{column}\s*=\s*\$(\d+)", sql, re.I)
    return args[int(match.group(1)) - 1] if match else None


class CapturingConn:
    """Fake asyncpg connection that records every statement and its arguments."""

    def __init__(self, *, fetchrow=None, fetch=None, fetchval=None):
        self.calls = []
        self._results = {"fetchrow": fetchrow, "fetch": fetch, "fetchval": fetchval}

    async def _run(self, method, sql, args):
        self.calls.append((method, _squash(sql), args))
        result = self._results[method]
        return result(sql, args) if callable(result) else result

    async def fetchrow(self, sql, *args):
        return await self._run("fetchrow", sql, args)

    async def fetch(self, sql, *args):
        return await self._run("fetch", sql, args)

    async def fetchval(self, sql, *args):
        return await self._run("fetchval", sql, args)


def patch_acquire(monkeypatch, module, conn):
    """Replace module.acquire_with_tenant; the returned list records each tenant argument."""
    acquired = []

    @asynccontextmanager
    async def fake(pool, tenant_id, **_kwargs):
        acquired.append(tenant_id)
        yield conn

    monkeypatch.setattr(module, "acquire_with_tenant", fake)
    return acquired


def caller(role, tenant_id=TENANT_A):
    return {"id": str(uuid4()), "role": role, "tenant_id": tenant_id}


def event_row(tenant_id):
    return {
        "event_id": EVENT_ID, "created_at": NOW, "event_type": "suspicious_activity",
        "severity": "HIGH", "status": "open", "tenant_id": UUID(tenant_id), "user_id": None,
        "title": "t", "description": "d", "detection_source": "manual", "evidence": None,
        "assigned_to": None, "resolved_at": None, "resolved_by": None,
        "resolution_notes": None, "auto_action_taken": None, "sla_deadline": None,
    }


# ---------------------------------------------------------------------------
# GET /admin/security-events/events/{event_id}
# ---------------------------------------------------------------------------

async def test_get_security_event_filters_by_the_callers_tenant_in_sql(monkeypatch):
    conn = CapturingConn(fetchrow=event_row(TENANT_A))
    acquired = patch_acquire(monkeypatch, se, conn)

    out = await se.get_security_event(
        event_id=EVENT_ID, current_user=caller("tenant_admin"), db_pool=object()
    )

    [(_, sql, args)] = conn.calls
    assert bound(sql, args, "event_id") == EVENT_ID
    assert bound(sql, args, "tenant_id") == TENANT_A, sql
    assert acquired == [TENANT_A]
    assert out.event_id == EVENT_ID


async def test_get_security_event_cross_tenant_id_is_not_found(monkeypatch):
    stored = event_row(TENANT_B)

    def database(sql, args):  # honours the tenant predicate, as Postgres does
        wanted = bound(sql, args, "tenant_id")
        return stored if wanted is None or str(wanted) == str(stored["tenant_id"]) else None

    patch_acquire(monkeypatch, se, CapturingConn(fetchrow=database))

    with pytest.raises(HTTPException) as refused:
        await se.get_security_event(
            event_id=EVENT_ID, current_user=caller("tenant_admin", TENANT_A), db_pool=object()
        )

    assert refused.value.status_code == 404


async def test_get_security_event_still_refuses_a_foreign_row_the_database_returned(monkeypatch):
    patch_acquire(monkeypatch, se, CapturingConn(fetchrow=event_row(TENANT_B)))

    with pytest.raises(HTTPException) as refused:
        await se.get_security_event(
            event_id=EVENT_ID, current_user=caller("tenant_admin", TENANT_A), db_pool=object()
        )

    assert refused.value.status_code == 403


async def test_get_security_event_platform_admin_keeps_the_id_only_lookup(monkeypatch):
    conn = CapturingConn(fetchrow=event_row(TENANT_B))
    acquired = patch_acquire(monkeypatch, se, conn)

    out = await se.get_security_event(
        event_id=EVENT_ID, current_user=caller("platform_admin", TENANT_A), db_pool=object()
    )

    [(_, sql, args)] = conn.calls
    assert bound(sql, args, "tenant_id") is None and args == (EVENT_ID,)
    assert acquired == [None]
    assert str(out.tenant_id) == TENANT_B


# ---------------------------------------------------------------------------
# SuspensionService._hold_account_members
# ---------------------------------------------------------------------------

async def test_member_hold_update_is_bound_to_each_rows_own_tenant():
    first, second, removed = uuid4(), uuid4(), uuid4()
    rows = [
        {"id": first, "tenant_id": UUID(TENANT_A), "status": "active", "updated_at": NOW},
        {"id": second, "tenant_id": UUID(TENANT_B), "status": "active", "updated_at": NOW},
        {"id": removed, "tenant_id": UUID(TENANT_B), "status": "removed", "updated_at": NOW},
    ]
    conn = CapturingConn(fetch=rows, fetchval=NOW)

    saved = await SuspensionService(db_pool=None)._hold_account_members(
        conn, [UUID(TENANT_A), UUID(TENANT_B)], []
    )

    updates = [(sql, args) for method, sql, args in conn.calls if sql.startswith("UPDATE tenant_users")]
    assert [bound(sql, args, "id") for sql, args in updates] == [first, second]
    assert [bound(sql, args, "tenant_id") for sql, args in updates] == [
        UUID(TENANT_A), UUID(TENANT_B),
    ], updates
    assert all("status='suspended'" in sql for sql, _ in updates)
    assert [member["id"] for member in saved] == [str(first), str(second)]


# ---------------------------------------------------------------------------
# AbuseDetectionService._check_rapid_calls
# ---------------------------------------------------------------------------

async def test_rapid_call_check_binds_tenant_number_and_window(monkeypatch):
    conn = CapturingConn(fetchval=4)
    acquired = patch_acquire(monkeypatch, abuse_detection, conn)
    detector = abuse_detection.AbuseDetectionService(db_pool=object())

    events = await detector._check_rapid_calls(
        TENANT_A, "+14155550100", threshold=3, window_minutes=7
    )

    [(_, sql, args)] = conn.calls
    assert bound(sql, args, "tenant_id") == TENANT_A
    assert bound(sql, args, "phone_number") == "+14155550100"
    assert 7 in args, "the window must be a bound parameter"
    assert not re.search(r"INTERVAL\s*'", sql, re.I), "the window must not be formatted into the SQL"
    assert acquired == [TENANT_A]
    assert [event.abuse_type for event in events] == [abuse_detection.AbuseType.REPEAT_NUMBER]
    assert events[0].details["window_minutes"] == 7 and events[0].details["recent_calls"] == 4


async def test_rapid_call_check_below_threshold_reports_nothing(monkeypatch):
    patch_acquire(monkeypatch, abuse_detection, CapturingConn(fetchval=2))

    assert await abuse_detection.AbuseDetectionService(db_pool=object())._check_rapid_calls(
        TENANT_A, "+14155550100"
    ) == []


# ---------------------------------------------------------------------------
# Claims behind the baseline exceptions
# ---------------------------------------------------------------------------

def _security_event_calls():
    audit = AsyncMock()
    return {
        "update": lambda user, db: se.update_security_event(
            event_id=EVENT_ID, data=se.SecurityEventUpdate(status="investigating"),
            current_user=user, audit_logger=audit, db_pool=db),
        "resolve": lambda user, db: se.resolve_security_event(
            event_id=EVENT_ID, resolution_notes="done", current_user=user,
            audit_logger=audit, db_pool=db),
        "escalate": lambda user, db: se.escalate_event(
            event_id=EVENT_ID, reason="urgent", current_user=user, audit_logger=audit, db_pool=db),
        "open_alerts": lambda user, db: se.get_open_alerts(current_user=user, db_pool=db),
        "overdue_alerts": lambda user, db: se.get_overdue_alerts(current_user=user, db_pool=db),
        "list": lambda user, db: se.list_security_events(
            status=None, severity=None, tenant_id=None, assigned_to=None, detection_source=None,
            limit=100, offset=0, event_type=None, event_group=None, user_query=None,
            partner_id=None, start_date=None, end_date=None, before_time=None, before_id=None,
            current_user=user, db_pool=db),
    }


@pytest.mark.parametrize("route", sorted(_security_event_calls()))
async def test_security_event_routes_bind_a_tenant_callers_own_tenant(monkeypatch, route):
    conn = CapturingConn(fetchrow=event_row(TENANT_A), fetch=[])
    acquired = patch_acquire(monkeypatch, se, conn)

    await _security_event_calls()[route](caller("tenant_admin", TENANT_A), object())

    [(_, sql, args)] = conn.calls
    assert bound(sql, args, "tenant_id") == TENANT_A, sql
    assert acquired == [TENANT_A]


@pytest.mark.parametrize("route", sorted(_security_event_calls()))
async def test_security_event_routes_are_estate_wide_only_for_a_platform_admin(monkeypatch, route):
    conn = CapturingConn(fetchrow=event_row(TENANT_B), fetch=[])
    acquired = patch_acquire(monkeypatch, se, conn)

    await _security_event_calls()[route](caller("platform_admin", TENANT_A), object())

    [(_, sql, args)] = conn.calls
    assert bound(sql, args, "tenant_id") is None
    assert acquired == [None]


async def test_security_event_list_refuses_another_tenant_before_any_sql(monkeypatch):
    conn = CapturingConn(fetch=[])
    patch_acquire(monkeypatch, se, conn)

    with pytest.raises(HTTPException) as refused:
        await se.list_security_events(
            status=None, severity=None, tenant_id=UUID(TENANT_B), assigned_to=None,
            detection_source=None, limit=100, offset=0, event_type=None, event_group=None,
            user_query=None, partner_id=None, start_date=None, end_date=None,
            before_time=None, before_id=None, current_user=caller("tenant_admin", TENANT_A),
            db_pool=object(),
        )

    assert refused.value.status_code == 403 and conn.calls == []


def _route_dependencies(route):
    names, stack = set(), list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        names.add(getattr(getattr(dependency, "call", None), "__name__", ""))
        stack.extend(dependency.dependencies)
    return names


@pytest.mark.parametrize("method,path", [
    ("GET", "/admin/suspensions/propagation-status/{suspension_id}"),
    ("GET", "/admin/suspensions/users/{user_id}/status"),
    ("GET", "/admin/suspensions/users/{user_id}/history"),
    ("GET", "/admin/suspensions/tenants/{tenant_id}/status"),
    ("GET", "/admin/suspensions/partners/{partner_id}/status"),
    ("POST", "/admin/suspensions/{suspension_id}/appeal"),
    ("POST", "/admin/suspensions/{suspension_id}/appeal/review"),
])
def test_suspension_routes_that_read_platform_wide_state_require_a_platform_admin(method, path):
    [route] = [
        r for r in suspensions.router.routes if r.path == path and method in r.methods
    ]

    assert "require_platform_admin" in _route_dependencies(route)
