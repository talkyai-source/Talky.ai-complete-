"""Suspending a tenant succeeds and revokes its members' sessions (real Postgres).

Bug hunt 2026-10-10 (db-schema-core-2): suspend_tenant/suspend_partner with
immediate=True (the endpoint default) ran
``UPDATE security_sessions SET terminated_at=..., termination_reason=...
WHERE tenant_id=$1``. security_sessions has none of those columns (it has
revoked/revoked_at/revoke_reason and is keyed by user_id), so Postgres raised
UndefinedColumnError inside the suspension transaction and rolled back the
suspension event, tenants.status and tenant_users: an abusive or unpaid tenant
was never suspended. A mocked pool cannot see this, so the tables here are cut
verbatim out of database/schema/baseline_2026-06-02.sql (a pg_dump of
production).

Runs only with an explicit TEST_DATABASE_URL (an admin DSN on a throwaway
server). It creates and drops its own database.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path

import asyncpg
import pytest

from app.domain.services.suspension_service import SuspensionService, SuspensionType

_BASELINE = Path(__file__).resolve().parents[2] / "database" / "schema" / "baseline_2026-06-02.sql"
_TABLES = ("tenants", "tenant_users", "security_sessions", "suspension_events",
           "suspension_propagation_queue")


def _admin_dsn_or_skip() -> str:
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit TEST_DATABASE_URL is required")
    return dsn


async def _scenario(admin_dsn: str, suspend):
    dbname = f"bh_suspend_{uuid.uuid4().hex[:10]}"
    base = admin_dsn.rpartition("/")[0]
    admin = await asyncpg.connect(admin_dsn)
    await admin.execute(f'CREATE DATABASE "{dbname}"')
    await admin.close()
    pool = await asyncpg.create_pool(f"{base}/{dbname}", min_size=1, max_size=2)
    try:
        baseline = _BASELINE.read_text(encoding="utf-8")
        async with pool.acquire() as conn:
            await conn.execute("CREATE FUNCTION public.uuid_generate_v4() RETURNS uuid "
                               "LANGUAGE sql AS $$ SELECT gen_random_uuid() $$")
            for table in _TABLES:
                ddl = re.search(rf"CREATE TABLE public\.{table} \(.*?\n\);", baseline, re.S)
                assert ddl, f"{table} DDL not found in the baseline"
                await conn.execute(ddl.group(0))
            tenant, other = uuid.uuid4(), uuid.uuid4()
            member, outsider = uuid.uuid4(), uuid.uuid4()
            for tid in (tenant, other):
                await conn.execute("INSERT INTO tenants (id, business_name) VALUES ($1, 'Synthetic')", tid)
            await conn.execute(
                "INSERT INTO tenant_users (tenant_id, user_id, role_id, status) "
                "VALUES ($1, $2, $5, 'active'), ($3, $4, $5, 'active')",
                tenant, member, other, outsider, uuid.uuid4())
            for user in (member, outsider):
                await conn.execute(
                    "INSERT INTO security_sessions (user_id, session_token_hash, expires_at) "
                    "VALUES ($1, $2, NOW() + INTERVAL '1 day')", user, uuid.uuid4().hex)
        await suspend(SuspensionService(db_pool=pool), tenant)
        async with pool.acquire() as conn:
            status = await conn.fetchval("SELECT status FROM tenants WHERE id = $1", tenant)
            sessions = {r["user_id"]: r for r in await conn.fetch(
                "SELECT user_id, revoked, revoked_at, revoke_reason FROM security_sessions")}
        return status, sessions[member], sessions[outsider]
    finally:
        await pool.close()
        admin = await asyncpg.connect(admin_dsn)
        await admin.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        await admin.close()


def test_an_immediate_tenant_suspension_commits_and_revokes_only_its_sessions():
    async def suspend(service, tenant):
        await service.suspend_tenant(tenant, SuspensionType.ABUSE, "fraud",
                                     "Synthetic abuse suspension", immediate=True)

    status, member, outsider = asyncio.run(_scenario(_admin_dsn_or_skip(), suspend))
    assert status == "suspended"
    assert (member["revoked"], member["revoke_reason"]) == (True, "tenant_suspended")
    assert member["revoked_at"] is not None
    assert (outsider["revoked"], outsider["revoked_at"]) == (False, None)
