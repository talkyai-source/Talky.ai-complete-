"""The WebSocket tenant bootstrap must use the user-scoped pooled path.

Root cause this pins (2026-09-08): both assistant sockets resolved the JWT
subject's tenant through ``db_client.table("user_profiles")`` BEFORE any tenant
context existed. That adapter installs the nil tenant, and once ``user_profiles``
went under forced RLS (migration 0038) the query returned no row, so every
connection closed with "User profile not found." on the same second it opened.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.api.v1 import ws_tenant
from app.api.v1.endpoints import assistant_voice_ws


class _Conn:
    def __init__(self, row):
        self.row = row
        self.queries: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        self.queries.append((" ".join(sql.split()), args))
        return self.row


def _fake_acquire(calls, conn):
    @asynccontextmanager
    async def acquire_with_tenant(pool, tenant_id, *, user_id=None, **kwargs):
        calls.append({"pool": pool, "tenant_id": tenant_id, "user_id": user_id})
        yield conn

    return acquire_with_tenant


@pytest.mark.asyncio
async def test_resolves_through_bypass_connection_scoped_to_the_subject(monkeypatch):
    calls: list[dict] = []
    conn = _Conn({"tenant_id": "1845a165-08aa-4554-bcec-2d31ac523662"})
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", _fake_acquire(calls, conn))
    pool = object()

    tenant = await ws_tenant.resolve_user_tenant(pool, "user-1")

    assert tenant == "1845a165-08aa-4554-bcec-2d31ac523662"
    # tenant_id=None is the documented bypass form (SET LOCAL app.bypass_rls),
    # and the subject predicate is explicit — never "whatever RLS lets through".
    assert calls == [{"pool": pool, "tenant_id": None, "user_id": "user-1"}]
    assert conn.queries == [
        ("SELECT tenant_id FROM user_profiles WHERE id = $1", ("user-1",))
    ]


@pytest.mark.asyncio
async def test_missing_profile_is_none_not_an_exception(monkeypatch):
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", _fake_acquire([], _Conn(None)))
    assert await ws_tenant.resolve_user_tenant(object(), "ghost") is None


@pytest.mark.asyncio
async def test_database_failure_propagates(monkeypatch):
    @asynccontextmanager
    async def _broken(pool, tenant_id, **kwargs):
        raise RuntimeError("pool exhausted")
        yield  # pragma: no cover

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", _broken)
    with pytest.raises(RuntimeError):
        await ws_tenant.resolve_user_tenant(object(), "user-1")


@pytest.mark.asyncio
async def test_voice_socket_uses_the_same_bootstrap(monkeypatch):
    class _TableMustNotBeUsed:
        pool = object()

        def table(self, *_a, **_k):
            raise AssertionError("voice bootstrap must not use the tenant-scoped adapter")

    calls: list[dict] = []
    conn = _Conn({"tenant_id": "tenant-9"})
    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", _fake_acquire(calls, conn))

    assert await assistant_voice_ws._resolve_tenant("user-9", _TableMustNotBeUsed()) == "tenant-9"
    assert calls[0]["tenant_id"] is None and calls[0]["user_id"] == "user-9"
