"""Actual migrated trunk/assignment/receipt SQL; configuration I/O is synthetic."""
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints.telephony_sip import trunks
from app.infrastructure.telephony import pjsip_config_generator

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def trunk_db(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path.endswith("_test")
    admin = await asyncpg.connect(dsn)
    role = "op04_" + uuid4().hex
    tenants, campaigns, ids, users = ([uuid4(), uuid4()] for _ in range(4))
    pool = None
    try:
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(f'GRANT SELECT,UPDATE,DELETE ON tenant_sip_trunks TO "{role}"')
        await admin.execute(f'GRANT SELECT,UPDATE ON tenants,campaigns TO "{role}"')
        await admin.execute(f'GRANT SELECT ON calls,tenant_route_policies,inbound_did_assignments TO "{role}"')
        await admin.execute(f'GRANT SELECT,INSERT,UPDATE ON tenant_telephony_idempotency TO "{role}"')
        await admin.execute(f'GRANT SELECT,INSERT ON tenant_policy_audit_log TO "{role}"')
        for tenant, campaign, identity, user in zip(tenants, campaigns, ids, users):
            await admin.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic OP04')", tenant)
            await admin.execute("INSERT INTO user_profiles(id,tenant_id,email) VALUES($1,$2,$3)", user, tenant, f"{user}@example.invalid")
            await admin.execute("INSERT INTO campaigns(id,tenant_id,name) VALUES($1,$2,'Synthetic OP04')", campaign, tenant)
            await admin.execute(
                """INSERT INTO tenant_sip_trunks(id,tenant_id,trunk_name,sip_domain,is_active,metadata,
                live_registration_status,live_status_checked_at)
                VALUES($1,$2,'Synthetic OP04','sip.example.invalid',FALSE,'{"register":false}',
                'reachable',NOW())""", identity, tenant,
            )

        async def init(conn):
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute("SELECT set_config('application_name',$1,false)", role)
            await conn.set_type_codec("jsonb", schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, init=init)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert tuple(flags.values()) == (False, False)
            assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_sip_trunks)")
        remove = AsyncMock()
        monkeypatch.setattr(pjsip_config_generator, "remove_trunk_config", remove)
        monkeypatch.setattr(trunks, "_enforce_ws_i_quota", AsyncMock(return_value=None))
        yield SimpleNamespace(admin=admin, pool=pool, role=role, tenants=tenants, campaigns=campaigns,
                              trunks=ids, remove=remove, actor=SimpleNamespace(id=str(users[0]), tenant_id=str(tenants[0])))
    finally:
        if pool:
            await pool.close()
        await admin.execute("DELETE FROM tenant_telephony_idempotency WHERE tenant_id=ANY($1::uuid[])", tenants)
        await admin.execute("DELETE FROM campaigns WHERE id=ANY($1::uuid[])", campaigns)
        await admin.execute("DELETE FROM tenant_sip_trunks WHERE id=ANY($1::uuid[])", ids)
        # The actual immutable audit trigger owns its parent references.
        # Retain synthetic audited users/tenants in this disposable database;
        # never weaken retention or disable a trigger for fixture cleanup.
        await admin.execute("DELETE FROM user_profiles WHERE id=ANY($1::uuid[]) AND NOT EXISTS(SELECT 1 FROM tenant_policy_audit_log WHERE actor_user_id=user_profiles.id)", users)
        await admin.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[]) AND NOT EXISTS(SELECT 1 FROM tenant_policy_audit_log WHERE tenant_id=tenants.id)", tenants)
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


def request():
    return Request({"type": "http", "method": "DELETE", "path": "/synthetic-trunk", "headers": []})


async def delete(db, *, index=0, key="synthetic-delete"):
    return await trunks.delete_sip_trunk(db.trunks[index], request(), key, db.actor, db.pool)


async def test_delete_receipt_replays_without_second_configuration_effect(trunk_db):
    db = trunk_db
    assert (await delete(db))["deleted"] is True
    replay = await delete(db)
    assert replay.status_code == 200 and json.loads(replay.body)["deleted"] is True
    assert db.remove.await_count == 1
    assert not await db.admin.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_sip_trunks WHERE id=$1)", db.trunks[0])


async def test_foreign_delete_cannot_remove_configuration_or_owner_record(trunk_db):
    db = trunk_db
    assert (await delete(db, index=1)).status_code == 404
    db.remove.assert_not_awaited()
    assert await db.admin.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_sip_trunks WHERE id=$1)", db.trunks[1])


async def test_failed_configuration_cleanup_retains_trunk_and_rolls_back_receipt(trunk_db):
    db = trunk_db
    db.remove.side_effect = RuntimeError("synthetic reload failure")
    with pytest.raises(HTTPException) as error:
        await delete(db)
    assert error.value.status_code == 503
    assert await db.admin.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_sip_trunks WHERE id=$1)", db.trunks[0])
    assert not await db.admin.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_telephony_idempotency WHERE tenant_id=$1)", db.tenants[0])


@pytest.mark.parametrize("dependency", ["campaign", "pool"])
async def test_current_json_assignment_blocks_disabled_trunk_deletion(trunk_db, dependency):
    db = trunk_db
    if dependency == "campaign":
        await db.admin.execute("UPDATE campaigns SET calling_config=jsonb_build_object('trunk',jsonb_build_object('id',$2::text)) WHERE id=$1", db.campaigns[0], str(db.trunks[0]))
    else:
        await db.admin.execute("UPDATE tenants SET calling_rules=jsonb_build_object('pool_trunk',jsonb_build_object('id',$2::text)) WHERE id=$1", db.tenants[0], str(db.trunks[0]))
    assert (await delete(db)).status_code == 409
    db.remove.assert_not_awaited()


async def test_assignment_share_lock_prevents_delete_from_overtaking_dependency(trunk_db, monkeypatch):
    db = trunk_db
    await db.admin.execute("UPDATE tenant_sip_trunks SET is_active=TRUE WHERE id=$1", db.trunks[0])
    entered, proceed = asyncio.Event(), asyncio.Event()
    fetch = trunks._fetch_assignable_trunk

    async def hold_after_read(*args):
        row = await fetch(*args)
        entered.set()
        await proceed.wait()
        return row

    monkeypatch.setattr(trunks, "_fetch_assignable_trunk", hold_after_read)
    assignment = asyncio.create_task(trunks.set_campaign_trunk_assignment(
        trunks.CampaignTrunkBody(campaign_id=str(db.campaigns[0]), trunk_id=db.trunks[0]),
        request(), db.actor, db.pool,
    ))
    deletion = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        deletion = asyncio.create_task(delete(db))

        async def await_database_lock():
            while not await db.admin.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=$1 AND wait_event_type='Lock')", db.role
            ):
                await asyncio.sleep(0.01)
        await asyncio.wait_for(await_database_lock(), timeout=5)
        assert not deletion.done()
        proceed.set()
        result = await asyncio.wait_for(assignment, timeout=5)
        assert result.trunk_id == str(db.trunks[0])
        assert (await asyncio.wait_for(deletion, timeout=5)).status_code == 409
        await db.admin.execute("UPDATE tenant_sip_trunks SET is_active=FALSE WHERE id=$1", db.trunks[0])
        assert (await delete(db, key="after-disable")).status_code == 409
        db.remove.assert_not_awaited()
    finally:
        proceed.set()
        for task in (assignment, deletion):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*[t for t in (assignment, deletion) if t is not None], return_exceptions=True)
