"""bughunt billing-money-credentials-7 (proper fix) on a real Postgres.

Migration 0068 adds tenant_secrets.lookup_hash and a partial index over it;
SecretsManager.create()/rotate() fill it and validate_api_key() looks the
presented key up by its hash, falling back (and backfilling) only for rows that
predate the column. This runs the migration's own SQL and the real manager
against a throwaway database, counting DEK unwraps (the per-request CPU cost the
finding is about) and asking the planner whether it can use the index.

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
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from app.core.db import _register_jsonb_codecs
from app.domain.services import secrets_manager as sm
from app.domain.services.secrets_manager import SecretsManager, SecretType

DSN = os.getenv("BH_PG_DSN", "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="BH_PG_DSN not set (needs a scratch Postgres)")

_VERSIONS = Path(__file__).resolve().parents[2] / "Alembic" / "versions"
MASTER = b"k" * 32

# tenant_secrets / secret_access_log as the database is at 0067: the pg_dump
# tables of database/schema/baseline_2026-06-02.sql without the FKs to tenants and
# user_profiles. The baseline's UNIQUE (tenant_id, secret_name, is_active) is kept:
# it is why a TENANT-owned secret cannot be rotated today (reported as a separate
# finding), so the rotation test below uses a platform-owned key (tenant_id NULL).
TABLES_AT_0067 = """
CREATE TABLE public.tenant_secrets (
    secret_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    tenant_id uuid,
    created_by uuid,
    secret_type varchar(30) NOT NULL,
    secret_name varchar(100) NOT NULL,
    description text,
    encrypted_value bytea NOT NULL,
    encrypted_dek bytea NOT NULL,
    iv bytea NOT NULL,
    algorithm varchar(20) NOT NULL DEFAULT 'AES-256-GCM',
    permissions jsonb DEFAULT '{}'::jsonb,
    version integer NOT NULL DEFAULT 1,
    rotated_from uuid REFERENCES public.tenant_secrets(secret_id),
    rotated_to uuid REFERENCES public.tenant_secrets(secret_id),
    rotated_at timestamptz,
    expires_at timestamptz,
    last_accessed_at timestamptz,
    last_accessed_by uuid,
    access_count integer NOT NULL DEFAULT 0,
    is_active boolean NOT NULL DEFAULT true,
    is_compromised boolean NOT NULL DEFAULT false,
    revoked_at timestamptz,
    revoked_reason text,
    CONSTRAINT tenant_secrets_tenant_id_secret_name_is_active_key
        UNIQUE (tenant_id, secret_name, is_active)
);
CREATE INDEX idx_tenant_secrets_active ON public.tenant_secrets (tenant_id, is_active) WHERE is_active = true;
CREATE INDEX idx_tenant_secrets_type ON public.tenant_secrets (secret_type);
CREATE TABLE public.secret_access_log (
    access_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    accessed_at timestamptz NOT NULL DEFAULT now(),
    secret_id uuid NOT NULL REFERENCES public.tenant_secrets(secret_id),
    tenant_id uuid,
    accessed_by uuid,
    access_type varchar(30) NOT NULL,
    access_reason text,
    ip_address inet,
    user_agent text,
    success boolean NOT NULL,
    failure_reason text,
    api_key_prefix varchar(16),
    presented_permission varchar(50)
);
"""
NO_TABLE = "SELECT 1"


def _migration():
    spec = importlib.util.spec_from_file_location(
        "m0068_tenant_secret_lookup", _VERSIONS / "0068_tenant_secret_lookup.py"
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
            # Same jsonb codec the application pool registers (app.core.db).
            pool = await asyncpg.create_pool(
                _sibling_dsn(name), min_size=1, max_size=3, init=_register_jsonb_codecs
            )
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


def _manager(pool) -> SecretsManager:
    return SecretsManager(pool, master_key=MASTER, redis_client=None)


def _count_unwraps(mgr):
    calls: list[int] = []
    real = mgr._decrypt_dek

    def counting(blob):
        calls.append(1)
        return real(blob)

    mgr._decrypt_dek = counting
    return calls


async def _insert_key(conn, mgr, key, *, name, with_hash_column, lookup_hash=None, platform=False):
    """A TENANT_API_KEY row as create() wrote it before 0068 (no lookup_hash column
    yet), or, with with_hash_column, with the given lookup_hash (None = legacy).
    ``platform`` makes it platform-owned (tenant_id NULL)."""
    value = {"api_key_hash": mgr._hash_api_key(key), "prefix": key[:16]}
    dek = mgr._generate_dek()
    ciphertext, iv = mgr._encrypt_value(value, dek)
    secret_id = uuid.uuid4()
    columns = (
        "secret_id, tenant_id, secret_type, secret_name, encrypted_value, encrypted_dek, iv, "
        "permissions, expires_at"
    )
    values = "$1, $2, 'TENANT_API_KEY', $3, $4, $5, $6, $7, now() + interval '30 days'"
    args = [
        secret_id, None if platform else uuid.uuid4(), name, ciphertext, mgr._encrypt_dek(dek), iv,
        {"permissions": ["calls:read"]},
    ]
    if with_hash_column:
        columns += ", lookup_hash"
        values += ", $8"
        args.append(lookup_hash)
    await conn.execute(f"INSERT INTO public.tenant_secrets ({columns}) VALUES ({values})", *args)
    return secret_id


async def _lookup_hash(pool, secret_id):
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT lookup_hash FROM tenant_secrets WHERE secret_id = $1", secret_id)


def test_migration_adds_a_nullable_column_and_a_partial_index_and_round_trips():
    migration = _migration()

    async def scenario(pool):
        out = {}
        mgr = _manager(pool)
        async with pool.acquire() as conn:
            legacy_id = await _insert_key(
                conn, mgr, mgr._generate_api_key(), name="legacy", with_hash_column=False
            )
            await _apply(conn, migration.UPGRADE_STATEMENTS)
            await _apply(conn, migration.UPGRADE_STATEMENTS)  # idempotent
            out["column"] = tuple(
                await conn.fetchrow(
                    "SELECT data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'tenant_secrets' "
                    "AND column_name = 'lookup_hash'"
                )
            )
            out["index"] = await conn.fetchval(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
                "AND indexname = 'idx_tenant_secrets_lookup_hash'"
            )
            out["legacy_hash"] = await conn.fetchval(
                "SELECT lookup_hash FROM tenant_secrets WHERE secret_id = $1", legacy_id
            )

            await _apply(conn, migration.DOWNGRADE_STATEMENTS)
            out["after_downgrade"] = (
                await conn.fetchval(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'tenant_secrets' AND column_name = 'lookup_hash'"
                ),
                await conn.fetchval(
                    "SELECT count(*) FROM pg_indexes WHERE indexname = 'idx_tenant_secrets_lookup_hash'"
                ),
                await conn.fetchval("SELECT count(*) FROM tenant_secrets"),
            )
            await _apply(conn, migration.UPGRADE_STATEMENTS)  # and back up again
            out["after_reupgrade"] = await conn.fetchval(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'tenant_secrets' AND column_name = 'lookup_hash'"
            )
        return out

    out = _run(TABLES_AT_0067, scenario)
    assert out["column"] == ("text", "YES")
    index = out["index"]
    assert "USING btree (lookup_hash)" in index and index.startswith("CREATE INDEX")
    assert "TENANT_API_KEY" in index and "is_active" in index
    assert out["legacy_hash"] is None  # the migration decrypts nothing
    assert out["after_downgrade"] == (0, 0, 1)  # derived data only: no row lost
    assert out["after_reupgrade"] == 1


def test_migration_is_a_noop_where_the_table_does_not_exist():
    migration = _migration()

    async def scenario(pool):
        async with pool.acquire() as conn:
            await _apply(conn, migration.UPGRADE_STATEMENTS)
            await _apply(conn, migration.DOWNGRADE_STATEMENTS)
        return True

    assert _run(NO_TABLE, scenario) is True


def test_created_keys_validate_through_the_index_and_unwrap_exactly_one_row():
    migration = _migration()

    async def scenario(pool):
        out = {}
        async with pool.acquire() as conn:
            await _apply(conn, migration.UPGRADE_STATEMENTS)
        mgr = _manager(pool)
        created = []
        for n in range(8):
            tenant = uuid.uuid4()
            secret_id, key = await mgr.create(
                SecretType.TENANT_API_KEY, "tenant", tenant, {}, secret_name=f"key-{n}",
                permissions={"permissions": ["calls:read"]},
            )
            created.append((secret_id, key, tenant))
        unwraps = _count_unwraps(mgr)

        secret_id, key, tenant = created[5]
        found = await mgr.validate_api_key(key)
        out["found"] = (found["secret_id"], found["tenant_id"], found["permissions"]) == (
            secret_id, tenant, ["calls:read"]
        )
        out["found_unwraps"] = len(unwraps)
        unwraps.clear()

        out["unknown"] = await mgr.validate_api_key(mgr._generate_api_key())
        out["unknown_unwraps"] = len(unwraps)

        out["stored_hash"] = await _lookup_hash(pool, secret_id)
        out["expected_hash"] = mgr._hash_api_key(key)
        async with pool.acquire() as conn:
            out["audit_rows"] = await conn.fetchval(
                "SELECT count(*) FROM secret_access_log "
                "WHERE secret_id = $1 AND access_type = 'validate' AND success", secret_id
            )
            out["access_count"] = await conn.fetchval(
                "SELECT access_count FROM tenant_secrets WHERE secret_id = $1", secret_id
            )

        # The row filters survive the new lookup: revoked and expired keys are out.
        await mgr.revoke(created[0][0], revoked_by=uuid.uuid4(), reason="test", tenant_id=created[0][2])
        out["revoked"] = await mgr.validate_api_key(created[0][1])
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tenant_secrets SET expires_at = now() - interval '1 minute' WHERE secret_id = $1",
                created[1][0],
            )
        out["expired"] = await mgr.validate_api_key(created[1][1])
        out["denied"] = await mgr.validate_api_key(created[2][1], required_permission="calls:write")
        out["granted"] = await mgr.validate_api_key(created[2][1], required_permission="calls:read")
        return out

    out = _run(TABLES_AT_0067, scenario)
    assert out["found"] is True
    assert out["found_unwraps"] == 1  # was one per active key row ahead of the match
    assert out["unknown"] is None and out["unknown_unwraps"] == 0  # was one per active key row
    assert out["stored_hash"] == out["expected_hash"] and len(out["stored_hash"]) == 64
    assert out["audit_rows"] == 1 and out["access_count"] == 1
    assert out["revoked"] is None and out["expired"] is None
    assert out["denied"] is None and out["granted"] is not None


def test_legacy_keys_use_the_fallback_once_and_are_indexed_from_then_on():
    migration = _migration()

    async def scenario(pool):
        out = {}
        mgr = _manager(pool)
        legacy = []
        async with pool.acquire() as conn:
            for n in range(4):  # the last one is platform-owned (tenant_id NULL)
                key = mgr._generate_api_key()
                legacy.append(
                    (key, await _insert_key(
                        conn, mgr, key, name=f"legacy-{n}", with_hash_column=False, platform=(n == 3)
                    ))
                )
            await _apply(conn, migration.UPGRADE_STATEMENTS)
            # A new-style key alongside the legacy rows.
        secret_id, fresh_key = await mgr.create(
            SecretType.TENANT_API_KEY, "tenant", uuid.uuid4(), {}, secret_name="fresh",
            permissions={"permissions": ["calls:read"]},
        )
        unwraps = _count_unwraps(mgr)

        # A legacy key still validates (fallback) ...
        found = await mgr.validate_api_key(legacy[2][0])
        out["legacy_found"] = found is not None and found["secret_id"] == legacy[2][1]
        out["legacy_unwraps_first"] = len(unwraps)
        out["backfilled_match"] = await _lookup_hash(pool, legacy[2][1]) == mgr._hash_api_key(legacy[2][0])
        unwraps.clear()

        # ... and the second time it is an indexed hit.
        await mgr.validate_api_key(legacy[2][0])
        out["legacy_unwraps_second"] = len(unwraps)
        unwraps.clear()

        # An unknown key walks the remaining legacy rows once and backfills them all ...
        out["unknown"] = await mgr.validate_api_key(mgr._generate_api_key())
        out["probe_unwraps_first"] = len(unwraps)
        out["all_backfilled"] = [
            await _lookup_hash(pool, sid) == mgr._hash_api_key(key) for key, sid in legacy
        ]
        unwraps.clear()

        # ... after which probing costs no decryption at all.
        await mgr.validate_api_key(mgr._generate_api_key())
        out["probe_unwraps_second"] = len(unwraps)
        for key, sid in legacy:
            result = await mgr.validate_api_key(key)
            assert result["secret_id"] == sid
        out["fresh_found"] = (await mgr.validate_api_key(fresh_key))["secret_id"] == secret_id
        return out

    out = _run(TABLES_AT_0067, scenario)
    assert out["legacy_found"] is True
    assert 1 <= out["legacy_unwraps_first"] <= 4  # legacy rows only, never the indexed one
    assert out["backfilled_match"] is True
    assert out["legacy_unwraps_second"] == 1
    assert out["unknown"] is None and out["probe_unwraps_first"] <= 3
    assert out["all_backfilled"] == [True] * 4
    assert out["probe_unwraps_second"] == 0
    assert out["fresh_found"] is True


def test_rotation_issues_an_indexed_key_and_the_old_key_stays_valid_for_the_grace_period():
    migration = _migration()

    async def scenario(pool):
        out = {}
        async with pool.acquire() as conn:
            await _apply(conn, migration.UPGRADE_STATEMENTS)
        mgr = _manager(pool)
        # Platform-owned (tenant_id NULL): the baseline name-uniqueness constraint
        # does not apply to NULL tenants, so rotate() runs on the real schema.
        secret_id, old_key = await mgr.create(
            SecretType.TENANT_API_KEY, "system", uuid.uuid4(), {}, secret_name="platform-key",
            permissions={"permissions": ["*"]},
        )
        new_id, new_key = await mgr.rotate(secret_id, rotated_by=uuid.uuid4())
        out["new_hash_stored"] = await _lookup_hash(pool, new_id) == mgr._hash_api_key(new_key)
        out["old_hash_kept"] = await _lookup_hash(pool, secret_id) == mgr._hash_api_key(old_key)
        unwraps = _count_unwraps(mgr)
        out["new_valid"] = (await mgr.validate_api_key(new_key))["secret_id"] == new_id
        out["new_unwraps"] = len(unwraps)
        unwraps.clear()
        out["old_valid"] = (await mgr.validate_api_key(old_key))["secret_id"] == secret_id
        out["old_unwraps"] = len(unwraps)
        return out

    out = _run(TABLES_AT_0067, scenario)
    assert out == {
        "new_hash_stored": True,
        "old_hash_kept": True,
        "new_valid": True,
        "new_unwraps": 1,
        "old_valid": True,
        "old_unwraps": 1,
    }


def test_both_lookups_can_use_the_partial_index_in_a_custom_or_a_generic_plan():
    migration = _migration()

    async def scenario(pool):
        out = {}
        mgr = _manager(pool)
        async with pool.acquire() as conn:
            await _apply(conn, migration.UPGRADE_STATEMENTS)
            for n in range(150):
                key = mgr._generate_api_key()
                await _insert_key(
                    conn, mgr, key, name=f"k{n}", with_hash_column=True, lookup_hash=mgr._hash_api_key(key)
                )
            await conn.execute("ANALYZE public.tenant_secrets")

            async def plan(statement, *args):
                return "\n".join(r[0] for r in await conn.fetch(statement, *args))

            # Default planner settings: the hash lookup is an index scan on its own merits.
            out["by_hash"] = await plan("EXPLAIN " + sm._VALIDATE_BY_HASH_SQL, "f" * 64)
            async with conn.transaction():
                await conn.execute("SET LOCAL plan_cache_mode = force_generic_plan")
                await conn.execute("PREPARE by_hash(text) AS " + sm._VALIDATE_BY_HASH_SQL)
                out["by_hash_generic"] = await plan("EXPLAIN EXECUTE by_hash('f')")
            # The legacy pass finds its (few) rows through the same index, not a table scan.
            async with conn.transaction():
                await conn.execute("SET LOCAL enable_seqscan = off")
                out["legacy"] = await plan("EXPLAIN " + sm._VALIDATE_LEGACY_SQL)
        return out

    out = _run(TABLES_AT_0067, scenario)
    for name in ("by_hash", "by_hash_generic", "legacy"):
        assert "idx_tenant_secrets_lookup_hash" in out[name], (name, out[name])
        assert "Seq Scan" not in out[name], (name, out[name])
