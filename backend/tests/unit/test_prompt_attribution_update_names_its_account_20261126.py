"""The prompt-attribution UPDATE on ``calls`` names its account in the SQL.

House rule: never rely on row security alone. When the tenant is known the
row must belong to it; with no tenant (documented no-tenant path) the
statement matches by id only, as before.
"""
from __future__ import annotations

import contextlib
import types
import uuid

import pytest

from app.domain.services.voice_orchestrator import VoiceOrchestrator

TENANT = "22222222-2222-2222-2222-222222222222"


class _Conn:
    def __init__(self):
        self.calls = []

    async def execute(self, query, *args):
        self.calls.append((" ".join(query.split()), args))
        return "UPDATE 1"


def _patch(monkeypatch, conn, seen):
    @contextlib.asynccontextmanager
    async def _fake(pool, tenant):
        seen.append(tenant)
        yield conn

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", _fake)


def _config(tenant=None):
    return types.SimpleNamespace(
        prompt_hash="h1", prompt_template="t1", prompt_version="v1", tenant_id=tenant,
    )


async def _run(monkeypatch, tenant_arg, config_tenant=None):
    conn, seen = _Conn(), []
    _patch(monkeypatch, conn, seen)
    orch = VoiceOrchestrator(db_client=types.SimpleNamespace(pool=object()))
    await orch._persist_prompt_identity(str(uuid.uuid4()), _config(config_tenant), tenant_arg)
    assert len(conn.calls) == 1
    return conn.calls[0]


@pytest.mark.asyncio
async def test_update_names_the_account_when_tenant_known(monkeypatch):
    sql, args = await _run(monkeypatch, TENANT)
    assert "tenant_id = $5::uuid" in sql
    assert len(args) == 5
    assert args[4] == TENANT


@pytest.mark.asyncio
async def test_update_without_tenant_is_the_statement_as_before(monkeypatch):
    sql, args = await _run(monkeypatch, None)
    assert sql.endswith("WHERE id = $1::uuid")
    assert "tenant_id" not in sql
    assert len(args) == 4


@pytest.mark.asyncio
async def test_tenant_falls_back_to_config(monkeypatch):
    _, args = await _run(monkeypatch, None, config_tenant=TENANT)
    assert args[4] == TENANT
