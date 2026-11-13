"""QA F12-10 (rest): the platform-admin DNC aliases leave an audit trail too.

The tenant DNC routes were fixed in round 2; the admin aliases were not.
DELETE /admin/dnc/{entry_id} deleted the row directly with no before-state
or audit, so a number could be taken off Do-Not-Call and dialled with no
trace; POST /admin/dnc called DNCService.add without its audit argument.
Both now go through DNCService with the audit logger, writing the audit row
in the same transaction as the change, chain lock first.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.domain.services import dnc_service

TENANT = "11111111-1111-4111-8111-111111111111"
ENTRY = "22222222-2222-4222-8222-222222222222"
ADMIN = "33333333-3333-4333-8333-333333333333"
_ROW = {
    "id": ENTRY, "tenant_id": TENANT, "normalized_number": "+442079460123",
    "source": "caller_opt_out", "reason": "Caller opt-out", "expires_at": None,
    "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
}


def _fake_db(monkeypatch, *, row):
    seen = SimpleNamespace(order=[], sql=[], scopes=[])

    class _Conn:
        async def fetchrow(self, query, *args):
            seen.order.append("sql")
            seen.sql.append((" ".join(query.split()), args))
            return row

    @asynccontextmanager
    async def scoped(pool, tenant_id, **kwargs):
        seen.scopes.append(tenant_id)
        yield _Conn()

    monkeypatch.setattr(dnc_service, "acquire_with_tenant", scoped)
    audit = SimpleNamespace(
        lock_transaction=AsyncMock(side_effect=lambda conn: seen.order.append("lock")),
        log_in_transaction=AsyncMock(side_effect=lambda conn, **kw: seen.order.append("audit")),
    )
    return seen, audit


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [TENANT, None], ids=["tenant-entry", "platform-entry"])
async def test_a_platform_removal_is_audited_in_the_same_transaction(monkeypatch, owner):
    seen, audit = _fake_db(monkeypatch, row={**_ROW, "tenant_id": owner})
    removed = await dnc_service.DNCService(object()).remove_any(
        entry_id=ENTRY, audit=audit, actor_id=ADMIN, actor_role="platform_admin")
    assert removed is True
    assert seen.order == ["lock", "sql", "audit"]
    assert seen.scopes == [None], "the admin route resolves the entry before its tenant"
    query, args = seen.sql[0]
    assert query.startswith("DELETE FROM dnc_entries WHERE id = $1") and args == (ENTRY,)
    kwargs = audit.log_in_transaction.await_args.kwargs
    assert kwargs["action"] == "dnc.entry.remove" and kwargs["tenant_id"] == owner
    assert kwargs["actor_id"] == ADMIN and kwargs["actor_role"] == "platform_admin"
    assert kwargs["before_state"]["normalized_number"] == "+442079460123"


@pytest.mark.asyncio
async def test_a_platform_removal_of_nothing_writes_no_audit_row(monkeypatch):
    _, audit = _fake_db(monkeypatch, row=None)
    assert await dnc_service.DNCService(object()).remove_any(
        entry_id=ENTRY, audit=audit, actor_id=ADMIN, actor_role="platform_admin") is False
    audit.log_in_transaction.assert_not_awaited()


@pytest.fixture
def admin_service(monkeypatch):
    calls = {}

    class _Svc:
        def __init__(self, pool):
            pass

        async def remove_any(self, **kwargs):
            calls["remove_any"] = kwargs
            return calls.get("found", True)

        async def add(self, **kwargs):
            calls["add"] = kwargs
            return SimpleNamespace(id=ENTRY, normalized_number="+442079460123", tenant_id=None,
                                   source=kwargs["source"], reason=kwargs.get("reason"),
                                   expires_at=None, created_at=_ROW["created_at"])

    monkeypatch.setattr(dnc_service, "DNCService", _Svc)
    return calls


@pytest.mark.asyncio
async def test_the_admin_delete_route_goes_through_the_audited_removal(admin_service):
    from app.api.v1.endpoints import call_limits

    admin = SimpleNamespace(id=ADMIN, role="platform_admin", tenant_id=None)
    audit = SimpleNamespace()
    await call_limits.remove_dnc_entry(UUID(ENTRY), admin_user=admin,
                                       db_client=SimpleNamespace(pool=object()), audit_logger=audit)
    call = admin_service["remove_any"]
    assert call["audit"] is audit and call["actor_id"] == ADMIN and call["entry_id"] == ENTRY

    admin_service["found"] = False
    with pytest.raises(HTTPException) as missing:
        await call_limits.remove_dnc_entry(UUID(ENTRY), admin_user=admin,
                                           db_client=SimpleNamespace(pool=object()), audit_logger=audit)
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_the_admin_add_route_passes_its_audit_logger(admin_service):
    from app.api.v1.endpoints import call_limits

    admin = SimpleNamespace(id=ADMIN, role="platform_admin", tenant_id=None)
    audit = SimpleNamespace()
    entry = call_limits.DncEntrySchema(phone_number="+442079460123", source="manual", reason="complaint")
    await call_limits.add_dnc_entry(entry, tenant_id=None, admin_user=admin,
                                    db_client=SimpleNamespace(pool=object()), audit_logger=audit)
    call = admin_service["add"]
    assert call["audit"] is audit and call["added_by"] == ADMIN and call["actor_role"] == "platform_admin"
