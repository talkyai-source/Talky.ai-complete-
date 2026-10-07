"""Actual canonical upgrades from both released heads, in fresh UUID databases.

Opt-in local owner access only. The existing database is used for provisioning,
never for schema/data writes. Each generated database is dropped without FORCE;
unrelated sessions and roles are not touched. This does not qualify production
data, application-role grants, or restore operations.
"""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import re
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

import asyncpg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

BACKEND = Path(__file__).resolve().parents[2]
BASELINE = "0008_tenant_voice_tuning"
HEARTBEAT = "0048_audit_skip_heartbeat"
RECEIPTS = "0062_saved_acknowledgement"
HEAD = "0063_release_history_merge"
RUN_EVIDENCE = []
pytestmark = pytest.mark.integration


@asynccontextmanager
async def connection(dsn):
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=120)
    try:
        await conn.execute("SET app.bypass_rls='true'")
        yield conn
    finally:
        await conn.close()


async def provision(admin_dsn, name, report):
    assert re.fullmatch(r"release_merge_[0-9a-f]{32}_test", name)
    async with connection(admin_dsn) as conn:
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", name)
        await conn.execute(f'CREATE DATABASE "{name}"')
        report["created"] = True


async def cleanup(admin_dsn, name, report):
    assert re.fullmatch(r"release_merge_[0-9a-f]{32}_test", name)
    if report.get("created"):
        async with connection(admin_dsn) as conn:
            report["connections_before_drop"] = await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=$1", name)
            await conn.execute(f'DROP DATABASE "{name}"')
            report["removed"] = not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", name)


async def bootstrap(dsn):
    async with connection(dsn) as conn:
        assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_tables WHERE schemaname='public')")
        await conn.execute((BACKEND / "database/complete_schema.sql").read_text(encoding="utf-8"))


async def seed_start(dsn, start, tenant, trunk, report):
    async with connection(dsn) as conn:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == start
        function = await conn.fetchval("SELECT pg_get_functiondef('public.log_tenant_policy_mutation()'::regprocedure)")
        report["starting_heartbeat_skip"] = "<@ ARRAY[" in function
        assert report["starting_heartbeat_skip"] == (start == HEARTBEAT)
        assert bool(await conn.fetchval("SELECT to_regclass('public.crm_deliveries')")) == (start == RECEIPTS)
        await conn.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic migration tenant')", tenant)
        await conn.execute("""INSERT INTO tenant_sip_trunks(id,tenant_id,trunk_name,sip_domain)
            VALUES($1,$2,'Synthetic migration trunk','example.invalid')""", trunk, tenant)
        rows = await conn.fetch("SELECT to_jsonb(a)::text AS value FROM tenant_policy_audit_log a WHERE tenant_id=$1 ORDER BY id", tenant)
        report["original_audit"] = [r["value"] for r in rows]
        assert len(report["original_audit"]) == 1


async def verify_merged(dsn, tenant, trunk, report):
    async with connection(dsn) as conn:
        report["heads"] = [r["version_num"] for r in await conn.fetch("SELECT version_num FROM alembic_version")]
        assert report["heads"] == [HEAD]
        rows = await conn.fetch("SELECT to_jsonb(a)::text AS value FROM tenant_policy_audit_log a WHERE tenant_id=$1 ORDER BY id", tenant)
        assert [r["value"] for r in rows] == report["original_audit"]
        report["original_audit_retained"] = True
        # Both branch contracts exist. This uses the installed function and real
        # canonical trunk trigger, never a replacement function or fake table.
        for _ in range(2):
            await conn.execute("UPDATE tenant_sip_trunks SET live_status_checked_at=clock_timestamp(), updated_at=clock_timestamp() WHERE id=$1", trunk)
        await conn.execute("UPDATE tenant_sip_trunks SET is_active=is_active WHERE id=$1", trunk)
        assert await conn.fetchval("SELECT count(*) FROM tenant_policy_audit_log WHERE tenant_id=$1", tenant) == 1
        await conn.execute("UPDATE tenant_sip_trunks SET is_active=true,live_status_checked_at=clock_timestamp() WHERE id=$1", trunk)
        await conn.execute("DELETE FROM tenant_sip_trunks WHERE id=$1", trunk)
        actions = await conn.fetch("SELECT action,changed_fields FROM tenant_policy_audit_log WHERE tenant_id=$1 ORDER BY created_at,id", tenant)
        assert sorted(r["action"] for r in actions) == ["DELETE", "INSERT", "UPDATE"]
        update = next(r for r in actions if r["action"] == "UPDATE")
        assert {"is_active", "live_status_checked_at"} <= set(update["changed_fields"])
        report["audit_actions"] = [r["action"] for r in actions]
        assert await conn.fetchval("SELECT to_regclass('public.crm_deliveries') IS NOT NULL")
        assert await conn.fetchval("SELECT to_regclass('public.uq_dnc_entries_tenant_number_source') IS NOT NULL")
        recovery = await conn.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.assistant_action_resolutions'::regclass")
        assert recovery["relrowsecurity"] and recovery["relforcerowsecurity"]
        assert await conn.fetchval("SELECT count(*) FROM pg_trigger WHERE tgrelid='public.assistant_action_resolutions'::regclass AND tgname='action_resolutions_immutable'") == 1
        report["both_branch_contracts"] = True


@pytest.mark.parametrize("start", [HEARTBEAT, RECEIPTS, BASELINE])
def test_actual_upgrade_retains_both_histories_and_audit_evidence(monkeypatch, start):
    admin_dsn = os.getenv("TALKY_RELEASE_MERGE_ADMIN_DSN", "")
    if not admin_dsn:
        pytest.skip("Explicit reviewed local migration owner fixture required")
    parsed = urlparse(admin_dsn)
    assert parsed.scheme == "postgresql" and parsed.hostname == "127.0.0.1" and parsed.port == 55434
    assert parsed.path == "/cp04_acceptance_test" and not parsed.query and not parsed.fragment
    name = "release_merge_" + uuid4().hex + "_test"
    dsn = urlunparse(parsed._replace(path="/" + name))
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.chdir(BACKEND)
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "Alembic"))
    assert ScriptDirectory.from_config(config).get_heads() == [HEAD]
    report = {"start": start, "database": name, "steps": []}
    RUN_EVIDENCE.append(report)
    tenant, trunk = uuid4(), uuid4()
    try:
        asyncio.run(provision(admin_dsn, name, report))
        asyncio.run(bootstrap(dsn))
        command.stamp(config, BASELINE)
        report["steps"].append("canonical bootstrap + supported stamp0008")
        if start != BASELINE:
            command.upgrade(config, start)
            report["steps"].append("actual upgrade to " + start)
        asyncio.run(seed_start(dsn, start, tenant, trunk, report))
        command.upgrade(config, "head")
        report["steps"].append("actual upgrade head")
        # Repeating the deployed command must not rerun already applied DDL.
        command.upgrade(config, "head")
        report["steps"].append("repeat upgrade head")
        asyncio.run(verify_merged(dsn, tenant, trunk, report))
    finally:
        asyncio.run(cleanup(admin_dsn, name, report))
    assert report["removed"] and report["connections_before_drop"] == 0
