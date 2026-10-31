"""bughunt billing-money-credentials-2 / -3 - tenant_ai_credentials lifecycle.

Runs against a REAL Postgres because the defect was a UNIQUE NULLS NOT DISTINCT
(tenant_id, provider, credential_kind, status) violation that a fake cannot
model.  Skipped unless BH_PG_DSN points at a scratch database, e.g.

    BH_PG_DSN=postgresql://postgres@127.0.0.1:54377/postgres

The test creates and drops its own table in a throw-away schema.

The table is built as migration 0067 leaves it: that constraint is replaced by a
partial unique index on the ACTIVE row, so every disabled version is kept.  (The
interim fix deleted the previous disabled row on each rotation; 0067 made that
unnecessary.  Migration behaviour itself is in
test_bh2_cred_ai_credentials_history_pg.py.)
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import asyncpg
import pytest

from app.api.v1.endpoints import tenant_ai_credentials as ep
from app.domain.services.credential_resolver import CredentialResolver

DSN = os.getenv("BH_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="BH_PG_DSN not set (needs a scratch Postgres)")

SCHEMA = f"bh_cred_{uuid.uuid4().hex[:8]}"

DDL = f"""
CREATE SCHEMA {SCHEMA};
CREATE TABLE {SCHEMA}.tenant_ai_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    provider TEXT NOT NULL,
    credential_kind TEXT NOT NULL DEFAULT 'api_key',
    encrypted_key TEXT NOT NULL,
    last4 TEXT,
    label TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    rotated_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX uq_tenant_ai_credentials_active
    ON {SCHEMA}.tenant_ai_credentials (tenant_id, provider, credential_kind)
    WHERE status = 'active';
"""

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"


class _Enc:
    def encrypt(self, p):
        return f"ENC:{p}"


@pytest.fixture
async def pool(monkeypatch):
    p = await asyncpg.create_pool(
        DSN, min_size=1, max_size=3, server_settings={"search_path": f"{SCHEMA},public"}
    )
    async with p.acquire() as c:
        await c.execute(DDL)
    monkeypatch.setattr(ep, "get_encryption_service", lambda: _Enc())
    try:
        yield p
    finally:
        async with p.acquire() as c:
            await c.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
        await p.close()


def _user(tenant=TENANT):
    return SimpleNamespace(tenant_id=tenant)


async def _post(pool, key, provider="groq", tenant=TENANT):
    return await ep.create_credential(
        ep.CredentialCreateRequest(provider=provider, api_key=key),
        current_user=_user(tenant),
        db_pool=pool,
    )


async def _rows(pool, tenant=TENANT):
    async with pool.acquire() as c:
        return await c.fetch(
            "SELECT id, status, last4 FROM tenant_ai_credentials WHERE tenant_id = $1 "
            "ORDER BY created_at",
            tenant,
        )


@pytest.mark.asyncio
async def test_third_rotation_succeeds_and_only_newest_key_is_active(pool):
    await _post(pool, "key-aaaa-1111")
    await _post(pool, "key-bbbb-2222")
    third = await _post(pool, "key-cccc-3333")  # was UniqueViolationError -> 500
    rows = await _rows(pool)
    active = [r for r in rows if r["status"] == "active"]
    assert [r["last4"] for r in active] == ["3333"]
    assert str(active[0]["id"]) == third.id
    # Every rotated-out version is kept (0067); the interim fix deleted all but one.
    assert sum(1 for r in rows if r["status"] == "disabled") == 2


@pytest.mark.asyncio
async def test_post_delete_post_delete_leaves_no_active_key(pool):
    first = await _post(pool, "key-aaaa-1111")
    await ep.disable_credential(first.id, current_user=_user(), db_pool=pool)
    second = await _post(pool, "key-bbbb-2222")
    # Second revoke of the same provider/kind: was UniqueViolationError -> 500,
    # leaving the leaked key active.
    await ep.disable_credential(second.id, current_user=_user(), db_pool=pool)
    rows = await _rows(pool)
    # Both revoked keys stay on record (0067): nothing is deleted to make room.
    assert len(rows) == 2 and all(r["status"] == "disabled" for r in rows)


@pytest.mark.asyncio
async def test_disable_is_idempotent_and_tenant_scoped(pool):
    mine = await _post(pool, "key-aaaa-1111")
    theirs = await _post(pool, "key-zzzz-9999", tenant=OTHER_TENANT)
    # Another tenant cannot disable my key, even knowing its id.
    await ep.disable_credential(mine.id, current_user=_user(OTHER_TENANT), db_pool=pool)
    assert [r["status"] for r in await _rows(pool)] == ["active"]
    await ep.disable_credential(mine.id, current_user=_user(), db_pool=pool)
    await ep.disable_credential(mine.id, current_user=_user(), db_pool=pool)
    assert [r["status"] for r in await _rows(pool)] == ["disabled"]
    assert [r["status"] for r in await _rows(pool, OTHER_TENANT)] == ["active"]
    assert str((await _rows(pool, OTHER_TENANT))[0]["id"]) == theirs.id


@pytest.mark.asyncio
async def test_resolver_cache_invalidated_after_commit(pool, monkeypatch):
    """invalidate_cache must fire for the right (tenant, provider) and only
    AFTER the transaction block has exited (committed); invalidating earlier
    lets a concurrent resolve re-cache the old value."""
    in_txn = {"open": False}
    seen: list[tuple] = []
    real_acquire = ep.acquire_with_tenant

    class _Tracked:
        def __init__(self, *a, **k):
            self._cm = real_acquire(*a, **k)

        async def __aenter__(self):
            conn = await self._cm.__aenter__()
            in_txn["open"] = True
            return conn

        async def __aexit__(self, *exc):
            try:
                return await self._cm.__aexit__(*exc)
            finally:
                in_txn["open"] = False

    def spy(*, tenant_id=None, provider=None):
        assert in_txn["open"] is False, "invalidated before commit"
        seen.append((tenant_id, provider))

    monkeypatch.setattr(ep, "acquire_with_tenant", _Tracked)
    monkeypatch.setattr(CredentialResolver, "invalidate_cache", staticmethod(spy))

    created = await _post(pool, "key-aaaa-1111")
    assert seen == [(TENANT, "groq")]
    await ep.disable_credential(created.id, current_user=_user(), db_pool=pool)
    assert seen == [(TENANT, "groq"), (TENANT, "groq")]
