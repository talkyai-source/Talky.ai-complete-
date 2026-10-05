"""Actual CP03 migration and platform RLS on a disposable PostgreSQL database."""
from __future__ import annotations

import importlib.util
import os
import re
import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db import _register_jsonb_codecs
from app.core.db_utils import acquire_with_tenant

pytestmark = pytest.mark.integration
TABLES = ("processed_webhook_events", "billing_webhook_notifications", "billing_webhook_review_log")


@pytest_asyncio.fixture
async def webhook_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("CP03 integration accepts only a disposable localhost *_test database")
    admin = await asyncpg.create_pool(dsn, min_size=1, max_size=3, timeout=5,
                                      command_timeout=10, init=_register_jsonb_codecs)
    suffix = uuid4().hex
    fixture = SimpleNamespace(admin=admin, role="cp03_" + suffix, prefix="evt_cp03_" + suffix,
                              tenant=uuid4(), dsn=dsn, pool=None)
    try:
        async with acquire_with_tenant(admin, None) as conn:
            await conn.execute(f'CREATE ROLE "{fixture.role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{fixture.role}"')
            await conn.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON {",".join(TABLES)} TO "{fixture.role}"')
            await conn.execute(f'GRANT SELECT ON billing_ledger,topup_orders,subscriptions,tenants,invoices,billing_checkout_attempts TO "{fixture.role}"')
            await conn.execute(f'GRANT SELECT,INSERT ON invoice_snapshots,billing_refund_snapshots TO "{fixture.role}"')
            await conn.execute(f'GRANT USAGE,SELECT ON SEQUENCE invoice_snapshots_id_seq,billing_refund_snapshots_id_seq TO "{fixture.role}"')

        async def set_role(conn):
            await conn.execute(f'SET ROLE "{fixture.role}"')

        fixture.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=6, timeout=5,
                                                command_timeout=10, init=_register_jsonb_codecs,
                                                setup=set_role)
        yield fixture
    finally:
        if fixture.pool:
            await fixture.pool.close()
        async with acquire_with_tenant(admin, None) as conn:
            for table in reversed(TABLES):
                await conn.execute(f"DELETE FROM {table} WHERE event_id LIKE $1", fixture.prefix + "%")
            await conn.execute(f'DROP OWNED BY "{fixture.role}"')
            await conn.execute(f'DROP ROLE "{fixture.role}"')
        await admin.close()


@asynccontextmanager
async def ordinary_tenant(fixture):
    async with fixture.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.bypass_rls','off',true)")
            await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(fixture.tenant))
            yield conn


def migration_module():
    path = Path(__file__).resolve().parents[2] / "Alembic/versions/0054_billing_webhook_receipts.py"
    spec = importlib.util.spec_from_file_location("cp03_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("legacy_table", [True, False], ids=["retained-legacy", "fresh-install"])
async def test_real_migration_retains_legacy_claim_as_unverified(webhook_db, monkeypatch, legacy_table):
    fixture = webhook_db
    schema = "cp03_legacy_" + uuid4().hex
    module = migration_module()
    statements = []
    monkeypatch.setattr(module, "op", SimpleNamespace(execute=lambda sql: statements.append(str(sql))))
    module.upgrade()
    old_time = datetime(2020, 1, 2, 3, 4, tzinfo=UTC)
    async with acquire_with_tenant(fixture.admin, None) as conn:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        try:
            if legacy_table:
                await conn.execute(f'''CREATE TABLE "{schema}".processed_webhook_events (
                    event_id TEXT PRIMARY KEY,event_type TEXT,processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
                await conn.execute(f'INSERT INTO "{schema}".processed_webhook_events VALUES($1,$2,$3)',
                                   "evt_prior_claim", "invoice.paid", old_time)
            # Execute the actual migration's SQL, changing only its namespace.
            for sql in statements:
                await conn.execute(sql.replace("public.", f'"{schema}".'))
            if not legacy_table:
                assert await conn.fetchval(f'SELECT COUNT(*) FROM "{schema}".processed_webhook_events') == 0
                await conn.execute(f'''INSERT INTO "{schema}".processed_webhook_events
                    (event_id,event_type,processed_at) VALUES($1,$2,$3)''',
                                   "evt_prior_claim", "invoice.paid", old_time)
            row = await conn.fetchrow(f'SELECT * FROM "{schema}".processed_webhook_events')
            assert row["event_id"] == "evt_prior_claim" and row["processed_at"] == old_time
            assert row["state"] == "legacy_unverified" and row["completed_at"] is None
            assert row["event_payload"] is None and row["payload_hash"] is None
            assert row["attempt_count"] == 0
            assert row["legacy_claim"] is True
            await conn.execute(f'''INSERT INTO "{schema}".billing_webhook_notifications
                (delivery_key,event_id,event_type,kind,subject,body,status)
                VALUES('synthetic','evt_prior_claim','invoice.paid','receipt','Synthetic','Synthetic','superseded')''')
        finally:
            await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


async def test_fresh_migrated_tables_force_platform_rls(webhook_db):
    fixture = webhook_db
    async with acquire_with_tenant(fixture.pool, None) as conn:
        role = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        assert not role["rolsuper"] and not role["rolbypassrls"]
        for table in TABLES:
            flags = await conn.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=$1::regclass", table)
            assert flags["relrowsecurity"] and flags["relforcerowsecurity"]
        await conn.execute("INSERT INTO processed_webhook_events(event_id,event_type,tenant_id) VALUES($1,'invoice.paid',$2)", fixture.prefix, fixture.tenant)
        await conn.execute("""INSERT INTO billing_webhook_notifications
            (delivery_key,event_id,event_type,tenant_id,kind,subject,body)
            VALUES($1,$1,'invoice.paid',$2,'receipt','Synthetic receipt','Synthetic body')""", fixture.prefix, fixture.tenant)
        await conn.execute("""INSERT INTO billing_webhook_review_log(event_id,operator,decision,reason)
            VALUES($1,'synthetic operator','retain','Synthetic review')""", fixture.prefix)
    for table in TABLES:
        async with ordinary_tenant(fixture) as conn:
            assert await conn.fetchval(f"SELECT COUNT(*) FROM {table} WHERE event_id=$1", fixture.prefix) == 0
            assert await conn.execute(f"DELETE FROM {table} WHERE event_id=$1", fixture.prefix) == "DELETE 0"
    async with ordinary_tenant(fixture) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO processed_webhook_events(event_id) VALUES($1)", fixture.prefix + "_forged")


async def test_notification_identity_and_foreign_key_reject_duplicate_or_orphan(webhook_db):
    fixture = webhook_db
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("INSERT INTO processed_webhook_events(event_id,state) VALUES($1,'pending')", fixture.prefix)
        sql = """INSERT INTO billing_webhook_notifications(delivery_key,event_id,event_type,kind,subject,body)
            VALUES($1,$2,'invoice.paid','receipt','Synthetic','Synthetic')"""
        await conn.execute(sql, fixture.prefix, fixture.prefix)
        with pytest.raises(asyncpg.UniqueViolationError):
            async with conn.transaction():
                await conn.execute(sql, fixture.prefix, fixture.prefix)
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with conn.transaction():
                await conn.execute(sql, fixture.prefix + "_orphan", "evt_nonexistent")


@pytest.mark.parametrize("table,column,value", [
    ("processed_webhook_events", "state", "claimed_means_done"),
    ("processed_webhook_events", "provider_mode", "mock"),
    ("billing_webhook_notifications", "status", "delivered_without_receipt"),
])
async def test_database_rejects_ambiguous_new_states(webhook_db, table, column, value):
    fixture = webhook_db
    async with acquire_with_tenant(fixture.pool, None) as conn:
        await conn.execute("INSERT INTO processed_webhook_events(event_id) VALUES($1)", fixture.prefix)
        if table == "billing_webhook_notifications":
            await conn.execute("""INSERT INTO billing_webhook_notifications(delivery_key,event_id,event_type,kind,subject,body)
                VALUES($1,$1,'invoice.paid','receipt','Synthetic','Synthetic')""", fixture.prefix)
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(f"UPDATE {table} SET {column}=$2 WHERE event_id=$1", fixture.prefix, value)


async def test_actual_alembic_downgrade_refuses_to_destroy_receipts(webhook_db):
    fixture = webhook_db
    # Keep the original revision's guard covered even when a newer revision
    # refuses first during the actual CLI rollback.
    with pytest.raises(RuntimeError, match="identities must be retained"):
        migration_module().downgrade()
    async with acquire_with_tenant(fixture.admin, None) as conn:
        await conn.execute("INSERT INTO processed_webhook_events(event_id) VALUES($1)", fixture.prefix)
        await conn.execute("""INSERT INTO billing_webhook_notifications
            (delivery_key,event_id,event_type,kind,subject,body)
            VALUES($1,$1,'invoice.paid','receipt','Synthetic','Synthetic')""", fixture.prefix)
        await conn.execute("""INSERT INTO billing_webhook_review_log(event_id,operator,decision,reason)
            VALUES($1,'synthetic operator','retain','Synthetic review')""", fixture.prefix)
        before = await conn.fetchval("SELECT version_num FROM alembic_version")
        evidence_before = {
            table: dict(await conn.fetchrow(f"SELECT * FROM {table} WHERE event_id=$1", fixture.prefix))
            for table in TABLES
        }
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", "downgrade", "0053_billing_price_options",
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "DATABASE_URL": fixture.dsn, "ENVIRONMENT": "test"},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 30)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    # A newer retention guard may refuse first. Require an explicit downgrade
    # RuntimeError, rather than accepting an unrelated connection/SQL failure,
    # and prove the marker and every owned receipt remain exactly unchanged.
    output = output.decode("utf-8", errors="replace")
    assert process.returncode != 0
    assert re.search(r'File "[^\"]+[\\/]Alembic[\\/]versions[\\/][^\"]+\.py", line \d+, in downgrade', output)
    assert re.search(r"(?m)^RuntimeError: .+", output)
    async with acquire_with_tenant(fixture.admin, None) as conn:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == before
        for table in TABLES:
            row = await conn.fetchrow(f"SELECT * FROM {table} WHERE event_id=$1", fixture.prefix)
            assert dict(row) == evidence_before[table]
