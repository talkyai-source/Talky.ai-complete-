"""auth-sessions-9: break-glass approve/create_session compared tz-aware
timestamptz values (asyncpg) with naive datetime.utcnow() and always raised."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

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
        return self.row

    async def execute(self, sql, *args):
        self.writes.append(args)


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
        "status": status, "approvers_required": 1, "approvals": [],
        # asyncpg returns timestamptz as tz-aware datetimes
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
        "session_created_at": None, "requested_access": ["platform:admin"],
    }
    row.update(over)
    return row


def _no_naive(args):
    for a in args:
        if isinstance(a, datetime):
            assert a.tzinfo is not None, f"naive datetime written to timestamptz: {a!r}"


async def test_approve_works_with_timezone_aware_expiry():
    pool = FakePool(_row(EmergencyStatus.PENDING.value))
    result = await EmergencyAccess(pool).approve(str(uuid4()), str(uuid4()))
    assert result["approved"] is True and result["fully_approved"] is True
    for args in pool.conn.writes:
        _no_naive(args)


async def test_approve_expires_a_lapsed_request_instead_of_crashing():
    row = _row(EmergencyStatus.PENDING.value,
               expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    pool = FakePool(row)
    with pytest.raises(ValueError, match="expired"):
        await EmergencyAccess(pool).approve(str(uuid4()), str(uuid4()))


async def test_create_session_works_with_timezone_aware_expiry():
    pool = FakePool(_row(EmergencyStatus.APPROVED.value))
    session = await EmergencyAccess(pool).create_session(str(uuid4()))
    assert session.session_token
    for args in pool.conn.writes:
        _no_naive(args)


async def test_create_session_rejects_lapsed_approval():
    row = _row(EmergencyStatus.APPROVED.value,
               expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    with pytest.raises(ValueError, match="expired"):
        await EmergencyAccess(FakePool(row)).create_session(str(uuid4()))


async def test_request_and_deny_write_aware_timestamps():
    pool = FakePool(None)
    ea = EmergencyAccess(pool)
    req = await ea.request(str(uuid4()), EmergencyScenario.SECURITY_INCIDENT, "j", ["x"])
    assert req.expires_at.tzinfo is not None and req.created_at.tzinfo is not None
    await ea.deny(str(uuid4()), str(uuid4()), "no")
    for args in pool.conn.writes:
        _no_naive(args)
