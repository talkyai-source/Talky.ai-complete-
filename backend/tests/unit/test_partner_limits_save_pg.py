"""Saving partner limits works on the production schema (real Postgres).

Bug hunt 2026-10-10 (db-schema-core-8, partner limits): PUT
/partners/{id}/limits upserted with ON CONFLICT (partner_id), but
partner_limits has no unique constraint on partner_id, so Postgres raised
"there is no unique or exclusion constraint matching the ON CONFLICT
specification" on every save. The table here is cut verbatim from
database/schema/baseline_2026-06-02.sql (a pg_dump of production).

Runs only with an explicit TEST_DATABASE_URL (an admin DSN on a throwaway
server). It creates and drops its own database.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

from app.api.v1.endpoints.call_limits import PartnerLimitsSchema, update_partner_limits

_BASELINE = Path(__file__).resolve().parents[2] / "database" / "schema" / "baseline_2026-06-02.sql"


def _admin_dsn_or_skip() -> str:
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit TEST_DATABASE_URL is required")
    return dsn


async def _run(admin_dsn):
    dbname = f"bh_partner_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    await admin.close()
    pool = await asyncpg.create_pool(f"{admin_dsn.rpartition('/')[0]}/{dbname}", min_size=1, max_size=2)
    try:
        ddl = re.search(r"CREATE TABLE public\.partner_limits \(.*?\n\);",
                        _BASELINE.read_text(encoding="utf-8"), re.S).group(0)
        async with pool.acquire() as conn:
            await conn.execute("CREATE FUNCTION public.uuid_generate_v4() RETURNS uuid "
                               "LANGUAGE sql AS $$ SELECT gen_random_uuid() $$")
            await conn.execute(ddl)
        partner, client = uuid.uuid4(), SimpleNamespace(pool=pool)
        admin_user = SimpleNamespace(id=str(uuid.uuid4()), role="platform_admin")
        first = await update_partner_limits(partner, PartnerLimitsSchema(max_tenants=5),
                                            admin_user=admin_user, db_client=client, audit_logger=None)
        second = await update_partner_limits(partner, PartnerLimitsSchema(max_tenants=7),
                                             admin_user=admin_user, db_client=client, audit_logger=None)
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT max_tenants FROM partner_limits WHERE partner_id = $1", partner)
        return first, second, [r["max_tenants"] for r in rows]
    finally:
        await pool.close()
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()


def test_partner_limits_save_inserts_then_updates_one_row():
    first, second, stored = asyncio.run(_run(_admin_dsn_or_skip()))
    assert (first["max_tenants"], second["max_tenants"]) == (5, 7)
    assert stored == [7]
