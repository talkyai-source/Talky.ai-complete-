"""campaigns-contacts-crm-1: browser Test Agent calls must never reach a CRM."""
from __future__ import annotations

import asyncio
import contextlib

from tests.unit.test_crm_sync_service import (  # noqa: F401  (harness is a fixture)
    CALL, TENANT, FakeConnector, _call_row, harness,
)
from app.services import crm_sync_service as svc


def _run(coro):
    return asyncio.run(coro)


def test_a_test_call_is_not_synced_to_the_crm(harness):
    connector = FakeConnector("salesforce")
    harness["connectors"]["salesforce"] = connector
    harness["call"] = _call_row(is_test=True, phone_number="browser-test", lead_id=None)
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert out.skipped is True and out.skipped_reason == "test_call"
    assert connector.calls == [], "a test session must not create CRM records"
    assert harness["marked"] == []


def test_a_test_call_parks_its_trigger_queued_delivery_rows(harness):
    """The 0048 trigger can already have queued a row; it must leave the
    pending set or the drain worker retries it forever."""
    harness["connectors"]["salesforce"] = FakeConnector("salesforce")
    harness["call"] = _call_row(is_test=True, phone_number="browser-test", lead_id=None)
    _run(harness["service"].sync_call(TENANT, CALL))
    rows = harness["deliveries"].rows
    assert rows, "expected the delivery row to be parked"
    assert all(r["status"] == "skipped" for r in rows.values())


def test_a_real_call_is_still_synced(harness):
    connector = FakeConnector("salesforce")
    harness["connectors"]["salesforce"] = connector
    harness["call"] = _call_row(is_test=False)
    out = _run(harness["service"].sync_call(TENANT, CALL))
    assert not out.skipped
    assert any(c[0] == "log" for c in connector.calls)


def test_load_call_selects_is_test(monkeypatch):
    seen = {}

    class _Conn:
        async def fetchrow(self, sql, *args):
            seen["sql"] = sql
            return None

    @contextlib.asynccontextmanager
    async def _acq(pool, tenant_id):
        yield _Conn()

    monkeypatch.setattr(svc, "acquire_with_tenant", _acq)
    service = svc.CRMSyncService(db_client=object(), db_pool=object())
    _run(service._load_call(TENANT, CALL))
    assert "is_test" in seen["sql"]
