"""Actual 0061 DDL against both historical DNC shapes; no provider I/O."""
import importlib
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration
MIGRATION = importlib.import_module("Alembic.versions.0061_dnc_runtime_contract")
BACKEND = Path(__file__).resolve().parents[2]
LEGACY_SOURCES = "'manual','customer_request','internal_list','government_list','litigation','abuse_prevention'"


@pytest_asyncio.fixture
async def schema_db():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("Only disposable localhost *_test databases are supported")
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=15)
    schema = "dnc_migration_" + uuid4().hex
    try:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET search_path TO "{schema}"')
        await conn.execute("CREATE TABLE tenants(id uuid PRIMARY KEY); CREATE TABLE user_profiles(id uuid PRIMARY KEY)")
        yield SimpleNamespace(conn=conn, schema=schema, dsn=dsn)
    finally:
        await conn.execute("SET search_path TO public")
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


async def historical(db, shape):
    if shape == "legacy":
        extra = f"""phone_number varchar(50) NOT NULL, created_by uuid,
            CONSTRAINT dnc_entries_source_check CHECK (source IN ({LEGACY_SOURCES})),
            UNIQUE(tenant_id,normalized_number),"""
        source_type = "text"
    else:
        extra = "added_by uuid REFERENCES user_profiles(id) ON DELETE SET NULL, updated_at timestamptz NOT NULL DEFAULT now(),"
        source_type = "varchar(50)"
    await db.conn.execute(f"""CREATE TABLE dnc_entries(
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
        normalized_number varchar(50) NOT NULL,
        source {source_type} NOT NULL DEFAULT 'manual',
        reason text, expires_at timestamptz, created_at timestamptz DEFAULT now(),
        {extra}
        CONSTRAINT preserve_number_check CHECK (length(normalized_number)>1))""")
    await db.conn.execute("ALTER TABLE dnc_entries ENABLE ROW LEVEL SECURITY; ALTER TABLE dnc_entries FORCE ROW LEVEL SECURITY")
    await db.conn.execute("""CREATE POLICY tenant_rows ON dnc_entries USING (
        tenant_id=NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)""")


async def upgrade(db, monkeypatch, source="migration"):
    statements = []
    monkeypatch.setattr(MIGRATION, "op", SimpleNamespace(execute=statements.append))
    if source == "migration":
        MIGRATION.upgrade()
    else:
        script = (BACKEND / "database/complete_schema.sql").read_text(encoding="utf-8")
        statements = [script.split("-- BEGIN 0061 DNC RUNTIME CONTRACT:", 1)[1]
                      .split("\n", 1)[1].split("-- END 0061 DNC RUNTIME CONTRACT", 1)[0]]
    async with db.conn.transaction():
        for sql in statements:
            await db.conn.execute(sql.replace("public.", f'"{db.schema}".'))


async def add(db, tenant, source="caller_opt_out", number="+15555550106"):
    return await db.conn.fetchval("""INSERT INTO dnc_entries
        (tenant_id,phone_number,normalized_number,source) VALUES($1,$2,$2,$3) RETURNING id""",
        tenant, number, source)


@pytest.mark.parametrize("shape", ["legacy", "later"])
@pytest.mark.parametrize("source", ["migration", "bootstrap"])
async def test_known_shapes_preserve_evidence_and_enforce_source_specific_keys(schema_db, monkeypatch, shape, source):
    db = schema_db
    await historical(db, shape)
    tenant, other, actor = uuid4(), uuid4(), uuid4()
    await db.conn.execute("INSERT INTO tenants VALUES($1),($2)", tenant, other)
    await db.conn.execute("INSERT INTO user_profiles VALUES($1)", actor)
    if shape == "legacy":
        await db.conn.execute("""INSERT INTO dnc_entries(tenant_id,phone_number,normalized_number,source,created_by,reason)
            VALUES($1,'original display','+15555550106','customer_request',$2,'original reason')""", tenant, actor)
    else:
        await db.conn.execute("""INSERT INTO dnc_entries(tenant_id,normalized_number,source,added_by,reason)
            VALUES($1,'+15555550106','custom_source',$2,'original reason')""", tenant, actor)
    await db.conn.execute("UPDATE dnc_entries SET expires_at='2030-01-01T00:00:00Z'")
    before = dict(await db.conn.fetchrow("SELECT * FROM dnc_entries"))
    policy = await db.conn.fetch("SELECT policyname,qual,with_check FROM pg_policies WHERE schemaname=$1", db.schema)
    await upgrade(db, monkeypatch, source)
    after = dict(await db.conn.fetchrow("SELECT * FROM dnc_entries"))
    assert {k: after[k] for k in before} == before
    assert await db.conn.fetchval("SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid='dnc_entries'::regclass")
    assert await db.conn.fetch("SELECT policyname,qual,with_check FROM pg_policies WHERE schemaname=$1", db.schema) == policy
    assert await db.conn.fetchval("SELECT count(*) FROM pg_constraint WHERE conrelid='dnc_entries'::regclass AND conname='preserve_number_check'") == 1
    assert after["phone_number"] == ("original display" if shape == "legacy" else "+15555550106")
    await add(db, tenant)
    with pytest.raises(asyncpg.UniqueViolationError):
        await add(db, tenant)
    await add(db, other)
    await add(db, tenant, "another_custom_source")
    await add(db, None)
    with pytest.raises(asyncpg.UniqueViolationError):
        await add(db, None)
    await add(db, None, "another_custom_source")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.conn.execute("UPDATE dnc_entries SET added_by=$1", uuid4())
    indexes = await db.conn.fetch("SELECT indexrelid FROM pg_index WHERE indrelid='dnc_entries'::regclass ORDER BY indexrelid")
    await upgrade(db, monkeypatch, source)
    MIGRATION.downgrade()
    assert await db.conn.fetch("SELECT indexrelid FROM pg_index WHERE indrelid='dnc_entries'::regclass ORDER BY indexrelid") == indexes


@pytest.mark.parametrize("tenant_scoped", [False, True])
async def test_duplicate_source_records_abort_without_removing_or_rewriting_evidence(schema_db, monkeypatch, tenant_scoped):
    db = schema_db
    await historical(db, "later")
    tenant = uuid4() if tenant_scoped else None
    if tenant:
        await db.conn.execute("INSERT INTO tenants VALUES($1)", tenant)
    await db.conn.execute("""INSERT INTO dnc_entries(tenant_id,normalized_number,source,reason)
        VALUES($1,'+15555550106','caller_opt_out','first'),($1,'+15555550106','caller_opt_out','second')""", tenant)
    before = await db.conn.fetch("SELECT * FROM dnc_entries ORDER BY id")
    with pytest.raises(asyncpg.RaiseError, match="duplicate DNC source records"):
        await upgrade(db, monkeypatch)
    assert await db.conn.fetch("SELECT * FROM dnc_entries ORDER BY id") == before
    assert await db.conn.fetchval("SELECT to_regclass('uq_dnc_entries_tenant_number_source')") is None
    assert not await db.conn.fetchval("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=$1 AND table_name='dnc_entries' AND column_name='phone_number')", db.schema)


@pytest.mark.parametrize("definition", [
    "CREATE INDEX uq_dnc_entries_tenant_number_source ON dnc_entries(tenant_id,normalized_number,source)",
    "CREATE UNIQUE INDEX uq_dnc_entries_tenant_number_source ON dnc_entries(id)",
    "CREATE UNIQUE INDEX uq_dnc_entries_tenant_number_source ON dnc_entries(tenant_id,normalized_number,source) WHERE tenant_id IS NULL",
    "CREATE UNIQUE INDEX custom_source_blind_key ON dnc_entries(normalized_number)",
])
async def test_unrecognized_or_wrong_named_index_requires_review(schema_db, monkeypatch, definition):
    db = schema_db
    await historical(db, "later")
    await db.conn.execute(definition)
    before = await db.conn.fetch("SELECT indexdef FROM pg_indexes WHERE schemaname=$1 ORDER BY indexname", db.schema)
    with pytest.raises(asyncpg.RaiseError, match="0061: (named DNC index|unrecognized DNC unique)"):
        await upgrade(db, monkeypatch)
    assert await db.conn.fetch("SELECT indexdef FROM pg_indexes WHERE schemaname=$1 ORDER BY indexname", db.schema) == before


async def test_unrecognized_source_constraint_is_not_dropped(schema_db, monkeypatch):
    db = schema_db
    await historical(db, "later")
    await db.conn.execute("ALTER TABLE dnc_entries ADD CONSTRAINT custom_source_guard CHECK(source <> 'restricted')")
    with pytest.raises(asyncpg.RaiseError, match="unrecognized DNC source constraint"):
        await upgrade(db, monkeypatch)
    assert await db.conn.fetchval("SELECT count(*) FROM pg_constraint WHERE conrelid='dnc_entries'::regclass AND conname='custom_source_guard'") == 1


async def test_invalid_concurrent_index_is_not_accepted(schema_db, monkeypatch):
    db = schema_db
    await historical(db, "later")
    await db.conn.execute("INSERT INTO dnc_entries(normalized_number,source) VALUES('+15555550106','caller_opt_out'),('+15555550106','caller_opt_out')")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.conn.execute("CREATE UNIQUE INDEX CONCURRENTLY uq_dnc_entries_global_number_source ON dnc_entries(normalized_number,source) WHERE tenant_id IS NULL")
    with pytest.raises(asyncpg.RaiseError, match="named DNC index is invalid"):
        await upgrade(db, monkeypatch)
    assert await db.conn.fetchval("SELECT count(*) FROM dnc_entries") == 2


@pytest.mark.parametrize("column,kind", [("phone_number", "integer"), ("added_by", "text"), ("updated_at", "timestamp")])
async def test_incompatible_existing_column_requires_review(schema_db, monkeypatch, column, kind):
    db = schema_db
    await historical(db, "later")
    if column in {"added_by", "updated_at"}:
        await db.conn.execute(f'ALTER TABLE dnc_entries DROP COLUMN "{column}"')
    await db.conn.execute(f'ALTER TABLE dnc_entries ADD COLUMN "{column}" {kind}')
    with pytest.raises(asyncpg.RaiseError, match="incompatible DNC compatibility column type"):
        await upgrade(db, monkeypatch)


async def test_existing_nullable_update_time_is_preserved_as_unknown(schema_db, monkeypatch):
    db = schema_db
    await historical(db, "later")
    await db.conn.execute("ALTER TABLE dnc_entries ALTER COLUMN updated_at DROP NOT NULL")
    await db.conn.execute("INSERT INTO dnc_entries(normalized_number,source,updated_at) VALUES('+15555550106','manual',NULL)")
    await upgrade(db, monkeypatch)
    assert await db.conn.fetchval("SELECT updated_at FROM dnc_entries") is None


@pytest.mark.parametrize("tenant_scoped", [False, True])
async def test_concurrent_writers_keep_one_source_receipt(schema_db, monkeypatch, tenant_scoped):
    db = schema_db
    await historical(db, "later")
    await upgrade(db, monkeypatch)
    tenant = uuid4() if tenant_scoped else None
    if tenant:
        await db.conn.execute("INSERT INTO tenants VALUES($1)", tenant)
    async def write():
        conn = await asyncpg.connect(db.dsn, timeout=5, command_timeout=10)
        try:
            return await conn.fetchval(f'''INSERT INTO "{db.schema}".dnc_entries
                (tenant_id,phone_number,normalized_number,source)
                VALUES($1,'+15555550106','+15555550106','caller_opt_out')
                ON CONFLICT DO NOTHING RETURNING id''', tenant)
        finally:
            await conn.close()
    results = await asyncio.gather(write(), write())
    assert sum(result is not None for result in results) == 1
    assert await db.conn.fetchval("SELECT count(*) FROM dnc_entries") == 1
