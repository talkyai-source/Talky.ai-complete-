"""Signed call web hooks must name the account when they reach the database.

The request middleware skips /api/v1/webhooks*, so no tenant is in context when
``dispatch_goal_achieved`` / ``dispatch_mark_spam`` call the call service. The
old database client then applies the nil tenant, row security hides the call,
and the sender gets 404 (a customer's do-not-call mark is dropped).

Unlike the older tests, this one runs a REAL CallService on a REAL old Client
against a fake connection that applies the row-security policy
(bypass OR tenant_id = current tenant) to SELECT and UPDATE.
"""
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import webhooks_secure as ws
from app.core import postgres_adapter as pa
from app.core.security.tenant_isolation import (
    _bypass_rls,
    _tenant_context,
    get_bypass_rls,
    get_current_tenant_id,
)
from app.domain.services.call_service import CallService

T = "20000000-0000-4000-8000-0000000000a1"
OTHER = "20000000-0000-4000-8000-0000000000b2"
CALL_ID = "30000000-0000-4000-8000-000000000001"
LEAD_ID = "40000000-0000-4000-8000-000000000001"
NIL = "00000000-0000-0000-0000-000000000000"

COLUMNS = {
    "calls": ["id", "tenant_id", "lead_id", "dialer_job_id", "goal_achieved", "outcome", "updated_at"],
    "leads": ["id", "tenant_id", "status", "do_not_call", "phone_number", "updated_at"],
}


class Store:
    def __init__(self, rows):
        self.rows = rows  # {table: [dict, ...]}
        self.statements = []  # (kind, table, tenant, bypass, rows_returned)


class FakeConn:
    """Applies the production policy: a row is seen only if bypass is true or
    the connection's tenant equals the row's tenant_id."""

    def __init__(self, store):
        self.store = store
        self.tenant = None
        self.bypass = False

    @asynccontextmanager
    async def transaction(self, **_kw):
        yield

    async def close(self):
        return None

    async def set_type_codec(self, *a, **k):
        return None

    async def execute(self, sql, *args):
        m = re.fullmatch(r"SET LOCAL app\.current_tenant_id = '([^']*)'", sql)
        if m:
            self.tenant = m.group(1)
            return "SET"
        m = re.fullmatch(r"SET LOCAL app\.bypass_rls = '([^']*)'", sql)
        if m:
            self.bypass = m.group(1) == "true"
            return "SET"
        raise AssertionError(f"unexpected execute: {sql}")

    def _visible(self, table):
        return [
            r for r in self.store.rows[table]
            if self.bypass or self.tenant == str(r["tenant_id"])
        ]

    @staticmethod
    def _where(sql, args, offset=0):
        tail = sql.split(" WHERE ", 1)[1] if " WHERE " in sql else ""
        tail = re.split(r" ORDER BY | LIMIT | RETURNING ", tail)[0]
        return {c: args[int(i) - 1] for c, i in re.findall(r"(\w+) = \$(\d+)", tail)}

    async def fetch(self, sql, *args):
        if "information_schema.columns" in sql:
            return [{"column_name": c, "udt_name": "text"} for c in COLUMNS[args[0]]]
        if sql.startswith("SELECT "):
            table = re.search(r" FROM (\w+)", sql).group(1)
            flt = self._where(sql, args)
            out = [dict(r) for r in self._visible(table)
                   if all(str(r.get(c)) == str(v) for c, v in flt.items())]
            self.store.statements.append(("select", table, self.tenant, self.bypass, len(out)))
            return out
        if sql.startswith("UPDATE "):
            table = re.match(r"UPDATE (\w+) SET (.*?) WHERE ", sql).group(1)
            set_sql = re.match(r"UPDATE \w+ SET (.*?) WHERE ", sql).group(1)
            sets = {c: args[int(i) - 1] for c, i in re.findall(r"(\w+) = \$(\d+)", set_sql)}
            flt = self._where(sql, args)
            out = []
            for r in self._visible(table):
                if all(str(r.get(c)) == str(v) for c, v in flt.items()):
                    r.update(sets)
                    out.append(dict(r))
            self.store.statements.append(("update", table, self.tenant, self.bypass, len(out)))
            return out
        raise AssertionError(f"unexpected fetch: {sql}")


@pytest.fixture
def world(monkeypatch):
    store = Store({
        "calls": [{"id": CALL_ID, "tenant_id": T, "lead_id": LEAD_ID, "dialer_job_id": None,
                   "goal_achieved": False, "outcome": None, "updated_at": None}],
        "leads": [{"id": LEAD_ID, "tenant_id": T, "status": "called", "do_not_call": False,
                   "phone_number": "+15551230000", "updated_at": None}],
    })

    async def fake_connect(*_a, **_k):
        return FakeConn(store)

    monkeypatch.setattr("app.core.postgres_adapter.asyncpg.connect", fake_connect)
    monkeypatch.setattr(pa, "_TABLE_COLUMN_TYPES_CACHE", {})

    service = CallService(pa.Client(object()))
    dnc = []

    async def record_dnc(tenant_id, phone_number, reason):
        # Patched on the instance: the real one writes through a pool, which is
        # outside this test (it is the call service's own pooled path).
        dnc.append((tenant_id, phone_number, reason))

    service._add_spam_mark_to_dnc_list = record_dnc
    monkeypatch.setattr("app.core.container.get_container",
                        lambda: SimpleNamespace(call_service=service))
    return SimpleNamespace(store=store, dnc=dnc)


def _tenants_seen(store, kind, table):
    return [s[2] for s in store.statements if s[0] == kind and s[1] == table]


async def test_goal_achieved_update_runs_under_the_verified_tenant(world):
    result = await ws.dispatch_goal_achieved(T, CALL_ID)
    assert result == {"message": "Goal marked as achieved", "call_id": CALL_ID}
    assert _tenants_seen(world.store, "update", "calls") == [T]
    assert world.store.rows["calls"][0]["goal_achieved"] is True
    assert get_current_tenant_id() is None and get_bypass_rls() is False


async def test_mark_spam_reaches_call_and_lead_under_the_verified_tenant(world):
    result = await ws.dispatch_mark_spam(T, CALL_ID, LEAD_ID, "spam")
    assert result["message"] == "Marked as spam"
    assert _tenants_seen(world.store, "select", "calls") == [T]
    assert _tenants_seen(world.store, "update", "calls") == [T]
    assert _tenants_seen(world.store, "update", "leads") == [T]
    assert world.store.rows["leads"][0]["do_not_call"] is True
    assert world.dnc == [(T, "+15551230000", "spam")]
    assert get_current_tenant_id() is None and get_bypass_rls() is False


async def test_goal_achieved_with_another_tenants_id_stays_404(world):
    with pytest.raises(HTTPException) as exc:
        await ws.dispatch_goal_achieved(OTHER, CALL_ID)
    assert exc.value.status_code == 404
    assert world.store.rows["calls"][0]["goal_achieved"] is False
    assert _tenants_seen(world.store, "update", "calls") == [OTHER]
    assert get_current_tenant_id() is None and get_bypass_rls() is False


async def test_mark_spam_with_another_tenants_id_stays_404(world):
    with pytest.raises(HTTPException) as exc:
        await ws.dispatch_mark_spam(OTHER, CALL_ID, LEAD_ID, "spam")
    assert exc.value.status_code == 404
    assert world.store.rows["leads"][0]["do_not_call"] is False
    assert world.dnc == []
    assert "update" not in [s[0] for s in world.store.statements]
    assert get_current_tenant_id() is None and get_bypass_rls() is False


async def test_a_caller_with_tenant_and_bypass_gets_them_back(world):
    t1 = _tenant_context.set(OTHER)
    t2 = _bypass_rls.set(True)
    try:
        await ws.dispatch_goal_achieved(T, CALL_ID)
        assert get_current_tenant_id() == OTHER and get_bypass_rls() is True
        await ws.dispatch_mark_spam(T, CALL_ID, LEAD_ID, "spam")
        assert get_current_tenant_id() == OTHER and get_bypass_rls() is True
        with pytest.raises(HTTPException):
            await ws.dispatch_goal_achieved(OTHER, "30000000-0000-4000-8000-0000000000ff")
        assert get_current_tenant_id() == OTHER and get_bypass_rls() is True
    finally:
        _bypass_rls.reset(t2)
        _tenant_context.reset(t1)
    # Inside the scope the fake saw the verified tenant, never the bypass.
    assert all(s[3] is False for s in world.store.statements)


async def test_a_tenant_that_is_not_a_uuid_is_not_scoped_and_answers_404(world):
    # Nothing is named: the nil account sees no rows, the answer is the 404 the
    # route always gave, never a 500, and the text never reaches a SET LOCAL.
    with pytest.raises(HTTPException) as exc:
        await ws.dispatch_goal_achieved("not-a-uuid", CALL_ID)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await ws.dispatch_mark_spam("' OR 1=1 --", CALL_ID, LEAD_ID, "spam")
    assert exc.value.status_code == 404
    assert {s[2] for s in world.store.statements} <= {NIL}
    assert get_current_tenant_id() is None and get_bypass_rls() is False
