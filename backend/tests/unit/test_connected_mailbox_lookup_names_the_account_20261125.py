"""The connected-mailbox lookup must name the account on its database connection.

``EmailService.send_email`` is called from a live call's task and from the
reminder worker, neither of which sets a tenant. The connector resolver reads
``connectors`` through the old database client, which then applies the nil
tenant: row security hides the mailbox and the answer is "no mailbox
connected" although one is.

The fake connection below applies the production policy (bypass OR tenant_id =
the connection's tenant) to SELECT, so the test fails the way the database
does. The resolver does more after its first query; the fake stops it by
raising a sentinel on the first table other than ``connectors`` (the adapter
turns that into an error result), and the tests also assert the first query
returned one row through the resolver's own log line.
"""
import logging
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.core import postgres_adapter as pa
from app.core.security.tenant_isolation import (
    _bypass_rls,
    _tenant_context,
    get_bypass_rls,
    get_current_tenant_id,
)
from app.services import email_service as es
from app.services.email_service import EmailNotConnectedError, EmailService

T = "20000000-0000-4000-8000-0000000000a1"
OTHER = "20000000-0000-4000-8000-0000000000b2"
CONNECTOR_ID = "50000000-0000-4000-8000-000000000001"

COLUMNS = {
    "connectors": ["id", "tenant_id", "type", "provider", "status", "created_at", "config"],
    "connector_accounts": ["id", "tenant_id", "connector_id", "status", "created_at"],
}


class StopHere(Exception):
    """Raised by the fake for any table after the first connectors query."""


class Store:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []  # (table, tenant, bypass, rows_returned)
        self.stopped_on = None  # (table, tenant)


class FakeConn:
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

    async def fetch(self, sql, *args):
        if "information_schema.columns" in sql:
            return [{"column_name": c, "udt_name": "text"} for c in COLUMNS[args[0]]]
        assert sql.startswith("SELECT "), sql
        table = re.search(r" FROM (\w+)", sql).group(1)
        if table != "connectors":
            self.store.stopped_on = (table, self.tenant)
            raise StopHere(table)
        tail = sql.split(" WHERE ", 1)[1]
        tail = re.split(r" ORDER BY | LIMIT ", tail)[0]
        flt = {c: args[int(i) - 1] for c, i in re.findall(r"(\w+) = \$(\d+)", tail)}
        out = [
            dict(r) for r in self.store.rows["connectors"]
            if (self.bypass or self.tenant == str(r["tenant_id"]))
            and all(str(r.get(c)) == str(v) for c, v in flt.items())
        ]
        self.store.statements.append((table, self.tenant, self.bypass, len(out)))
        return out


@pytest.fixture
def world(monkeypatch):
    store = Store({"connectors": [{
        "id": CONNECTOR_ID, "tenant_id": T, "type": "email", "provider": "gmail",
        "status": "active", "created_at": "2026-11-01T00:00:00+00:00", "config": None,
    }]})

    async def fake_connect(*_a, **_k):
        return FakeConn(store)

    monkeypatch.setattr("app.core.postgres_adapter.asyncpg.connect", fake_connect)
    monkeypatch.setattr(pa, "_TABLE_COLUMN_TYPES_CACHE", {})
    monkeypatch.setattr(es, "get_encryption_service", lambda: SimpleNamespace())

    service = EmailService(object())  # a pool object without a .table attribute
    actions = []

    async def create_action(**kw):
        actions.append(("create", kw))
        return "60000000-0000-4000-8000-000000000001"

    async def update_action(**kw):
        actions.append(("update", kw))

    # Patched on the instance so no pool is needed: the action-record writes go
    # through acquire_with_tenant on a pool, which is outside what is tested.
    service._create_action_record = create_action
    service._update_action_status = update_action
    return SimpleNamespace(store=store, service=service, actions=actions)


def _rows_one(caplog):
    return any("active_connector_rows=1" in r.getMessage() for r in caplog.records)


def _clean():
    assert get_current_tenant_id() is None and get_bypass_rls() is False


async def test_review_connector_finds_the_mailbox_under_the_tenant(world, caplog):
    caplog.set_level(logging.INFO, logger="app.services.connector_resolver")
    with pytest.raises(Exception) as exc:
        await world.service.review_connector(T)
    assert not isinstance(exc.value, EmailNotConnectedError), "mailbox hidden: nil tenant"
    assert world.store.statements[0] == ("connectors", T, False, 1)
    assert world.store.stopped_on == ("connector_accounts", T)
    assert _rows_one(caplog)
    _clean()


async def test_send_email_finds_the_mailbox_under_the_tenant(world, caplog):
    caplog.set_level(logging.INFO, logger="app.services.connector_resolver")
    result = await world.service.send_email(
        tenant_id=T, to=["a@example.com"], subject="Hello", body="Body text")
    # The sentinel stopped the resolver after the first query; the send reports
    # a failed (not "not connected") outcome and records it.
    assert result["success"] is False
    assert world.store.statements[0] == ("connectors", T, False, 1)
    assert world.store.stopped_on == ("connector_accounts", T)
    assert _rows_one(caplog)
    assert [a[0] for a in world.actions] == ["create", "update"]
    assert world.actions[1][1]["status"] == "failed"
    _clean()


async def test_send_email_positional_tenant_is_scoped_too(world, caplog):
    caplog.set_level(logging.INFO, logger="app.services.connector_resolver")
    await world.service.send_email(T, ["a@example.com"], "Hello", "Body text")
    assert world.store.statements[0] == ("connectors", T, False, 1)
    assert _rows_one(caplog)
    _clean()


async def test_another_tenants_id_stays_hidden(world):
    with pytest.raises(EmailNotConnectedError):
        await world.service.review_connector(OTHER)
    assert world.store.statements == [("connectors", OTHER, False, 0)]
    with pytest.raises(EmailNotConnectedError):
        await world.service.send_email(
            tenant_id=OTHER, to=["a@example.com"], subject="Hello", body="Body text")
    assert world.store.stopped_on is None
    _clean()


async def test_a_tenant_that_is_not_a_uuid_is_not_scoped_and_sees_nothing(world):
    with pytest.raises(EmailNotConnectedError):
        await world.service.send_email(
            tenant_id="test-tenant", to=["a@example.com"], subject="Hello", body="Body text")
    # nothing was named: the nil account, which sees no rows
    assert world.store.statements == [("connectors", "00000000-0000-0000-0000-000000000000", False, 0)]
    _clean()


async def test_caller_context_is_restored_even_with_bypass(world):
    t1 = _tenant_context.set(OTHER)
    t2 = _bypass_rls.set(True)
    try:
        with pytest.raises(Exception):
            await world.service.review_connector(T)
        assert get_current_tenant_id() == OTHER and get_bypass_rls() is True
        await world.service.send_email(
            tenant_id=T, to=["a@example.com"], subject="Hello", body="Body text")
        assert get_current_tenant_id() == OTHER and get_bypass_rls() is True
    finally:
        _bypass_rls.reset(t2)
        _tenant_context.reset(t1)
    # inside the scope the lookup never ran on the caller's bypass
    assert all(s[2] is False for s in world.store.statements)
