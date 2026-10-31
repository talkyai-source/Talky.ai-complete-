"""bughunt billing-money-credentials-3 (proper fix) on a real Postgres.

Migration 0067 replaces UNIQUE NULLS NOT DISTINCT (tenant_id, provider,
credential_kind, status) on tenant_ai_credentials with a partial unique index on
the ACTIVE row, and the endpoint stops deleting the previous disabled row. A
fake cannot model either, so this runs the migration's own SQL and the real
endpoint against a throwaway database.

Skipped unless BH_PG_DSN points at a scratch Postgres SERVER. Any database on it
will do: the test connects there only to CREATE and DROP its own
bh2_cred_<hex> database.

    BH_PG_DSN=postgresql://postgres@127.0.0.1:53392/postgres
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from app.api.v1.endpoints import tenant_ai_credentials as ep

DSN = os.getenv("BH_PG_DSN", "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="BH_PG_DSN not set (needs a scratch Postgres)")

_VERSIONS = Path(__file__).resolve().parents[2] / "Alembic" / "versions"

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"

# tenant_ai_credentials as the database is at 0066: the table from
# database/schema/baseline_2026-06-02.sql (appended block, 2026-06-03) without
# the FK to tenants. Its unique key is the constraint 0067 removes.
TABLE_AT_0066 = """
CREATE TABLE public.tenant_ai_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    provider TEXT NOT NULL CHECK (char_length(provider) > 0 AND char_length(provider) <= 64),
    credential_kind TEXT NOT NULL DEFAULT 'api_key'
        CHECK (credential_kind IN ('api_key', 'oauth_refresh_token', 'service_account_json')),
    encrypted_key TEXT NOT NULL,
    last4 TEXT CHECK (last4 IS NULL OR char_length(last4) <= 8),
    label TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    rotated_at TIMESTAMPTZ,
    CONSTRAINT tenant_ai_credentials_unique_active
        UNIQUE NULLS NOT DISTINCT (tenant_id, provider, credential_kind, status)
);
CREATE INDEX idx_tenant_ai_credentials_lookup
    ON public.tenant_ai_credentials (tenant_id, provider, credential_kind)
    WHERE status = 'active';
"""
NO_TABLE = "SELECT 1"


def _migration():
    spec = importlib.util.spec_from_file_location(
        "m0067_ai_credentials_history", _VERSIONS / "0067_ai_credentials_history.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sibling_dsn(dbname: str) -> str:
    return urlunsplit(urlsplit(DSN)._replace(path=f"/{dbname}"))


_NO_CREATEDB = object()


def _run(build_sql: str, scenario):
    """Create a throwaway database, build it, run the scenario, always drop it."""

    async def main():
        name = f"bh2_cred_{uuid.uuid4().hex[:10]}"
        admin = await asyncpg.connect(DSN)
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
        except asyncpg.InsufficientPrivilegeError:
            return _NO_CREATEDB
        finally:
            await admin.close()
        pool = None
        try:
            pool = await asyncpg.create_pool(_sibling_dsn(name), min_size=1, max_size=3)
            async with pool.acquire() as conn:
                await conn.execute(build_sql)
            return await scenario(pool)
        finally:
            if pool is not None:
                await pool.close()
            admin = await asyncpg.connect(DSN)
            try:
                await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await admin.close()

    result = asyncio.run(main())
    if result is _NO_CREATEDB:
        pytest.skip("the BH_PG_DSN role cannot CREATE DATABASE")
    return result


async def _apply(conn, statements):
    # fetch() prepares the statement, as Alembic's asyncpg dialect does, so a
    # statement holding two commands fails here exactly as it would in a deploy.
    for statement in statements:
        await conn.fetch(statement)


class _Enc:
    def encrypt(self, plaintext):
        return f"ENC:{plaintext}"


def _user(tenant=TENANT):
    return SimpleNamespace(tenant_id=tenant)


async def _post(pool, key, provider="groq", tenant=TENANT):
    return await ep.create_credential(
        ep.CredentialCreateRequest(provider=provider, api_key=key),
        current_user=_user(tenant),
        db_pool=pool,
    )


async def _rows(pool, tenant=TENANT, provider="groq"):
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, status, last4, encrypted_key, rotated_at FROM tenant_ai_credentials "
            "WHERE tenant_id = $1 AND provider = $2 ORDER BY last4",
            tenant,
            provider,
        )


async def _catalog(conn):
    constraint = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'public.tenant_ai_credentials'::regclass "
        "AND conname = 'tenant_ai_credentials_unique_active'"
    )
    index = await conn.fetchval(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND indexname = 'uq_tenant_ai_credentials_active'"
    )
    return constraint, index


_FLIP = (
    "UPDATE tenant_ai_credentials SET status = 'disabled', rotated_at = NOW() "
    "WHERE tenant_id = $1 AND provider = 'groq' AND status = 'active'"
)
_ADD_ACTIVE = (
    "INSERT INTO tenant_ai_credentials (tenant_id, provider, encrypted_key, last4) "
    "VALUES ($1, 'groq', 'ENC:' || $2::text, $2::text)"
)


def test_second_disabled_row_is_rejected_before_0067_and_kept_after():
    migration = _migration()

    async def scenario(pool):
        out = {}
        async with pool.acquire() as conn:
            await conn.execute(_ADD_ACTIVE, TENANT, "1111")
            await conn.execute(_FLIP, TENANT)  # first rotation: fine
            await conn.execute(_ADD_ACTIVE, TENANT, "2222")
            with pytest.raises(asyncpg.UniqueViolationError) as raised:
                await conn.execute(_FLIP, TENANT)  # second rotation: the 500
            out["constraint_before"] = raised.value.constraint_name
            out["active_before"] = await conn.fetchval(
                "SELECT last4 FROM tenant_ai_credentials WHERE status = 'active'"
            )

            await conn.execute("DELETE FROM tenant_ai_credentials")
            await _apply(conn, migration.UPGRADE_STATEMENTS)

            await conn.execute(_ADD_ACTIVE, TENANT, "1111")
            await conn.execute(_FLIP, TENANT)
            await conn.execute(_ADD_ACTIVE, TENANT, "2222")
            await conn.execute(_FLIP, TENANT)  # now accepted
            await conn.execute(_ADD_ACTIVE, TENANT, "3333")
            out["after"] = {
                r["last4"]: r["status"]
                for r in await conn.fetch("SELECT last4, status FROM tenant_ai_credentials")
            }
        return out

    out = _run(TABLE_AT_0066, scenario)
    # The failing rotation left the key being rotated out active.
    assert out["constraint_before"] == "tenant_ai_credentials_unique_active"
    assert out["active_before"] == "2222"
    assert out["after"] == {"1111": "disabled", "2222": "disabled", "3333": "active"}


def test_upgrade_over_a_table_that_already_holds_rows_rewrites_none_of_them():
    """The production situation: 0066 data, not an empty table. A credential that
    was rotated once (one disabled + one active row), another tenant's active key
    and a provider that only has a disabled row. The index must build over exactly
    these rows, nothing is rewritten, and the next rotation then keeps all history."""
    migration = _migration()
    snapshot = (
        "SELECT id, tenant_id, provider, credential_kind, encrypted_key, last4, status, "
        "created_at, updated_at, rotated_at FROM tenant_ai_credentials ORDER BY last4"
    )

    async def scenario(pool):
        out = {}
        async with pool.acquire() as conn:
            await conn.execute(_ADD_ACTIVE, TENANT, "1111")
            await conn.execute(_FLIP, TENANT)  # TENANT/groq: 1111 disabled ...
            await conn.execute(_ADD_ACTIVE, TENANT, "2222")  # ... 2222 active
            await conn.execute(_ADD_ACTIVE, OTHER_TENANT, "9999")
            await conn.execute(
                "INSERT INTO tenant_ai_credentials (tenant_id, provider, encrypted_key, last4, status) "
                "VALUES ($1, 'deepgram', 'ENC:dddd', '4444', 'disabled')",
                TENANT,
            )
            before = [tuple(r) for r in await conn.fetch(snapshot)]

            await _apply(conn, migration.UPGRADE_STATEMENTS)

            out["rows_untouched"] = [tuple(r) for r in await conn.fetch(snapshot)] == before
            out["row_count"] = len(before)
            out["catalog"] = await _catalog(conn)

            await conn.execute(_FLIP, TENANT)  # the rotation that used to need a DELETE
            await conn.execute(_ADD_ACTIVE, TENANT, "3333")
            out["history"] = {
                (str(r["tenant_id"])[:2], r["provider"], r["last4"]): r["status"]
                for r in await conn.fetch("SELECT tenant_id, provider, last4, status FROM tenant_ai_credentials")
            }
        return out

    out = _run(TABLE_AT_0066, scenario)
    assert out["row_count"] == 4 and out["rows_untouched"] is True
    constraint, index = out["catalog"]
    assert constraint is None and index is not None
    assert out["history"] == {
        ("11", "groq", "1111"): "disabled",
        ("11", "groq", "2222"): "disabled",
        ("11", "groq", "3333"): "active",
        ("22", "groq", "9999"): "active",
        ("11", "deepgram", "4444"): "disabled",
    }


def test_rotations_through_the_endpoint_keep_every_version_and_one_active(monkeypatch):
    migration = _migration()
    monkeypatch.setattr(ep, "get_encryption_service", lambda: _Enc())

    async def scenario(pool):
        out = {}
        async with pool.acquire() as conn:
            await _apply(conn, migration.UPGRADE_STATEMENTS)

        created = [await _post(pool, f"key-aaaa-{n}{n}{n}{n}") for n in range(1, 5)]
        rows = await _rows(pool)
        out["history"] = {r["last4"]: r["status"] for r in rows}
        out["active_id"] = [str(r["id"]) for r in rows if r["status"] == "active"]
        out["newest_id"] = created[-1].id
        out["ciphertexts_kept"] = all(r["encrypted_key"] == f"ENC:key-aaaa-{r['last4']}" for r in rows)
        out["disabled_have_rotated_at"] = all(
            r["rotated_at"] is not None for r in rows if r["status"] == "disabled"
        )

        # Revoke the live key, create another, revoke again: history keeps growing.
        await ep.disable_credential(created[-1].id, current_user=_user(), db_pool=pool)
        out["after_revoke"] = [r["status"] for r in await _rows(pool)]
        fifth = await _post(pool, "key-aaaa-5555")
        await ep.disable_credential(fifth.id, current_user=_user(), db_pool=pool)
        out["after_second_revoke"] = [r["status"] for r in await _rows(pool)]

        # Other tenants and other providers are independent.
        await _post(pool, "key-zzzz-9999", tenant=OTHER_TENANT)
        await _post(pool, "key-gggg-7777", provider="deepgram")
        out["other_tenant"] = [r["status"] for r in await _rows(pool, OTHER_TENANT)]
        out["other_provider"] = [r["status"] for r in await _rows(pool, provider="deepgram")]

        # The database itself still refuses a second ACTIVE row for one credential.
        await _post(pool, "key-aaaa-6666")
        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.UniqueViolationError) as raised:
                await conn.execute(_ADD_ACTIVE, TENANT, "7777")
            out["duplicate_active_constraint"] = raised.value.constraint_name
        return out

    out = _run(TABLE_AT_0066, scenario)
    assert out["history"] == {"1111": "disabled", "2222": "disabled", "3333": "disabled", "4444": "active"}
    assert out["active_id"] == [out["newest_id"]]
    assert out["ciphertexts_kept"] and out["disabled_have_rotated_at"]
    assert out["after_revoke"] == ["disabled"] * 4
    assert out["after_second_revoke"] == ["disabled"] * 5
    assert out["other_tenant"] == ["active"] and out["other_provider"] == ["active"]
    assert out["duplicate_active_constraint"] == "uq_tenant_ai_credentials_active"


def test_upgrade_is_idempotent_and_a_noop_where_the_table_does_not_exist():
    migration = _migration()

    async def with_table(pool):
        async with pool.acquire() as conn:
            await _apply(conn, migration.UPGRADE_STATEMENTS)
            await _apply(conn, migration.UPGRADE_STATEMENTS)
            constraint, index = await _catalog(conn)
            lookup = await conn.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_tenant_ai_credentials_lookup'"
            )
            return constraint, index, lookup

    constraint, index, lookup = _run(TABLE_AT_0066, with_table)
    assert constraint is None
    assert index is not None and "(tenant_id, provider, credential_kind)" in index
    assert "WHERE (status = 'active'::text)" in index and index.startswith("CREATE UNIQUE INDEX")
    assert lookup == 1  # the existing resolver index is untouched

    async def without_table(pool):
        async with pool.acquire() as conn:
            await _apply(conn, migration.UPGRADE_STATEMENTS)
            await _apply(conn, migration.DOWNGRADE_STATEMENTS)
            return True

    assert _run(NO_TABLE, without_table) is True


def test_downgrade_refuses_while_history_exists_and_restores_the_constraint_when_clean(monkeypatch):
    migration = _migration()
    monkeypatch.setattr(ep, "get_encryption_service", lambda: _Enc())

    async def scenario(pool):
        out = {}
        async with pool.acquire() as conn:
            await _apply(conn, migration.UPGRADE_STATEMENTS)
        for n in range(1, 4):  # two disabled versions + one active
            await _post(pool, f"key-aaaa-{n}{n}{n}{n}")

        async with pool.acquire() as conn:
            with pytest.raises(asyncpg.PostgresError, match="Cannot downgrade 0067_ai_credentials_history") as raised:
                await _apply(conn, migration.DOWNGRADE_STATEMENTS)
            out["message"] = str(raised.value)
            out["rows_after_refusal"] = await conn.fetchval("SELECT count(*) FROM tenant_ai_credentials")
            out["catalog_after_refusal"] = await _catalog(conn)

            # Clearing the extra history on purpose lets the downgrade through.
            await conn.execute("DELETE FROM tenant_ai_credentials WHERE last4 = '1111'")
            await _apply(conn, migration.DOWNGRADE_STATEMENTS)
            out["catalog_after_downgrade"] = await _catalog(conn)

            # ... and the schema is the 0066 one again: a second disabled row dies.
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(_FLIP, TENANT)

            # Down and up again is a clean round trip.
            await _apply(conn, migration.UPGRADE_STATEMENTS)
            out["catalog_after_reupgrade"] = await _catalog(conn)
        return out

    out = _run(TABLE_AT_0066, scenario)
    assert "1 group(s)" in out["message"]
    assert out["rows_after_refusal"] == 3
    constraint, index = out["catalog_after_refusal"]
    assert constraint is None and index is not None  # refusal changed nothing

    constraint, index = out["catalog_after_downgrade"]
    assert constraint == "UNIQUE NULLS NOT DISTINCT (tenant_id, provider, credential_kind, status)"
    assert index is None

    constraint, index = out["catalog_after_reupgrade"]
    assert constraint is None and index is not None
