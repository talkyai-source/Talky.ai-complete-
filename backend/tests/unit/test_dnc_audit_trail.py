"""QA F12-10: DNC changes leave an audit trail.

36 of 61 tenant or privileged write routes recorded no audit entry,
including DNC add/remove/import; deleting a permanent opt-out left no trace
in a real-database test. Single-entry DNC changes now write their audit row
in the same transaction as the change, taking the audit chain lock first.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services import dnc_service

TENANT = "11111111-1111-4111-8111-111111111111"
ENTRY = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
_ROW = {
    "id": ENTRY, "tenant_id": TENANT, "normalized_number": "+442079460123",
    "source": "caller_opt_out", "reason": "Caller opt-out", "expires_at": None,
    "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
}


def _fake_db(monkeypatch, *, row):
    order = []

    class _Conn:
        async def fetchrow(self, query, *args):
            order.append("sql:" + " ".join(query.split())[:24])
            return row

    @asynccontextmanager
    async def scoped(pool, tenant_id, **kwargs):
        yield _Conn()

    monkeypatch.setattr(dnc_service, "acquire_with_tenant", scoped)
    audit = SimpleNamespace(
        lock_transaction=AsyncMock(side_effect=lambda conn: order.append("lock")),
        log_in_transaction=AsyncMock(side_effect=lambda conn, **kw: order.append("audit")),
    )
    return order, audit


@pytest.mark.asyncio
async def test_removing_an_opt_out_leaves_an_audit_row_in_the_same_transaction(monkeypatch):
    order, audit = _fake_db(monkeypatch, row=_ROW)
    svc = dnc_service.DNCService(object())
    removed = await svc.remove(tenant_id=TENANT, entry_id=ENTRY, audit=audit,
                               actor_id=USER, actor_role="tenant_admin")
    assert removed is True
    assert order[0] == "lock" and order[-1] == "audit"
    kwargs = audit.log_in_transaction.await_args.kwargs
    assert kwargs["action"] == "dnc.entry.remove"
    assert kwargs["actor_id"] == USER and kwargs["tenant_id"] == TENANT
    assert kwargs["before_state"]["normalized_number"] == "+442079460123"
    assert kwargs["before_state"]["source"] == "caller_opt_out"


@pytest.mark.asyncio
async def test_removing_nothing_writes_no_audit_row(monkeypatch):
    _, audit = _fake_db(monkeypatch, row=None)
    svc = dnc_service.DNCService(object())
    assert await svc.remove(tenant_id=TENANT, entry_id=ENTRY, audit=audit, actor_id=USER) is False
    audit.log_in_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_adding_an_entry_leaves_an_audit_row(monkeypatch):
    order, audit = _fake_db(monkeypatch, row=_ROW)
    svc = dnc_service.DNCService(object())
    await svc.add(tenant_id=TENANT, e164="+442079460123", source="manual",
                  added_by=USER, audit=audit, actor_role="tenant_admin")
    assert order[0] == "lock" and order[-1] == "audit"
    kwargs = audit.log_in_transaction.await_args.kwargs
    assert kwargs["action"] == "dnc.entry.add"
    assert kwargs["after_state"]["normalized_number"] == "+442079460123"


@pytest.mark.asyncio
async def test_the_routes_pass_the_audit_logger_and_the_actor(monkeypatch):
    from app.api.v1.endpoints import dnc

    calls = {}

    class _Svc:
        def __init__(self, pool):
            pass

        async def remove(self, **kwargs):
            calls["remove"] = kwargs
            return True

        async def bulk_import(self, **kwargs):
            return {"accepted_count": 2, "skipped_count": 1, "invalid_count": 0,
                    "accepted": ["+442079460123", "+442079460124"], "invalid": []}

    monkeypatch.setattr(dnc, "DNCService", _Svc)
    user = SimpleNamespace(id=USER, tenant_id=TENANT, role="tenant_admin")
    audit = SimpleNamespace(log=AsyncMock())
    await dnc.remove_dnc_entry(ENTRY, current_user=user, db_pool=object(), audit=audit)
    assert calls["remove"]["audit"] is audit and calls["remove"]["actor_id"] == USER

    payload = dnc.DNCBulkImportRequest(numbers=["+442079460123", "+442079460124"], source="manual")
    await dnc.bulk_import(payload, current_user=user, db_pool=object(), audit=audit)
    kwargs = audit.log.await_args.kwargs
    assert kwargs["action"] == "dnc.bulk_import"
    assert kwargs["metadata"]["accepted_count"] == 2
