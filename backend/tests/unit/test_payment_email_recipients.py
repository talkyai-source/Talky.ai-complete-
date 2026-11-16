"""Payment received / failed notices go to every active tenant admin (max 5)."""
import re
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services import billing_invoice_projection as projection
from app.domain.services import billing_state_events as events

TENANT = "11111111-1111-1111-1111-111111111111"
INVOICE = "in_synthetic"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _user(n, *, role="tenant_admin", active=True):
    return {"id": f"00000000-0000-0000-0000-{n:012d}", "email": f"user{n}@example.invalid",
            "role": role, "is_active": active, "created_at": T0 + timedelta(days=n)}


class FakeConn:
    def __init__(self, users):
        self.users = users
        self.executed = []

    async def fetchrow(self, sql, *args):
        return None  # no prior invoice row

    async def fetchval(self, sql, *args):
        return 99

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "INSERT 0 1"

    async def fetch(self, sql, *args):
        assert "user_profiles" in sql and args[0] == TENANT
        rows = [u for u in self.users if u["role"] == "tenant_admin" and u["is_active"] is not False]
        if "ORDER BY" in sql:
            rows.sort(key=lambda u: (u["created_at"], u["id"]))
        else:  # unordered: the database may return any order
            rows.reverse()
        limit = int(re.search(r"LIMIT\s+(\d+)", sql).group(1))
        return rows[:limit]


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(events, "_lock", AsyncMock())
    monkeypatch.setattr(events, "_binding", AsyncMock(return_value={"tenant_id": TENANT, "attempt": None}))
    monkeypatch.setattr(events, "_apply_subscription", AsyncMock(return_value=events._result("handled", TENANT)))
    monkeypatch.setattr(projection, "capture_invoice", AsyncMock(return_value=({"detail_status": "ok"}, {"lines": {"has_more": False}})))
    monkeypatch.setattr(projection, "store_snapshot", AsyncMock())

    def configure(status, sub_status):
        invoice = {"id": INVOICE, "status": status, "amount_due": 100, "amount_paid": 100 if status == "paid" else 0,
                   "customer": "cus_1", "subscription": "sub_1", "metadata": {}}
        subscription = {"id": "sub_1", "customer": "cus_1", "status": sub_status}

        async def current(billing, resource, identity):
            return invoice if resource == "Invoice" else subscription
        monkeypatch.setattr(events, "_current", current)
    return configure


async def _notices(conn, kind):
    if kind == "paid":
        event = "invoice.paid"
    else:
        event = "invoice.payment_failed"
    result = await events._apply_invoice(conn, SimpleNamespace(billing_mode="test"), event, {"id": INVOICE, "subscription": "sub_1"})
    return result["notifications"]


@pytest.fixture(params=["paid", "failed"])
def kind(request, wired):
    wired("paid", "active") if request.param == "paid" else wired("open", "past_due")
    return request.param


def _base_key(kind):
    return f"invoice:{INVOICE}:{'paid' if kind == 'paid' else 'payment_failed'}"


@pytest.mark.asyncio
async def test_one_admin_gets_one_notice_with_todays_key(kind):
    notes = await _notices(FakeConn([_user(1)]), kind)
    assert [(n["recipient"], n["delivery_key"]) for n in notes] == [("user1@example.invalid", _base_key(kind))]


@pytest.mark.asyncio
async def test_two_admins_each_get_a_notice_oldest_first(kind):
    notes = await _notices(FakeConn([_user(2), _user(1)]), kind)
    assert [n["recipient"] for n in notes] == ["user1@example.invalid", "user2@example.invalid"]
    assert notes[0]["delivery_key"] == _base_key(kind)
    assert len({n["delivery_key"] for n in notes}) == 2
    assert all("@" not in n["delivery_key"] for n in notes)


@pytest.mark.asyncio
async def test_seven_admins_are_capped_at_five_oldest(kind):
    notes = await _notices(FakeConn([_user(n) for n in range(7, 0, -1)]), kind)
    assert [n["recipient"] for n in notes] == [f"user{n}@example.invalid" for n in range(1, 6)]
    assert len({n["delivery_key"] for n in notes}) == 5


@pytest.mark.asyncio
async def test_no_admins_records_one_notice_without_recipient(kind):
    notes = await _notices(FakeConn([]), kind)
    assert [(n["recipient"], n["delivery_key"]) for n in notes] == [(None, _base_key(kind))]


@pytest.mark.asyncio
async def test_inactive_admins_and_non_admins_are_never_addressed(kind):
    users = [_user(1, active=False), _user(2, role="agent"), _user(3, role="member"), _user(4)]
    notes = await _notices(FakeConn(users), kind)
    assert [(n["recipient"], n["delivery_key"]) for n in notes] == [("user4@example.invalid", _base_key(kind))]


@pytest.mark.asyncio
async def test_processing_the_same_event_twice_yields_the_same_keys(kind):
    users = [_user(n) for n in (3, 1, 2)]
    first = await _notices(FakeConn(users), kind)
    second = await _notices(FakeConn(users), kind)
    assert [n["delivery_key"] for n in first] == [n["delivery_key"] for n in second]
    assert [n["recipient"] for n in first] == [n["recipient"] for n in second]


@pytest.mark.asyncio
async def test_payment_supersedes_pending_failed_notices_of_every_admin(wired):
    wired("paid", "active")
    conn = FakeConn([_user(1), _user(2)])
    await _notices(conn, "paid")
    sql, args = next((s, a) for s, a in conn.executed if "superseded" in s)
    # The supersede statement must be able to match the extra admins' keys too.
    assert args[0] == f"invoice:{INVOICE}:payment_failed"
    assert "LIKE" in sql.upper() or "left(" in sql.lower() or "starts_with" in sql.lower()
