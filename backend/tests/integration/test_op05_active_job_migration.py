"""Execute canonical 0060 DDL on disposable schemas; never choose a job survivor."""
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.domain.services.dialer.job_states import ACTIVE_STATUSES, TERMINAL_STATUSES

pytestmark = pytest.mark.integration
MIGRATION = importlib.import_module("Alembic.versions.0060_dialer_active_job_owner")
BACKEND = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def migration_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("OP05 accepts only disposable localhost *_test databases")
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
    schema = "op05_migration_" + uuid4().hex
    try:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET search_path TO "{schema}"')
        await conn.execute("""CREATE TABLE dialer_jobs(
            id uuid PRIMARY KEY,tenant_id uuid NOT NULL,lead_id uuid NOT NULL,
            status varchar(50),attempt_number integer NOT NULL DEFAULT 1)""")
        yield SimpleNamespace(conn=conn, schema=schema)
    finally:
        await conn.execute('SET search_path TO public')
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


async def upgrade(db, monkeypatch, source="migration"):
    statements = []
    monkeypatch.setattr(MIGRATION, "op", SimpleNamespace(execute=statements.append))
    if source == "migration":
        MIGRATION.upgrade()
    else:
        script = (BACKEND / "database/complete_schema.sql").read_text(encoding="utf-8")
        statements = [script.split("-- BEGIN 0060 ACTIVE JOB OWNER:", 1)[1]
                      .split("\n", 1)[1].split("-- END 0060 ACTIVE JOB OWNER", 1)[0]]
    async with db.conn.transaction():
        for sql in statements:
            await db.conn.execute(sql.replace("public.", f'"{db.schema}".'))


async def insert(db, lead, status, tenant=None):
    identity = uuid4()
    await db.conn.execute("INSERT INTO dialer_jobs(id,tenant_id,lead_id,status) VALUES($1,$2,$3,$4)",
                          identity, tenant or uuid4(), lead, status)
    return identity


@pytest.mark.parametrize("source", ["migration", "bootstrap"])
async def test_fresh_guard_rejects_every_active_status_but_retains_history(migration_db, monkeypatch, source):
    db = migration_db
    await upgrade(db, monkeypatch, source)
    # An ordinary distinct tenant/lead remains independent; tenant spoofing
    # cannot create another owner for the same globally identified lead.
    for active in ACTIVE_STATUSES:
        lead = uuid4()
        await insert(db, lead, active)
        for challenger in ACTIVE_STATUSES:
            with pytest.raises(asyncpg.UniqueViolationError):
                await insert(db, lead, challenger)
        for terminal in TERMINAL_STATUSES:
            await insert(db, lead, terminal)
        await insert(db, uuid4(), active)
    assert await db.conn.fetchval("SELECT count(*) FROM dialer_jobs") == len(ACTIVE_STATUSES) * (len(TERMINAL_STATUSES) + 2)
    index_before = await db.conn.fetchval("SELECT 'uq_dialer_jobs_one_active_per_lead'::regclass::oid")
    await upgrade(db, monkeypatch, source)
    MIGRATION.downgrade()
    assert await db.conn.fetchval("SELECT 'uq_dialer_jobs_one_active_per_lead'::regclass::oid") == index_before


async def test_upgrade_preserves_existing_jobs_and_duplicate_failure_changes_nothing(migration_db, monkeypatch):
    db = migration_db
    lead = uuid4()
    ids = [await insert(db, lead, status) for status in ("processing", "queued", "completed")]
    before = await db.conn.fetch("SELECT * FROM dialer_jobs ORDER BY id")
    with pytest.raises(asyncpg.RaiseError, match="reconcile their call and queue evidence"):
        await upgrade(db, monkeypatch)
    assert await db.conn.fetch("SELECT * FROM dialer_jobs ORDER BY id") == before
    assert await db.conn.fetchval("SELECT to_regclass('uq_dialer_jobs_one_active_per_lead')") is None
    # A fixture-only explicit resolution permits retry. Migration never does it.
    await db.conn.execute("UPDATE dialer_jobs SET status='cancelled' WHERE id=$1", ids[1])
    await upgrade(db, monkeypatch)
    assert await db.conn.fetchval("SELECT count(*) FROM dialer_jobs") == 3
    with pytest.raises(asyncpg.UniqueViolationError):
        await insert(db, lead, "pending")


@pytest.mark.parametrize("definition", [
    "CREATE INDEX uq_dialer_jobs_one_active_per_lead ON dialer_jobs(lead_id)",
    "CREATE UNIQUE INDEX uq_dialer_jobs_one_active_per_lead ON dialer_jobs(id)",
    "CREATE UNIQUE INDEX uq_dialer_jobs_one_active_per_lead ON dialer_jobs(lead_id) WHERE status='pending'",
])
async def test_existing_incompatible_named_index_is_not_silently_accepted(migration_db, monkeypatch, definition):
    db = migration_db
    await db.conn.execute(definition)
    before = await db.conn.fetchval("SELECT pg_get_indexdef('uq_dialer_jobs_one_active_per_lead'::regclass)")
    with pytest.raises(asyncpg.RaiseError, match="0060: named active-job index"):
        await upgrade(db, monkeypatch)
    assert await db.conn.fetchval("SELECT pg_get_indexdef('uq_dialer_jobs_one_active_per_lead'::regclass)") == before


@pytest.mark.parametrize("predicate", ["", " WHERE status IN ('pending','queued','retry_scheduled','processing','calling','failed')"])
async def test_stronger_existing_valid_index_is_preserved(migration_db, monkeypatch, predicate):
    db = migration_db
    await db.conn.execute("CREATE UNIQUE INDEX uq_dialer_jobs_one_active_per_lead ON dialer_jobs(lead_id)" + predicate)
    before = await db.conn.fetchval("SELECT pg_get_indexdef('uq_dialer_jobs_one_active_per_lead'::regclass)")
    await upgrade(db, monkeypatch)
    assert await db.conn.fetchval("SELECT pg_get_indexdef('uq_dialer_jobs_one_active_per_lead'::regclass)") == before


async def test_failed_concurrent_index_build_is_refused_without_repairing_or_deleting(migration_db, monkeypatch):
    db = migration_db
    lead = uuid4()
    await insert(db, lead, "pending")
    await insert(db, lead, "processing")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.conn.execute("""CREATE UNIQUE INDEX CONCURRENTLY uq_dialer_jobs_one_active_per_lead
            ON dialer_jobs(lead_id) WHERE status IN ('pending','queued','retry_scheduled','processing','calling')""")
    assert await db.conn.fetchval("SELECT indisvalid FROM pg_index WHERE indexrelid='uq_dialer_jobs_one_active_per_lead'::regclass") is False
    with pytest.raises(asyncpg.RaiseError, match="invalid or has incompatible keys"):
        await upgrade(db, monkeypatch)
    assert await db.conn.fetchval("SELECT count(*) FROM dialer_jobs") == 2
