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


# Membership/evidence transition cases now run against the canonical migrated
# restricted-role database in integration/test_formal_account_holds.py. The old
# SQL emulator could not prove transactional authority, overlap or row ownership.

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
        async def _tx():
            yield

        @asynccontextmanager
        async def _cm():
            yield SimpleNamespace(
                transaction=lambda: _tx(),
                execute=_async(lambda *a: None),
                fetchrow=_async(lambda sql,*a: {"is_active":False} if "FROM user_profiles" in sql else outer._active),
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
    async def get_propagation_status(self, suspension_id):
        return {"suspension_id":str(suspension_id),"propagation_status":"unverified",
                "services_confirmed":[],"pending_services":["telephony_policy_check"]}

    async def get_status(self, user_id=None, tenant_id=None, partner_id=None):
        if partner_id:
            return svc_mod.SuspensionStatusResponse(target_type="partner",target_id=partner_id,
                status="restored",is_suspended=False,active_suspension=None,suspension_history=[],effective_status="active")
        return SimpleNamespace(
            target_type=SimpleNamespace(value="user" if user_id else "tenant"),
            target_id=user_id or tenant_id,
            status=SimpleNamespace(value="restored"),
            is_suspended=False,
            effective_status="active",
            remaining_hold_ids=[],
            active_suspension=None,
            suspension_history=[],
            history_complete=True,
            history_limit=200,
        )


READ_ROUTES = [
    f"/admin/suspensions/propagation-status/{TENANT}",
    f"/admin/suspensions/users/{ADMIN}/status",
    f"/admin/suspensions/users/{ADMIN}/history",
    f"/admin/suspensions/tenants/{TENANT}/status",
    f"/admin/suspensions/partners/{PARTNER}/status",
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
