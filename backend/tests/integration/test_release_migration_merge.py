"""Actual canonical upgrades from both released heads, in fresh UUID databases.

Opt-in local owner access only. The existing database is used for provisioning,
never for schema/data writes. Each generated database is dropped without FORCE;
unrelated sessions and roles are not touched. This does not qualify production
data, application-role grants, or restore operations.
"""
import asyncio
from contextlib import asynccontextmanager
import os
import json
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
MERGE = "0063_release_history_merge"
ADMIN = "0077_legacy_tenant_rls_repair"
PAID = "0071_paid_only_billing"
PREVIOUS_HEAD = "0078_admin_billing_merge"
SUPPLIER = "0079_supplier_evidence"
ALLOWANCE = "0072_allowance_anchor"
SUPPLIER_ALLOWANCE = "0080_supplier_allowance_merge"
PAYMENT = "0073_payment_test_plan"
PAYMENT_MERGE = "0081_admin_payment_merge"
BOOTSTRAP = "0074_incidents_bootstrap"
UBUNTU = "0075_release_history_merge"
HEAD = "0082_ubuntu_admin_merge"
CURRENT_ADMIN_REVISIONS = {
    "0070_trunk_soft_delete", "0071_connector_lifecycle",
    "0072_admin_incidents", "0073_alertmanager_incidents", "0074_contact_enquiry_revision",
    "0075_admin_user_review", "0076_admin_user_operations", ADMIN, PAID,
    PREVIOUS_HEAD, SUPPLIER, ALLOWANCE, SUPPLIER_ALLOWANCE, PAYMENT, PAYMENT_MERGE,
    "0073_stream_events_bootstrap", BOOTSTRAP, UBUNTU, HEAD,
}
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
            # asyncpg close can finish just before the backend disappears from
            # pg_stat_activity. Wait briefly for that observation to settle;
            # never terminate a session or force a drop to hide a leak.
            for attempt in range(101):
                remaining = await conn.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=$1", name)
                if attempt == 0:
                    report["connections_on_cleanup_entry"] = remaining
                if remaining == 0 or attempt == 100:
                    break
                await asyncio.sleep(0.05)
            report["connections_before_drop"] = remaining
            assert remaining == 0, "Owned migration connections did not close within five seconds"
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
        # A current-head drill must prove the additive Admin schema too, not
        # merely accept a changed version marker after the historical merge.
        columns = await conn.fetch("""SELECT table_name,column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name=ANY($1::text[])""",
            ["tenants", "tenant_sip_trunks", "connector_accounts", "connectors", "incidents", "public_contact_enquiries", "user_profiles", "tenant_users"])
        present = {(row["table_name"],row["column_name"]) for row in columns}
        required = {
            ("tenants", "allowance_anchor_at"),
            ("tenant_sip_trunks","deleted_at"),
            ("connectors","provider_revocation_status"),
            ("public_contact_enquiries", "revision"),
            ("user_profiles", "admin_revision"),
            ("tenant_users", "admin_revision"),
            *(("connector_accounts",column) for column in (
                "credential_generation","refresh_claim_id","refresh_claimed_at","refresh_state","refresh_error")),
            *(("incidents",column) for column in (
                "acknowledged_at","acknowledged_by","resolved_at","resolved_by",
                "source_system","source_fingerprint","source_started_at","source_status",
                "source_recovered_at","source_last_received_at","source_labels")),
        }
        assert required <= present
        incidents = await conn.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='public.incidents'::regclass")
        assert incidents["relrowsecurity"] and incidents["relforcerowsecurity"]
        assert await conn.fetchval("SELECT count(*) FROM pg_policies WHERE schemaname='public' AND tablename='incidents' AND policyname='incidents_platform_only'") == 1
        assert await conn.fetchval("SELECT count(*) FROM pg_constraint WHERE conrelid='public.connector_accounts'::regclass AND conname='connector_refresh_claim_consistent'") == 1
        assert await conn.fetchval("SELECT count(*) FROM pg_constraint WHERE conrelid='public.incidents'::regclass AND conname IN ('incident_source_shape','incident_source_labels_object')") == 2
        episode_index = await conn.fetchrow("""SELECT i.indisunique,pg_get_expr(i.indpred,i.indrelid) AS predicate
            FROM pg_index i WHERE i.indexrelid='public.incidents_source_episode_unique'::regclass""")
        assert episode_index["indisunique"] and "source_system IS NOT NULL" in episode_index["predicate"]
        enquiry_revision = await conn.fetchrow("""SELECT is_nullable,column_default,data_type
            FROM information_schema.columns WHERE table_schema='public'
            AND table_name='public_contact_enquiries' AND column_name='revision'""")
        assert enquiry_revision["is_nullable"] == "NO" and enquiry_revision["column_default"] == "0"
        assert enquiry_revision["data_type"] == "integer"
        assert await conn.fetchval("""SELECT COUNT(*) FROM pg_constraint
            WHERE conrelid='public.public_contact_enquiries'::regclass
            AND contype='c' AND pg_get_constraintdef(oid) LIKE '%revision >= 0%'""") == 1
        for table, trigger in (
            ("user_profiles", "advance_user_profile_admin_revision"),
            ("tenant_users", "advance_tenant_user_admin_revision"),
        ):
            version = await conn.fetchrow("""SELECT is_nullable,column_default,data_type
                FROM information_schema.columns WHERE table_schema='public'
                AND table_name=$1 AND column_name='admin_revision'""", table)
            assert dict(version) == {"is_nullable": "NO", "column_default": "0", "data_type": "bigint"}
            assert await conn.fetchval("""SELECT COUNT(*) FROM pg_trigger
                WHERE tgrelid=$1::regclass AND tgname=$2 AND tgenabled='O'""", "public." + table, trigger) == 1
            assert await conn.fetchval("""SELECT COUNT(*) FROM pg_constraint
                WHERE conrelid=$1::regclass AND conname=$2 AND contype='c'""",
                "public." + table, table + "_admin_revision_nonnegative") == 1
        request_index = await conn.fetchrow("""SELECT i.indisunique,pg_get_expr(i.indpred,i.indrelid) AS predicate
            FROM pg_index i WHERE i.indexrelid='public.audit_user_create_request_unique'::regclass""")
        assert request_index["indisunique"] and "user_create_request_id" in request_index["predicate"]
        operation_index = await conn.fetchrow("""SELECT i.indisunique,pg_get_expr(i.indpred,i.indrelid) AS predicate
            FROM pg_index i WHERE i.indexrelid='public.audit_user_operation_terminal_unique'::regclass""")
        assert operation_index["indisunique"]
        assert all(value in operation_index["predicate"] for value in (
            "user_operation_version", "user_operation_id", "user_operation_cancelled",
            "user_created", "user_updated", "user_deleted", "user_deactivated",
            "user_primary_membership_configured"))
        assert await conn.fetchval("SELECT to_regclass('public.audit_user_operation_actor_prepared') IS NOT NULL")
        report["user_operation_terminal_uniqueness_and_discovery_indexes"] = True
        report["current_admin_contract"] = True
        for table in ("supplier_statement_evidence", "supplier_statement_reviews"):
            posture = await conn.fetchrow(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=$1::regclass", "public." + table)
            assert posture["relrowsecurity"] and posture["relforcerowsecurity"]
        report["supplier_tables_forced_rls"] = True


@pytest.mark.parametrize("start", [HEARTBEAT, RECEIPTS, BASELINE])
def test_actual_upgrade_retains_both_histories_and_audit_evidence(monkeypatch, start):
    admin_dsn = os.getenv("TALKY_RELEASE_MERGE_ADMIN_DSN", "")
    if not admin_dsn:
        pytest.skip("Explicit reviewed local migration owner fixture required")
    parsed = urlparse(admin_dsn)
    assert parsed.scheme == "postgresql" and parsed.hostname == "127.0.0.1" and parsed.port == 55434
    assert parsed.path in {"/cp04_acceptance_test", "/admin_premortem_20261021_test"} and not parsed.query and not parsed.fragment
    name = "release_merge_" + uuid4().hex + "_test"
    dsn = urlunparse(parsed._replace(path="/" + name))
    for key in ("DATABASE_URL", "READ_DATABASE_URL", "MIGRATION_DATABASE_URL"):
        monkeypatch.setenv(key, dsn)
    monkeypatch.chdir(BACKEND)
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "Alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [HEAD]
    assert set(scripts.get_revision(MERGE).down_revision) == {HEARTBEAT, RECEIPTS}
    assert CURRENT_ADMIN_REVISIONS <= {
        revision.revision for revision in scripts.iterate_revisions(HEAD, BASELINE, implicit_base=True)
    }
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


async def billing_state(dsn, tenant):
    async with connection(dsn) as conn:
        return {
            "heads": sorted(row["version_num"] for row in await conn.fetch("SELECT version_num FROM alembic_version")),
            # The additive anchor is absent before paid0072. Compare every old
            # field exactly, and assert the new/persisted anchor separately.
            "tenant": await conn.fetchval("SELECT (to_jsonb(t)-'allowance_anchor_at')::text FROM tenants t WHERE id=$1", tenant),
            "allowance_anchor_at": await conn.fetchval("SELECT to_jsonb(t)->>'allowance_anchor_at' FROM tenants t WHERE id=$1", tenant),
            "allowance_column": await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name='tenants' AND column_name='allowance_anchor_at')"""),
            "payment_plan": await conn.fetchval("SELECT to_jsonb(p)::text FROM plans p WHERE id='payment_test'"),
            "catalog": {table: [row["value"] for row in await conn.fetch(
                f'SELECT to_jsonb(t)::text AS value FROM public."{table}" t '
                + ("WHERE id <> 'payment_test' " if table == "plans" else "")
                + 'ORDER BY to_jsonb(t)::text')]
                for table in ("plans", "plan_price_options", "topup_packages")},
        }


@pytest.mark.parametrize("start_heads", [
    (PAID,), (ADMIN,), (ADMIN, PAID), (ALLOWANCE,), (SUPPLIER,), (SUPPLIER, ALLOWANCE),
    (PAYMENT,), (SUPPLIER_ALLOWANCE,), (SUPPLIER_ALLOWANCE, PAYMENT), (SUPPLIER, PAYMENT),
    (BOOTSTRAP,), (UBUNTU,), (PAYMENT_MERGE,), (UBUNTU, PAYMENT_MERGE),
])
def test_actual_admin_billing_merge_preserves_applied_terms_and_allowances(monkeypatch, start_heads):
    admin_dsn = os.getenv("TALKY_RELEASE_MERGE_ADMIN_DSN", "")
    if not admin_dsn:
        pytest.skip("Explicit reviewed local migration owner fixture required")
    parsed = urlparse(admin_dsn)
    assert parsed.scheme == "postgresql" and parsed.hostname == "127.0.0.1" and parsed.port == 55434
    assert parsed.path in {"/cp04_acceptance_test", "/admin_premortem_20261021_test"} and not parsed.query and not parsed.fragment
    name = "release_merge_" + uuid4().hex + "_test"
    dsn = urlunparse(parsed._replace(path="/" + name))
    for key in ("DATABASE_URL", "READ_DATABASE_URL", "MIGRATION_DATABASE_URL"):
        monkeypatch.setenv(key, dsn)
    monkeypatch.chdir(BACKEND)
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "Alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [HEAD]
    applied = {r.revision for start in start_heads for r in scripts.iterate_revisions(start, "base")}
    paid_applied = PAID in applied
    payment_applied = PAYMENT in applied
    allowance_applied = ALLOWANCE in applied
    report = {"start_heads": list(start_heads), "database": name, "steps": []}
    RUN_EVIDENCE.append(report)
    tenant = uuid4()

    async def seed():
        async with connection(dsn) as conn:
            await conn.execute("""INSERT INTO tenants(id,business_name,plan_id,minutes_allocated,minutes_used)
                VALUES($1,'Synthetic branch merge','basic',321,9)""", tenant)
            if paid_applied:
                # An already-released catalog can have subsequent reviewed terms.
                # Replaying paid0071 would erase this marker, even though its SQL
                # uses UPSERT and superficially appears idempotent.
                await conn.execute("UPDATE plans SET description='Synthetic post-release catalog marker' WHERE id='basic'")
            if payment_applied:
                await conn.execute("UPDATE plans SET description='Synthetic retained payment terms' WHERE id='payment_test'")
            if allowance_applied:
                await conn.execute("UPDATE tenants SET allowance_anchor_at='2026-10-25T12:34:56Z' WHERE id=$1", tenant)

    try:
        asyncio.run(provision(admin_dsn, name, report))
        asyncio.run(bootstrap(dsn))
        command.stamp(config, BASELINE)
        for start in start_heads:
            command.upgrade(config, start)
        asyncio.run(seed())
        before = asyncio.run(billing_state(dsn, tenant))
        assert before["heads"] == sorted(start_heads)
        command.upgrade(config, "head")
        after = asyncio.run(billing_state(dsn, tenant))
        assert after["heads"] == [HEAD]
        assert after["tenant"] == before["tenant"]
        assert after["allowance_column"] is True
        assert after["allowance_anchor_at"] == before["allowance_anchor_at"]
        if allowance_applied:
            assert before["allowance_column"] and before["allowance_anchor_at"] is not None
        else:
            assert before["allowance_anchor_at"] is None
        if payment_applied:
            assert after["payment_plan"] == before["payment_plan"]
            report["existing_payment_terms_unchanged"] = True
        else:
            assert before["payment_plan"] is None
            payment = json.loads(after["payment_plan"])
            assert payment["id"] == "payment_test" and payment["price"] == 1 and payment["minutes"] == 2
            report["missing_payment_plan_added"] = True
        if paid_applied:
            assert after["catalog"] == before["catalog"]
            report["existing_paid_catalog_unchanged"] = True
        else:
            assert after["catalog"] != before["catalog"]
            report["missing_paid_branch_applied"] = True
        command.upgrade(config, "head")
        assert asyncio.run(billing_state(dsn, tenant)) == after
        report["tenant_allowance_retained"] = True
        report["existing_allowance_anchor_retained"] = before["allowance_anchor_at"] is not None
        report["new_allowance_anchor_null"] = before["allowance_anchor_at"] is None
        report["repeat_upgrade_unchanged"] = True
    finally:
        asyncio.run(cleanup(admin_dsn, name, report))
    assert report["removed"] and report["connections_before_drop"] == 0
