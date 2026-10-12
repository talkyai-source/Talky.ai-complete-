"""Suspension correctness + access (tenancy-rbac-admin-2, -4, -9).

* -2: status/history reads are platform_admin only (suspension_events rows carry
  fraud notes + evidence about other customers).
* -4: a tenant/partner suspend->restore cycle must only flip the membership rows
  that the suspension itself flipped (never removed / pending / separately
  suspended members).
* -9: get_status must not raise on a timed suspension (TIMESTAMPTZ is aware).
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import CurrentUser, get_current_user, get_suspension_service
from app.api.v1.endpoints.suspensions import router as suspensions_router
from app.domain.services import suspension_service as svc_mod
from app.domain.services.suspension_service import (
    SuspensionService,
    SuspensionStatus,
    SuspensionType,
)

TENANT = UUID("11111111-1111-1111-1111-111111111111")
TENANT_B = UUID("22222222-2222-2222-2222-222222222222")
PARTNER = UUID("33333333-3333-3333-3333-333333333333")
ADMIN = UUID("44444444-4444-4444-4444-444444444444")


# ---------------------------------------------------------------------------
# In-memory stand-in for the handful of statements the service issues.
# It interprets the WHERE filters it sees (tenant_id / status / id = ANY), so a
# statement that forgets a filter changes the result, not just a string match.
# ---------------------------------------------------------------------------
def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


class FakeDb:
    def __init__(self):
        self.tenant_users: list[dict] = []
        self.events: list[dict] = []
        self.partner_tenants: dict[UUID, list[UUID]] = {}

    def add_member(self, tenant_id, status):
        row = {"id": uuid4(), "user_id": uuid4(), "tenant_id": tenant_id, "status": status}
        self.tenant_users.append(row)
        return row

    def statuses(self, tenant_id):
        return {r["id"]: r["status"] for r in self.tenant_users if r["tenant_id"] == tenant_id}

    # --- connection API -----------------------------------------------------
    async def execute(self, sql, *args):
        await self._run(sql, args)

    async def fetch(self, sql, *args):
        return await self._run(sql, args)

    async def fetchrow(self, sql, *args):
        rows = await self._run(sql, args)
        return rows[0] if rows else None

    def transaction(self):
        @asynccontextmanager
        async def _tx():
            yield

        return _tx()

    async def _run(self, sql, args):
        s = _norm(sql)
        if s.startswith("INSERT INTO suspension_events"):
            (sid, ttype, tid, stype, cat, desc, ev, at, until, by, active) = args
            self.events.append(
                dict(suspension_id=sid, target_type=ttype, target_id=tid, evidence=ev,
                     is_active=active, suspended_until=until)
            )
            return []
        if s.startswith("UPDATE suspension_events SET evidence"):
            sid, ev = args[0], args[1]
            next(e for e in self.events if e["suspension_id"] == sid)["evidence"] = ev
            return []
        if s.startswith("UPDATE suspension_events SET is_active = FALSE"):
            sid = args[-1]
            next(e for e in self.events if e["suspension_id"] == sid)["is_active"] = False
            return []
        if s.startswith("SELECT suspension_id, evidence FROM suspension_events") or s.startswith(
            "SELECT suspension_id FROM suspension_events"
        ):
            ttype, tid = args[0], args[1]
            hits = [e for e in self.events if e["target_type"] == ttype
                    and e["target_id"] == tid and e["is_active"]]
            return [dict(h) for h in hits[-1:]]
        if s.startswith("SELECT id FROM tenants WHERE white_label_partner_id"):
            return [{"id": t} for t in self.partner_tenants.get(args[0], [])]
        if s.startswith("UPDATE tenant_users"):
            return self._update_tenant_users(s, args)
        return []  # tenants / white_label_partners / security_sessions updates

    def _update_tenant_users(self, s, args):
        new_status = re.search(r"SET status = '(\w+)'", s).group(1)
        where = s.split(" WHERE ", 1)[1]
        tenant_id = args[0]
        want_status = re.search(r"AND status = '(\w+)'", where)
        ids = None
        m = re.search(r"id = ANY\(\$(\d+)", where)
        if m:
            ids = set(args[int(m.group(1)) - 1])
        changed = []
        for r in self.tenant_users:
            if r["tenant_id"] != tenant_id:
                continue
            if want_status and r["status"] != want_status.group(1):
                continue
            if ids is not None and r["id"] not in ids and str(r["id"]) not in ids:
                continue
            r["status"] = new_status
            changed.append({"id": r["id"]})
        return changed if "RETURNING" in s else []


def _service(monkeypatch, db: FakeDb) -> SuspensionService:
    @asynccontextmanager
    async def _acquire(pool, tenant_id, **kw):
        yield db

    monkeypatch.setattr(svc_mod, "acquire_with_tenant", _acquire)

    async def _no_prop(self, **kw):
        return {"success": [], "failed": []}

    monkeypatch.setattr(SuspensionService, "_propagate_to_services", _no_prop)
    return SuspensionService(db_pool=None)


def _mixed_tenant(db, tenant_id):
    return {
        "active": db.add_member(tenant_id, "active"),
        "active2": db.add_member(tenant_id, "active"),
        "removed": db.add_member(tenant_id, "removed"),
        "pending": db.add_member(tenant_id, "pending"),
        "suspended": db.add_member(tenant_id, "suspended"),  # suspended on its own earlier
    }


# ---------------------------------------------------------------------------
# tenancy-rbac-admin-4
# ---------------------------------------------------------------------------
async def test_tenant_suspend_restore_only_reactivates_originally_active_members(monkeypatch):
    db = FakeDb()
    m = _mixed_tenant(db, TENANT)
    other = db.add_member(TENANT_B, "active")
    svc = _service(monkeypatch, db)

    await svc.suspend_tenant(TENANT, SuspensionType.ADMIN, "abuse", "x", suspended_by=ADMIN)
    st = db.statuses(TENANT)
    assert st[m["active"]["id"]] == "suspended" and st[m["active2"]["id"]] == "suspended"
    assert st[m["removed"]["id"]] == "removed"
    assert st[m["pending"]["id"]] == "pending"
    assert db.statuses(TENANT_B)[other["id"]] == "active"  # tenant predicate

    await svc.restore_tenant(TENANT, ADMIN, "ok")
    st = db.statuses(TENANT)
    assert st[m["active"]["id"]] == "active" and st[m["active2"]["id"]] == "active"
    assert st[m["removed"]["id"]] == "removed", "removed member regained access"
    assert st[m["pending"]["id"]] == "pending", "pending member was activated"
    assert st[m["suspended"]["id"]] == "suspended", "separately suspended member was reactivated"


async def test_tenant_restore_of_legacy_event_never_resurrects_removed_or_pending(monkeypatch):
    """Events written before the fix carry no recorded member ids."""
    db = FakeDb()
    m = _mixed_tenant(db, TENANT)
    svc = _service(monkeypatch, db)
    sid = uuid4()
    db.events.append(dict(suspension_id=sid, target_type="tenant", target_id=TENANT,
                          evidence=None, is_active=True, suspended_until=None))
    for r in db.tenant_users:  # legacy suspend flipped every row
        if r["status"] in ("active", "suspended"):
            r["status"] = "suspended"

    await svc.restore_tenant(TENANT, ADMIN, "ok")
    st = db.statuses(TENANT)
    assert st[m["removed"]["id"]] == "removed"
    assert st[m["pending"]["id"]] == "pending"


async def test_partner_suspend_restore_only_reactivates_originally_active_members(monkeypatch):
    db = FakeDb()
    m = _mixed_tenant(db, TENANT)
    m_b = _mixed_tenant(db, TENANT_B)
    db.partner_tenants[PARTNER] = [TENANT, TENANT_B]
    svc = _service(monkeypatch, db)

    await svc.suspend_partner(PARTNER, SuspensionType.ADMIN, "abuse", "x", suspended_by=ADMIN)
    await svc.restore_partner(PARTNER, ADMIN, "ok")

    for members, tid in ((m, TENANT), (m_b, TENANT_B)):
        st = db.statuses(tid)
        assert st[members["active"]["id"]] == "active"
        assert st[members["removed"]["id"]] == "removed"
        assert st[members["pending"]["id"]] == "pending"
        assert st[members["suspended"]["id"]] == "suspended"


async def test_caller_evidence_survives_membership_bookkeeping(monkeypatch):
    db = FakeDb()
    _mixed_tenant(db, TENANT)
    svc = _service(monkeypatch, db)
    await svc.suspend_tenant(TENANT, SuspensionType.ADMIN, "abuse", "x",
                             evidence={"ticket": "T-1"}, suspended_by=ADMIN)
    ev = db.events[0]["evidence"]
    ev = json.loads(ev) if isinstance(ev, str) else ev
    assert ev["ticket"] == "T-1"


# ---------------------------------------------------------------------------
# tenancy-rbac-admin-9
# ---------------------------------------------------------------------------
def _async(fn):
    async def _inner(*a, **k):
        return fn(*a, **k)

    return _inner


class _Pool:
    def __init__(self, active_row, history):
        self._active, self._history = active_row, history

    def acquire(self):
        outer = self

        @asynccontextmanager
        async def _cm():
            yield SimpleNamespace(
                fetchrow=_async(lambda *a: outer._active),
                fetch=_async(lambda *a: outer._history),
            )

        return _cm()


def _event_row(until):
    now = datetime.now(timezone.utc)
    return dict(
        suspension_id=uuid4(), created_at=now, target_type="user", target_id=uuid4(),
        suspension_type="TEMPORARY", reason_category="abuse", reason_description="x",
        evidence=None, suspended_at=now, suspended_until=until, restored_at=None,
        suspended_by=None, restored_by=None, restore_reason=None, is_active=True,
        propagated_services=[], propagation_confirmed_at=None, appeal_submitted_at=None,
        appeal_reason=None, appeal_reviewed_by=None, appeal_decision=None,
        appeal_response=None, audit_log_id=None,
    )


async def test_get_status_timed_suspension_in_future_is_scheduled_restore():
    row = _event_row(datetime.now(timezone.utc) + timedelta(hours=2))
    svc = SuspensionService(db_pool=_Pool(row, [row]))
    res = await svc.get_status(user_id=row["target_id"])
    assert res.status == SuspensionStatus.SCHEDULED_RESTORE and res.is_suspended


async def test_get_status_timed_suspension_in_past_is_active():
    row = _event_row(datetime.now(timezone.utc) - timedelta(hours=2))
    svc = SuspensionService(db_pool=_Pool(row, [row]))
    res = await svc.get_status(user_id=row["target_id"])
    assert res.status == SuspensionStatus.ACTIVE and res.is_suspended


# ---------------------------------------------------------------------------
# tenancy-rbac-admin-2
# ---------------------------------------------------------------------------
class _StubService:
    async def get_status(self, user_id=None, tenant_id=None):
        return SimpleNamespace(
            target_type=SimpleNamespace(value="user" if user_id else "tenant"),
            target_id=user_id or tenant_id,
            status=SimpleNamespace(value="restored"),
            is_suspended=False,
            active_suspension=None,
            suspension_history=[],
        )


READ_ROUTES = [
    f"/admin/suspensions/users/{ADMIN}/status",
    f"/admin/suspensions/users/{ADMIN}/history",
    f"/admin/suspensions/tenants/{TENANT}/status",
]


def _client(role: str) -> TestClient:
    app = FastAPI()
    app.include_router(suspensions_router)
    user = CurrentUser(id=str(ADMIN), email="a@example.com", tenant_id=str(TENANT_B), role=role)
    # A customer role that legitimately holds both read permissions.
    user.set_permissions({"users:read", "tenants:read"})
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_suspension_service] = lambda: _StubService()
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("role", ["tenant_admin", "readonly", "user", "partner_admin"])
@pytest.mark.parametrize("path", READ_ROUTES)
def test_non_platform_roles_cannot_read_any_suspension_record(role, path):
    resp = _client(role).get(path)
    assert resp.status_code == 403, (role, path, resp.status_code)


@pytest.mark.parametrize("path", READ_ROUTES)
def test_platform_admin_can_read_suspension_records(path):
    resp = _client("platform_admin").get(path)
    assert resp.status_code == 200, (path, resp.status_code, resp.text[:200])


async def test_get_status_reads_a_freshly_written_event_with_null_propagated_services():
    """suspend_* never fills propagated_services, so a new row has NULL there."""
    row = _event_row(None)
    row["propagated_services"] = None
    svc = SuspensionService(db_pool=_Pool(row, [row]))
    res = await svc.get_status(user_id=row["target_id"])
    assert res.status == SuspensionStatus.ACTIVE and res.is_suspended
    assert res.active_suspension.propagated_services == []
