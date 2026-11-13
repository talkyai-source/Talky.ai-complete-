"""Opt-in PostgreSQL proof for F2-04; modifies only generated test databases."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


BACKEND = Path(__file__).resolve().parents[2]
REVISION = BACKEND / "Alembic/versions/0088_qa_data_constraints.py"
pytestmark = pytest.mark.integration
BASE = (
    "CREATE TABLE plans (id text PRIMARY KEY, minutes integer NOT NULL)",
    "CREATE TABLE tenants (id uuid PRIMARY KEY, plan_id text REFERENCES plans(id), minutes_allocated integer NOT NULL DEFAULT 0, minutes_used integer NOT NULL DEFAULT 0)",
    "CREATE TABLE tenant_call_limits (id uuid PRIMARY KEY, tenant_id uuid REFERENCES tenants(id), monthly_minutes_allocated integer NOT NULL DEFAULT 0, monthly_minutes_used integer NOT NULL DEFAULT 0)",
    "CREATE TABLE campaigns (id uuid PRIMARY KEY, tenant_id uuid REFERENCES tenants(id), status varchar(50) NOT NULL DEFAULT 'draft')",
    "CREATE TABLE leads (id uuid PRIMARY KEY, tenant_id uuid REFERENCES tenants(id), campaign_id uuid REFERENCES campaigns(id), status varchar(50) DEFAULT 'pending')",
    "CREATE TABLE billing_ledger (id bigint PRIMARY KEY, tenant_id uuid REFERENCES tenants(id), minutes_delta integer NOT NULL, amount_cents integer)",
)


def migration():
    assert REVISION.is_file(), "F2-04 migration missing"
    spec = importlib.util.spec_from_file_location("pg_qa_data_constraints", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def admin_dsn():
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("not run on PostgreSQL: explicit TEST_DATABASE_URL required")
    parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
    if parsed.scheme != "postgresql" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("F2-04 fixture requires loopback PostgreSQL", pytrace=False)
    if parsed.query or parsed.fragment or not re.search(r"(?:^|[_-])(?:test|ci|tmp)(?:[_-]|$)", parsed.path.lstrip("/"), re.I):
        pytest.fail("F2-04 fixture requires a test-named database without routing overrides", pytrace=False)
    return urlunsplit(parsed)


@pytest.fixture
async def database():
    dsn = admin_dsn()
    name = f"qa_constraints_{uuid4().hex}_test"
    assert re.fullmatch(r"qa_constraints_[a-f0-9]{32}_test", name)
    admin = await asyncpg.connect(dsn, timeout=5, command_timeout=60)
    engine = None
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        created = True
        target = urlunsplit(urlsplit(dsn)._replace(path=f"/{name}"))
        engine = create_async_engine("postgresql+asyncpg://", connect_args={"dsn": target, "timeout": 5, "command_timeout": 120}, poolclass=NullPool)
        async with engine.begin() as conn:
            for statement in BASE:
                await conn.execute(text(statement))
        yield engine, target
    finally:
        if engine is not None:
            await engine.dispose()
        try:
            if created:
                await admin.execute(f'DROP DATABASE "{name}"')
        finally:
            await admin.close()


async def change(engine, method):
    module = migration()
    async with engine.begin() as conn:
        def apply(sync):
            with Operations.context(MigrationContext.configure(sync)):
                getattr(module, method)()
        await conn.run_sync(apply)


async def scalar(engine, sql, params=None):
    async with engine.begin() as conn:
        return (await conn.execute(text(sql), params or {})).scalar()


async def test_not_valid_keeps_legacy_bad_rows_but_rejects_new_minutes_and_statuses(database):
    engine, target = database
    tenant, campaign, lead, limits = uuid4(), uuid4(), uuid4(), uuid4()
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO plans VALUES ('legacy',-1)"))
        await conn.execute(text("INSERT INTO tenants VALUES (:id,'legacy',-2,-3)"), {"id": tenant})
        await conn.execute(text("INSERT INTO tenant_call_limits VALUES (:id,:tenant,-4,-5)"), {"id": limits, "tenant": tenant})
        await conn.execute(text("INSERT INTO campaigns VALUES (:id,:tenant,'legacy_unknown')"), {"id": campaign, "tenant": tenant})
        await conn.execute(text("INSERT INTO leads VALUES (:id,:tenant,:campaign,'legacy_unknown')"), {"id": lead, "tenant": tenant, "campaign": campaign})
    await change(engine, "upgrade")
    assert await scalar(engine, "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'q31_%' AND contype='c' AND NOT convalidated") == 7
    assert await scalar(engine, "SELECT minutes FROM plans WHERE id='legacy'") == -1
    for sql in (
        "UPDATE plans SET minutes=minutes WHERE id='legacy'",
        "INSERT INTO plans VALUES ('bad',-1)",
        "INSERT INTO tenants(id,minutes_allocated) VALUES (gen_random_uuid(),-1)",
        "INSERT INTO tenants(id,minutes_used) VALUES (gen_random_uuid(),-1)",
        "INSERT INTO tenant_call_limits(id,monthly_minutes_allocated) VALUES (gen_random_uuid(),-1)",
        "INSERT INTO tenant_call_limits(id,monthly_minutes_used) VALUES (gen_random_uuid(),-1)",
        "INSERT INTO campaigns(id,status) VALUES (gen_random_uuid(),'unrecognized')",
        "INSERT INTO leads(id,status) VALUES (gen_random_uuid(),'unrecognized')",
    ):
        with pytest.raises(DBAPIError) as exc:
            async with engine.begin() as conn:
                await conn.execute(text(sql))
        assert getattr(exc.value.orig, "sqlstate", None) == "23514"
    # Counts describe old violations without repairing, validating or exposing them.
    script = BACKEND / "scripts/check_data_constraints.py"
    spec = importlib.util.spec_from_file_location("pg_qa_constraint_counts", script)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    conn = await asyncpg.connect(target, timeout=5, command_timeout=60)
    try:
        report = await checker.collect_report(conn, tenant_id=tenant)
    finally:
        await conn.close()
    assert len(report["constraints"]) == 7
    assert [row["violations"] for row in report["constraints"]] == [1] * 7
    assert report["missing_fk_indexes"] == []
    assert await scalar(engine, "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'q31_%' AND convalidated") == 0


async def test_zero_valid_lifecycle_states_and_negative_ledger_values_remain_valid(database):
    engine, _ = database
    await change(engine, "upgrade")
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO plans VALUES ('zero',0)"))
        await conn.execute(text("INSERT INTO tenants(id,minutes_allocated,minutes_used) VALUES (gen_random_uuid(),0,0)"))
        await conn.execute(text("INSERT INTO tenant_call_limits(id,monthly_minutes_allocated,monthly_minutes_used) VALUES (gen_random_uuid(),0,0)"))
        for state in ("draft", "scheduled", "running", "paused", "completed", "cancelled", "stopped", "deleted"):
            await conn.execute(text("INSERT INTO campaigns(id,status) VALUES (gen_random_uuid(),:state)"), {"state": state})
        for state in ("pending", "calling", "called", "contacted", "completed", "failed", "dnc", "deleted", "skipped", None):
            await conn.execute(text("INSERT INTO leads(id,status) VALUES (gen_random_uuid(),:state)"), {"state": state})
        await conn.execute(text("INSERT INTO billing_ledger(id,minutes_delta,amount_cents) VALUES (1,-250,-10000)"))
    assert await scalar(engine, "SELECT minutes_delta FROM billing_ledger WHERE id=1") == -250


async def test_fk_coverage_requires_real_leading_keys_and_preserves_existing_indexes(database):
    engine, _ = database
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE parent (id integer PRIMARY KEY)"))
        for kind in ("none", "prefix", "partial", "nonleading", "included", "expression"):
            await conn.execute(text(f"CREATE TABLE child_{kind} (id integer PRIMARY KEY, parent_id integer REFERENCES parent(id), other integer)"))
        for sql in (
            "CREATE INDEX existing_prefix ON child_prefix(parent_id,other)",
            "CREATE INDEX existing_partial ON child_partial(parent_id) WHERE other=1",
            "CREATE INDEX existing_nonleading ON child_nonleading(other,parent_id)",
            "CREATE INDEX existing_included ON child_included(id) INCLUDE(parent_id)",
            "CREATE INDEX existing_expression ON child_expression((parent_id+0))",
            "CREATE TABLE pair_parent (a integer,b integer,PRIMARY KEY(a,b))",
            "CREATE TABLE pair_child (a integer,b integer,other integer,FOREIGN KEY(a,b) REFERENCES pair_parent(a,b))",
            "CREATE INDEX existing_pair_reverse ON pair_child(b,a,other)",
        ):
            await conn.execute(text(sql))
    await change(engine, "upgrade")
    count = await scalar(engine, "SELECT count(*) FROM pg_class WHERE relkind='i' AND relname LIKE 'q31_fk_%'")
    # Five insufficient shapes receive indexes; a full prefix and equality-key permutation need no duplicate.
    for table, expected in [(f"child_{v}", 1) for v in ("none", "partial", "nonleading", "included", "expression")] + [("child_prefix", 0), ("pair_child", 0)]:
        assert await scalar(engine, "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE i.indrelid=to_regclass(:table) AND c.relname LIKE 'q31_fk_%'", {"table": table}) == expected
    await change(engine, "upgrade")
    assert await scalar(engine, "SELECT count(*) FROM pg_class WHERE relkind='i' AND relname LIKE 'q31_fk_%'") == count
    await change(engine, "downgrade")
    assert await scalar(engine, "SELECT count(*) FROM pg_class WHERE relkind='i' AND relname LIKE 'q31_fk_%'") == 0
    assert await scalar(engine, "SELECT count(*) FROM pg_class WHERE relkind='i' AND relname LIKE 'existing_%'") == 6
    assert await scalar(engine, "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'q31_%'") == 0
    await change(engine, "upgrade")
    assert await scalar(engine, "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'q31_%' AND NOT convalidated") == 7


async def test_downgrade_retains_adopted_compatible_constraint(database):
    engine, _ = database
    # Exact deterministic name/expression from the fixed registry, pre-existing and unowned.
    table, column, name, expression = migration().CHECKS[0]
    assert (table, column) == ("plans", "minutes")
    async with engine.begin() as conn:
        await conn.execute(text(f'ALTER TABLE public."{table}" ADD CONSTRAINT "{name}" CHECK ({expression})'))
        await conn.execute(text(f'COMMENT ON CONSTRAINT "{name}" ON public."{table}" IS \'owner preexisting constraint\''))
    await change(engine, "upgrade")
    await change(engine, "downgrade")
    assert await scalar(engine, "SELECT obj_description(oid,'pg_constraint') FROM pg_constraint WHERE conname=:name", {"name": name}) == "owner preexisting constraint"


async def test_existing_name_with_wrong_constraint_shape_aborts_without_replacement(database):
    engine, _ = database
    table, _, name, _ = migration().CHECKS[0]
    async with engine.begin() as conn:
        await conn.execute(text(f'ALTER TABLE public."{table}" ADD CONSTRAINT "{name}" CHECK (minutes >= -100)'))
    with pytest.raises(DBAPIError, match="(?i)(incompatible|conflict|mismatch)"):
        await change(engine, "upgrade")
    assert await scalar(engine, "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'q31_%'") == 1
    assert await scalar(engine, "SELECT count(*) FROM pg_class WHERE relkind='i' AND relname LIKE 'q31_fk_%'") == 0


async def test_existing_index_name_with_wrong_shape_is_not_silently_adopted(database):
    engine, _ = database
    await change(engine, "upgrade")
    name = await scalar(engine, "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE i.indrelid='tenants'::regclass AND c.relname LIKE 'q31_fk_%'")
    assert name and re.fullmatch(r"q31_fk_[a-z0-9_]+", name)
    await change(engine, "downgrade")
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE INDEX "{name}" ON tenants(minutes_used)'))
    with pytest.raises(DBAPIError, match="(?i)(incompatible|conflict|mismatch)"):
        await change(engine, "upgrade")
    assert await scalar(engine, "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'q31_%'") == 0
    assert "minutes_used" in await scalar(engine, "SELECT pg_get_indexdef(to_regclass(:name))", {"name": name})


async def test_missing_expected_column_refuses_instead_of_partially_claiming_repair(database):
    engine, _ = database
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE tenant_call_limits DROP COLUMN monthly_minutes_used"))
    with pytest.raises(DBAPIError):
        await change(engine, "upgrade")
    assert await scalar(engine, "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'q31_%'") == 0
