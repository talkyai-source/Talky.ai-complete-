"""Migrations 0065/0066 and the CRM drain filter, on real Postgres.

Bug hunt 2026-10-10:
  * workers-jobs-ops-9 (0065): stream_events kept the 5-value category CHECK on
    Alembic-built databases, so every 'call' event was rejected and dropped.
  * campaigns-contacts-crm-1 (0066 + CRMDeliveryStore.due): Test Agent calls
    were queued for CRM delivery like real calls.

Runs only with an explicit TEST_DATABASE_URL (an admin DSN on a throwaway
server). It creates and drops its own database.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

from app.services.crm_delivery_store import CRMDeliveryStore

_VERSIONS = Path(__file__).resolve().parents[2] / "Alembic" / "versions"


def _migration(name):
    spec = importlib.util.spec_from_file_location(name, _VERSIONS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _admin_dsn_or_skip() -> str:
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit TEST_DATABASE_URL is required")
    return dsn


_TABLES = """
CREATE TABLE stream_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, category text NOT NULL,
    CONSTRAINT stream_events_category_check
        CHECK (category = ANY (ARRAY['campaign','system','alert','user_action','milestone'])));
CREATE TABLE calls (
    id uuid PRIMARY KEY, tenant_id uuid, status text, outcome text, duration_seconds int,
    transcript text, summary_json jsonb, recording_url text, ended_at timestamptz,
    crm_call_id text, is_test boolean DEFAULT false);
CREATE TABLE connectors (tenant_id uuid, type text, status text, provider text);
CREATE TABLE crm_deliveries (
    tenant_id uuid, call_id uuid, provider text, status text, phase text, last_error text,
    desired_key text, attempts int DEFAULT 0, next_attempt_at timestamptz DEFAULT NOW(),
    lease_expires_at timestamptz, updated_at timestamptz,
    UNIQUE (tenant_id, call_id, provider));
"""
_TRIGGER = """
CREATE TRIGGER calls_queue_crm_deliveries AFTER INSERT OR UPDATE OF
    status, outcome, duration_seconds, transcript, summary_json, recording_url, ended_at
ON calls FOR EACH ROW EXECUTE FUNCTION queue_call_crm_deliveries()
"""


async def _scenario(admin_dsn):
    m65, m66 = _migration("0065_stream_events_call_category"), _migration("0066_crm_skip_test_calls")
    dbname = f"bh_repairs_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    await admin.close()
    pool = await asyncpg.create_pool(f"{admin_dsn.rpartition('/')[0]}/{dbname}", min_size=1, max_size=2)
    out = {}
    try:
        async with pool.acquire() as conn:
            await conn.execute(_TABLES)
            tenant = uuid.uuid4()

            async def call_event_accepted():
                try:
                    await conn.execute("INSERT INTO stream_events (tenant_id, category) VALUES ($1, 'call')", tenant)
                    return True
                except asyncpg.CheckViolationError:
                    return False

            out["call_before"] = await call_event_accepted()
            for _ in range(2):  # idempotent
                for statement in m65.UPGRADE_STATEMENTS:
                    await conn.execute(statement)
            out["call_after"] = await call_event_accepted()

            await conn.execute(
                "INSERT INTO connectors VALUES ($1, 'crm', 'active', 'salesforce')", tenant)
            await conn.execute(m66.ORIGINAL_FUNCTION_SQL)
            await conn.execute(_TRIGGER)

            async def queued(is_test):
                call = uuid.uuid4()
                await conn.execute(
                    "INSERT INTO calls (id, tenant_id, status, ended_at, is_test) VALUES ($1, $2, 'completed', NOW(), $3)",
                    call, tenant, is_test)
                return call, await conn.fetchval("SELECT count(*) FROM crm_deliveries WHERE call_id = $1", call)

            out["test_call_before"] = (await queued(True))[1]
            await conn.execute(m66.FUNCTION_SQL)
            stale_test_call, out["test_call_after"] = await queued(True)
            real_call, out["real_call_after"] = await queued(False)
            # A row queued before the fix must not occupy the drain batch.
            await conn.execute(
                "INSERT INTO crm_deliveries (tenant_id, call_id, provider, status, phase) "
                "VALUES ($1, $2, 'salesforce', 'pending', 'pending')", tenant, stale_test_call)
        out["due"] = {row["call_id"] for row in await CRMDeliveryStore(pool).due(limit=10)}
        out["real_call"], out["stale_test_call"] = real_call, stale_test_call
        return out
    finally:
        await pool.close()
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()


def test_schema_repairs_and_drain_filter():
    out = asyncio.run(_scenario(_admin_dsn_or_skip()))
    assert (out["call_before"], out["call_after"]) == (False, True)
    assert (out["test_call_before"], out["test_call_after"], out["real_call_after"]) == (1, 0, 1)
    assert out["due"] == {out["real_call"]}


async def _no_tables_scenario(admin_dsn):
    """The CI and disaster-recovery bootstrap (database/complete_schema.sql) has no
    stream_events, tenant_ai_credentials or crm_deliveries: there 0065-0068 must be
    no-ops, not errors. Only production's hand-applied lineage has the tables."""
    names = ("0065_stream_events_call_category", "0066_crm_skip_test_calls",
             "0067_ai_credentials_history", "0068_tenant_secret_lookup")
    dbname = f"bh_repairs_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    await admin.close()
    conn = await asyncpg.connect(f"{admin_dsn.rpartition('/')[0]}/{dbname}")
    try:
        for name in names:
            module = _migration(name)
            for statement in getattr(module, "UPGRADE_STATEMENTS", None) or (module.FUNCTION_SQL,):
                await conn.execute(statement)
        return await conn.fetchval("SELECT to_regclass('public.stream_events') IS NULL")
    finally:
        await conn.close()
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()


def test_migrations_0065_to_0068_are_noops_where_the_tables_do_not_exist():
    assert asyncio.run(_no_tables_scenario(_admin_dsn_or_skip())) is True


def test_the_chain_keeps_one_head():
    assert _migration("0065_stream_events_call_category").down_revision == "0064_dnc_phone_number_default"
    assert _migration("0066_crm_skip_test_calls").down_revision == "0065_stream_events_call_category"
