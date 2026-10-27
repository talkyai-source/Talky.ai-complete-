"""Tenant operator writes and audit receipts against disposable PostgreSQL.

Restricted NOBYPASSRLS role, synthetic owned UUID rows, no external calls. The
test DB must be explicitly designated local *_test. Do not run concurrently
with other audit-chain fixtures: each fixture removes only its own chain rows.
"""
import asyncio
import json
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import HTTPException, Response
from unittest.mock import AsyncMock

from app.api.v1.endpoints.admin import tenants
from app.core.db_utils import acquire_with_tenant
from app.core.security.platform_admin_guard import lock_user_administration
from app.domain.services.audit_logger import AuditEvent, AuditLogger

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def tenant_db(monkeypatch):
    dsn = os.getenv("TEST_ADMIN_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit migrated disposable TEST_ADMIN_DATABASE_URL required")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path.endswith("_test")
    owner = await asyncpg.connect(dsn)
    role, actor, target = "ad04t_" + uuid4().hex, uuid4(), uuid4()
    prefix = "AD04T " + uuid4().hex
    plan_id = "ad04t_" + uuid4().hex
    pool, faults, audit = None, [], None
    try:
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE ON tenants,tenant_quotas,audit_logs TO "{role}"')
        await owner.execute(f'GRANT SELECT ON user_profiles,tenant_users,roles,plans,campaigns TO "{role}"')
        await owner.execute("INSERT INTO plans(id,name,price,minutes) VALUES($1,'Synthetic AD04',0,100)", plan_id)
        await owner.execute(
            "INSERT INTO tenants(id,business_name,subscription_status,calling_rules) "
            "VALUES($1,$2,'active',$3::jsonb)", target, prefix,
            json.dumps({"max_concurrent_calls": 10, "timezone": "Asia/Karachi", "preserve_me": True}),
        )
        await owner.execute(
            "INSERT INTO user_profiles(id,email,role,is_active,is_verified,email_verified_at,tenant_id) "
            "VALUES($1,$2,'platform_admin',TRUE,TRUE,NOW(),$3)", actor, f"{actor}@example.invalid", target,
        )
        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute("SET statement_timeout='10s'")
            await conn.set_type_codec("jsonb", schema="pg_catalog", encoder=json.dumps, decoder=json.loads)
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, setup=setup)
        audit = AuditLogger(pool, signing_key="synthetic-test-only", flush_interval_seconds=3600)
        async def fail_audit():
            name = "ad04t_fault_" + uuid4().hex
            await owner.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.actor_id='{actor}'::uuid THEN RAISE EXCEPTION 'synthetic audit fault'; END IF; RETURN NEW; END $$""")
            await owner.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON audit_logs FOR EACH ROW EXECUTE FUNCTION {name}()")
            faults.append(name)
        async def row():
            return await owner.fetchrow("SELECT * FROM tenants WHERE id=$1", target)
        from app.services.scripts import seed_platform_sip_trunk
        monkeypatch.setattr(seed_platform_sip_trunk, "seed_for_tenant", AsyncMock())
        yield SimpleNamespace(owner=owner, pool=pool, client=SimpleNamespace(pool=pool), target=target,
                              actor=actor, admin=SimpleNamespace(id=str(actor)), audit=audit,
                              fail_audit=fail_audit, row=row, prefix=prefix, plan_id=plan_id)
    finally:
        if audit:
            await audit.aclose()
        if pool:
            await pool.close()
        for name in faults:
            await owner.execute(f"DROP TRIGGER {name} ON audit_logs")
            await owner.execute(f"DROP FUNCTION {name}()")
        await owner.execute("DELETE FROM audit_logs WHERE actor_id=$1", actor)
        await owner.execute("DELETE FROM user_profiles WHERE id=$1", actor)
        await owner.execute("DELETE FROM tenant_quotas WHERE tenant_id IN (SELECT id FROM tenants WHERE business_name LIKE $1)", prefix + "%")
        await owner.execute("DELETE FROM tenants WHERE business_name LIKE $1", prefix + "%")
        await owner.execute("DELETE FROM plans WHERE id=$1", plan_id)
        await owner.execute(f'DROP OWNED BY "{role}"')
        await owner.execute(f'DROP ROLE "{role}"')
        await owner.close()


async def test_global_ranking_includes_high_usage_after_first_alphabetical_page(tenant_db):
    db = tenant_db
    rows = [(uuid4(), f"{db.prefix} {index:03d}", index) for index in range(65)]
    await db.owner.executemany(
        "INSERT INTO tenants(id,business_name,minutes_used,subscription_status) VALUES($1,$2,$3,'active')",
        rows,
    )
    response = Response()
    result = await tenants.list_tenants(
        response, db.admin, db.client, search=db.prefix, status=None,
        include_archived=False, limit=5, offset=0, sort_by="minutes_used",
    )
    assert [row.minutes_used for row in result] == [64, 63, 62, 61, 60]
    assert str(result[0].id) == str(rows[-1][0])
    assert response.headers["X-Total-Count"] == "66"


async def change(db, operation, version, **values):
    if operation == "quota":
        return await tenants.update_tenant_quota(
            db.target, tenants.QuotaUpdateRequest(expected_updated_at=version, **values),
            db.admin, db.client, db.audit,
        )
    handler = getattr(tenants, operation + "_tenant")
    return await handler(db.target, tenants.TenantVersionRequest(expected_updated_at=version, **values),
                         db.admin, db.client, db.audit)


async def test_actual_receipt_survives_commit_and_same_target_replay_preserves_state(tenant_db):
    db = tenant_db; before = await db.row()
    result = await change(db, "suspend", before["updated_at"], reason="Entered review reason")
    saved = await db.row()
    assert result["status"] == saved["subscription_status"] == "suspended"
    assert result["updated_at"] == saved["updated_at"] > before["updated_at"]
    receipt = await db.owner.fetchrow("SELECT * FROM audit_logs WHERE event_id=$1", UUID(result["audit_event_id"]))
    assert receipt and receipt["actor_id"] == db.actor
    assert json.loads(receipt["metadata"])["reason"] == "Entered review reason"
    assert db.audit._recompute_row_hash(receipt) == receipt["entry_hash"]
    # A separate tenant-state flag is preserved by subscription writes.
    await db.owner.execute("UPDATE tenants SET status='pending_deletion' WHERE id=$1", db.target)
    replay = await change(db, "suspend", before["updated_at"])
    assert replay["changed"] is False and replay["audit_event_id"] is None
    assert (await db.row())["status"] == "pending_deletion"
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1", db.actor) == 1


async def test_concurrent_conflicting_versions_commit_one_effect_and_one_audit(tenant_db):
    db = tenant_db; before = await db.row()
    results = await asyncio.gather(change(db, "suspend", before["updated_at"]),
                                   change(db, "archive", before["updated_at"]), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    rejected = next(result for result in results if isinstance(result, Exception))
    assert isinstance(rejected, HTTPException) and rejected.status_code == 409
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1", db.actor) == 1


async def test_quota_preserves_unrelated_rules_and_reports_saved_values(tenant_db):
    db = tenant_db; before = await db.row()
    result = await change(db, "quota", before["updated_at"], max_concurrent_calls=7)
    saved = await db.row(); rules = json.loads(saved["calling_rules"])
    assert rules == {"max_concurrent_calls": 7, "timezone": "Asia/Karachi", "preserve_me": True}
    assert result["minutes_allocated"] == before["minutes_allocated"]
    assert result["max_concurrent_calls"] == 7
    with pytest.raises(HTTPException) as conflict:
        await change(db, "quota", before["updated_at"], minutes_allocated=900)
    assert conflict.value.status_code == 409


@pytest.mark.parametrize("operation", ["suspend", "quota"])
async def test_audit_failure_rolls_back_tenant_change(tenant_db, operation):
    db = tenant_db; before = await db.row(); await db.fail_audit()
    with pytest.raises(HTTPException) as failure:
        await change(db, operation, before["updated_at"], **({"minutes_allocated": 900} if operation == "quota" else {}))
    assert failure.value.status_code == 503
    assert dict(await db.row()) == dict(before)
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE actor_id=$1", db.actor) == 0


async def test_revoked_authority_while_waiting_is_checked_inside_transaction(tenant_db):
    db = tenant_db; before = await db.row()
    async with db.owner.transaction():
        await lock_user_administration(db.owner)
        pending = asyncio.create_task(change(db, "suspend", before["updated_at"]))
        for _ in range(100):
            if await db.owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted)"):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Tenant mutation did not wait on the real authority lock")
        await db.owner.execute("UPDATE user_profiles SET is_active=FALSE WHERE id=$1", db.actor)
    with pytest.raises(HTTPException) as rejected:
        await asyncio.wait_for(pending, 5)
    assert rejected.value.status_code == 403
    assert dict(await db.row()) == dict(before)


async def test_provisioning_rolls_back_when_its_audit_cannot_commit(tenant_db):
    db = tenant_db; await db.fail_audit()
    with pytest.raises(HTTPException) as rejected:
        await tenants.create_tenant(tenants.TenantCreateRequest(business_name=db.prefix + " new", plan_id=db.plan_id),
                                    Response(), db.admin, db.client, db.audit)
    assert rejected.value.status_code == 503
    assert await db.owner.fetchval("SELECT count(*) FROM tenants WHERE business_name=$1", db.prefix + " new") == 0


async def test_transactional_receipts_share_existing_buffered_hash_chain(tenant_db):
    db = tenant_db
    first = await db.audit.log(event_type=AuditEvent.CONFIG_CHANGED, action="synthetic_before",
                               actor_id=db.actor, tenant_id=db.target)
    await db.audit.flush()
    middle = await change(db, "suspend", (await db.row())["updated_at"])
    last = await db.audit.log(event_type=AuditEvent.CONFIG_CHANGED, action="synthetic_after",
                              actor_id=db.actor, tenant_id=db.target)
    await db.audit.flush()
    rows = await db.owner.fetch("SELECT * FROM audit_logs WHERE actor_id=$1 ORDER BY event_time", db.actor)
    assert [row["event_id"] for row in rows] == [first, UUID(middle["audit_event_id"]), last]
    assert rows[1]["previous_hash"] == rows[0]["entry_hash"]
    assert rows[2]["previous_hash"] == rows[1]["entry_hash"]
    assert all(db.audit._recompute_row_hash(row) == row["entry_hash"] for row in rows)
    assert all(db.audit._sign_entry(dict(row)) == row["signature"] for row in rows)


async def test_transactional_audit_refuses_missing_chain_lock(tenant_db):
    db = tenant_db
    async with acquire_with_tenant(db.pool, None) as conn:
        with pytest.raises(RuntimeError, match="chain lock"):
            await db.audit.log_in_transaction(conn, event_type=AuditEvent.CONFIG_CHANGED, action="denied")
