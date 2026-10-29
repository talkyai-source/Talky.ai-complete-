"""auth-sessions-9: break-glass approve/create_session compared tz-aware
timestamptz values (asyncpg) with naive datetime.utcnow() and always raised."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from types import SimpleNamespace

import pytest

from app.core.security.emergency_access import (
    EmergencyAccess,
    EmergencyScenario,
    EmergencyStatus,
)


class FakeConn:
    def __init__(self, row):
        self.row = row
        self.writes = []

    async def fetchrow(self, sql, *args):
        if "FROM user_mfa" in sql:
            return {"enabled": True, "totp_secret_enc": "test", "last_used_at": None}
        return self.row

    async def execute(self, sql, *args):
        self.writes.append(args)
        return "UPDATE 1"


class FakePool:
    def __init__(self, row):
        self.conn = FakeConn(row)

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _row(status, **over):
    row = {
        "request_id": uuid4(), "requestor_id": uuid4(),
        "scenario": EmergencyScenario.SECURITY_INCIDENT.value,
        "status": status, "approvers_required": 1,
        "approvals": ([{"approver_id": str(uuid4()), "method": "totp", "verification": "totp_step_up"}]
                      if status == EmergencyStatus.APPROVED.value else []),
        # asyncpg returns timestamptz as tz-aware datetimes
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
        "session_created_at": None, "requested_access": ["platform:admin"],
    }
    row.update(over)
    return row


def _auth(user_id):
    return {"auth_request": object(), "current_user": SimpleNamespace(id=str(user_id))}


@pytest.fixture(autouse=True)
def _verified_authority_for_timestamp_tests(monkeypatch):
    from app.core.security import emergency_access as module

    @asynccontextmanager
    async def authorized(service, request, user):
        yield service.db_pool.conn

    async def budget(service, user_id):
        return None

    monkeypatch.setattr(EmergencyAccess, "_authorized_write", authorized)
    monkeypatch.setattr(EmergencyAccess, "_claim_approval_attempt", budget)
    monkeypatch.setattr(module, "decrypt_totp_secret", lambda encrypted: "test")
    monkeypatch.setattr(module, "verify_totp_step", lambda *args, **kwargs: datetime.now(timezone.utc))


def _no_naive(args):
    for a in args:
        if isinstance(a, datetime):
            assert a.tzinfo is not None, f"naive datetime written to timestamptz: {a!r}"


async def test_approve_works_with_timezone_aware_expiry():
    pool = FakePool(_row(EmergencyStatus.PENDING.value))
    approver = uuid4()
    result = await EmergencyAccess(pool).approve(str(uuid4()), approver, verification_code="123456", **_auth(approver))
    assert result["approved"] is True and result["fully_approved"] is True
    for args in pool.conn.writes:
        _no_naive(args)


async def test_approve_expires_a_lapsed_request_instead_of_crashing():
    row = _row(EmergencyStatus.PENDING.value,
               expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    pool = FakePool(row)
    with pytest.raises(ValueError, match="expired"):
        approver = uuid4()
        await EmergencyAccess(pool).approve(str(uuid4()), approver, verification_code="123456", **_auth(approver))


async def test_create_session_works_with_timezone_aware_expiry():
    pool = FakePool(_row(EmergencyStatus.APPROVED.value))
    session = await EmergencyAccess(pool).create_session(str(uuid4()), **_auth(pool.conn.row["requestor_id"]))
    assert session.session_token
    for args in pool.conn.writes:
        _no_naive(args)


async def test_create_session_rejects_lapsed_approval():
    row = _row(EmergencyStatus.APPROVED.value,
               expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    with pytest.raises(ValueError, match="expired"):
        await EmergencyAccess(FakePool(row)).create_session(str(uuid4()), **_auth(row["requestor_id"]))


async def test_request_and_deny_write_aware_timestamps():
    pool = FakePool(None)
    ea = EmergencyAccess(pool)
    requestor = uuid4()
    req = await ea.request(requestor, EmergencyScenario.SECURITY_INCIDENT, "j", ["x"], **_auth(requestor))
    assert req.expires_at.tzinfo is not None and req.created_at.tzinfo is not None
    await ea.deny(str(uuid4()), requestor, "no", **_auth(requestor))
    for args in pool.conn.writes:
        _no_naive(args)
