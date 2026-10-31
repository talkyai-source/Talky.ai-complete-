"""Actual migrated trunk/assignment/receipt SQL; configuration I/O is synthetic.

Delete contract (owner report 2026-10-14, "I am not able to delete any PBX"): a
tenant can always delete its own line. Pins, the pool allotment and route policies
that point at it are cleared, inbound numbers on it are archived, and the row is
removed outright unless inbound history still references it, in which case it is
retired in place (``deleted_at``) and disappears from every API.
"""
import asyncio
import json
import os
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints.telephony_sip import trunks
from app.api.v1.endpoints.telephony_sip.schemas import SIPTrunkUpdateRequest
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
    # Rows a test adds beyond the two synthetic tenants; removed child-first.
    extra = {key: [] for key in ("calls", "assignments", "configs", "phones", "policies", "campaigns", "trunks")}
    pool, shimmed = None, []
    try:
        # A database stamped at 0021 (the CI lineage) skipped revision 0006, so it lacks the
        # two test-result columns production has and every trunk handler selects. Add them
        # for this test only and remove them again, so the shared database is left as found.
        for column, kind in (("last_tested_at", "timestamptz"), ("last_test_result", "jsonb")):
            if not await admin.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                "WHERE table_name='tenant_sip_trunks' AND column_name=$1)", column,
            ):
                await admin.execute(f"ALTER TABLE tenant_sip_trunks ADD COLUMN {column} {kind}")
                shimmed.append(column)
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(f'GRANT SELECT,UPDATE,DELETE ON tenant_sip_trunks TO "{role}"')
        await admin.execute(f'GRANT SELECT,UPDATE ON tenants,campaigns,inbound_did_assignments,inbound_campaign_configs TO "{role}"')
        await admin.execute(f'GRANT INSERT ON inbound_audit_events TO "{role}"')
        await admin.execute(f'GRANT USAGE ON SEQUENCE inbound_audit_events_id_seq TO "{role}"')
        await admin.execute(f'GRANT SELECT,DELETE ON tenant_route_policies TO "{role}"')
        await admin.execute(f'GRANT SELECT ON calls TO "{role}"')
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
                              trunks=ids, remove=remove, extra=extra,
                              actor=SimpleNamespace(id=str(users[0]), tenant_id=str(tenants[0])))
    finally:
        if pool:
            await pool.close()
        await admin.execute("DELETE FROM tenant_telephony_idempotency WHERE tenant_id=ANY($1::uuid[])", tenants)
        await admin.execute("DELETE FROM calls WHERE id=ANY($1::uuid[])", extra["calls"])
        await admin.execute("DELETE FROM inbound_did_assignments WHERE id=ANY($1::uuid[])", extra["assignments"])
        await admin.execute("DELETE FROM inbound_campaign_configs WHERE id=ANY($1::uuid[])", extra["configs"])
        await admin.execute("DELETE FROM tenant_phone_numbers WHERE id=ANY($1::uuid[])", extra["phones"])
        await admin.execute("DELETE FROM tenant_route_policies WHERE id=ANY($1::uuid[])", extra["policies"])
        await admin.execute("DELETE FROM campaigns WHERE id=ANY($1::uuid[])", campaigns + extra["campaigns"])
        await admin.execute("DELETE FROM tenant_sip_trunks WHERE id=ANY($1::uuid[])", ids + extra["trunks"])
        # The actual immutable audit trigger owns its parent references.
        # Retain synthetic audited users/tenants in this disposable database;
        # never weaken retention or disable a trigger for fixture cleanup.
        await admin.execute("DELETE FROM user_profiles WHERE id=ANY($1::uuid[]) AND NOT EXISTS(SELECT 1 FROM tenant_policy_audit_log WHERE actor_user_id=user_profiles.id) AND NOT EXISTS(SELECT 1 FROM inbound_audit_events WHERE actor_id=user_profiles.id)", users)
        await admin.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[]) AND NOT EXISTS(SELECT 1 FROM tenant_policy_audit_log WHERE tenant_id=tenants.id) AND NOT EXISTS(SELECT 1 FROM inbound_audit_events WHERE tenant_id=tenants.id)", tenants)
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        for column in shimmed:
            await admin.execute(f"ALTER TABLE tenant_sip_trunks DROP COLUMN {column}")
        await admin.close()


def request():
    return Request({"type": "http", "method": "DELETE", "path": "/synthetic-trunk", "headers": []})


async def delete(db, *, index=0, key="synthetic-delete"):
    return await trunks.delete_sip_trunk(db.trunks[index], request(), key, db.actor, db.pool)


async def seed_inbound_history(db, trunk, statuses=("active", "archived")):
    """Inbound numbers bound to ``trunk`` (tenant 0), one assignment per status."""
    tenant, assignments = db.tenants[0], []
    for status in statuses:
        campaign, config, phone, assignment = (uuid4() for _ in range(4))
        did = f"+1555{int(assignment.hex[:7], 16) % 10_000_000:07d}"
        archived = status == "archived"
        await db.admin.execute(
            "INSERT INTO campaigns(id,tenant_id,name,direction) VALUES($1,$2,'Synthetic inbound','inbound')",
            campaign, tenant,
        )
        await db.admin.execute(
            "INSERT INTO tenant_phone_numbers(id,tenant_id,e164,status,verification_method,verified_at) "
            "VALUES($1,$2,$3,'verified','manual_admin',NOW())", phone, tenant, did,
        )
        await db.admin.execute(
            "INSERT INTO inbound_campaign_configs(id,tenant_id,campaign_id,name,status,config_checksum) "
            "VALUES($1,$2,$3,'Synthetic config',$4,$5)",
            config, tenant, campaign, "archived" if archived else "active", "a" * 64,
        )
        await db.admin.execute(
            "INSERT INTO inbound_did_assignments(id,tenant_id,phone_number_id,campaign_id,config_id,"
            "sip_trunk_id,canonical_did,status,valid_from,valid_to) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,NOW()-INTERVAL '2 days',"
            + ("NOW()-INTERVAL '1 day'" if archived else "NULL") + ")",
            assignment, tenant, phone, campaign, config, trunk, did, status,
        )
        db.extra["campaigns"].append(campaign)
        db.extra["phones"].append(phone)
        db.extra["configs"].append(config)
        db.extra["assignments"].append(assignment)
        assignments.append(assignment)
    return assignments


async def test_delete_receipt_replays_without_second_configuration_effect(trunk_db):
    db = trunk_db
    first = await delete(db)
    assert first == {
        "id": str(db.trunks[0]), "deleted": True, "mode": "removed",
        "archived_inbound_numbers": 0, "paused_inbound_campaigns": 0, "unpinned_campaigns": 0,
    }
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


async def test_failed_cleanup_leaves_every_dependency_untouched(trunk_db):
    db = trunk_db
    (assignment, _archived) = await seed_inbound_history(db, db.trunks[0])
    await db.admin.execute(
        "UPDATE campaigns SET calling_config=jsonb_build_object('trunk',jsonb_build_object('id',$2::text)) WHERE id=$1",
        db.campaigns[0], str(db.trunks[0]),
    )
    db.remove.side_effect = RuntimeError("synthetic reload failure")
    with pytest.raises(HTTPException) as error:
        await delete(db)
    assert error.value.status_code == 503
    pin = await db.admin.fetchval("SELECT calling_config->'trunk'->>'id' FROM campaigns WHERE id=$1", db.campaigns[0])
    state = await db.admin.fetchrow("SELECT status, version FROM inbound_did_assignments WHERE id=$1", assignment)
    live = await db.admin.fetchval("SELECT deleted_at IS NULL FROM tenant_sip_trunks WHERE id=$1", db.trunks[0])
    assert (pin, state["status"], state["version"], live) == (str(db.trunks[0]), "active", 1, True)


async def test_delete_retires_a_line_that_inbound_history_still_references(trunk_db):
    db = trunk_db
    active, archived = await seed_inbound_history(db, db.trunks[0])
    live_config, live_campaign = await db.admin.fetchrow(
        "SELECT config_id, campaign_id FROM inbound_did_assignments WHERE id=$1", active)
    await db.admin.execute("UPDATE campaigns SET status='running' WHERE id=$1", live_campaign)
    before = {
        row["id"]: row for row in await db.admin.fetch(
            "SELECT id, status, version, valid_from, valid_to FROM inbound_did_assignments WHERE id=ANY($1::uuid[])",
            [active, archived],
        )
    }
    result = await delete(db)
    assert result == {
        "id": str(db.trunks[0]), "deleted": True, "mode": "archived",
        "archived_inbound_numbers": 1, "paused_inbound_campaigns": 1, "unpinned_campaigns": 0,
    }
    # The inbound campaign whose number was archived is paused, not stranded "active"
    # (its lifecycle refuses an archived assignment), and the pause is audited.
    config = await db.admin.fetchrow("SELECT status, version FROM inbound_campaign_configs WHERE id=$1", live_config)
    assert (config["status"], config["version"]) == ("paused", 2)
    assert await db.admin.fetchval("SELECT status FROM campaigns WHERE id=$1", live_campaign) == "paused"
    audit = await db.admin.fetch(
        "SELECT event_type, resource_id, actor_id, metadata->>'sip_trunk_id' AS trunk FROM inbound_audit_events "
        "WHERE tenant_id=$1", db.tenants[0])
    assert [tuple(r.values()) for r in audit] == [
        ("inbound_campaign_paused", live_config, UUID(db.actor.id), str(db.trunks[0]))]
    db.remove.assert_awaited_once()
    row = await db.admin.fetchrow(
        "SELECT deleted_at, is_active, live_registration_status FROM tenant_sip_trunks WHERE id=$1", db.trunks[0]
    )
    assert row["deleted_at"] is not None and row["is_active"] is False
    assert row["live_registration_status"] == "inactive"
    now = {
        r["id"]: r for r in await db.admin.fetch(
            "SELECT id, status, version, valid_from, valid_to FROM inbound_did_assignments WHERE id=ANY($1::uuid[])",
            [active, archived],
        )
    }
    assert now[active]["status"] == "archived"
    assert now[active]["version"] == before[active]["version"] + 1
    assert now[active]["valid_to"] > now[active]["valid_from"]
    # The assignment that was already archived is history and is left exactly as it was.
    assert (now[archived]["status"], now[archived]["version"], now[archived]["valid_to"]) == (
        "archived", before[archived]["version"], before[archived]["valid_to"],
    )

    # Invisible to the API ...
    listed = await trunks.list_sip_trunks(request(), db.actor, db.pool)
    assert str(db.trunks[0]) not in {str(t.id) for t in listed}
    # ... and the name is free again (the unique index only covers live lines).
    await db.admin.execute(
        "INSERT INTO tenant_sip_trunks(tenant_id,trunk_name,sip_domain) VALUES($1,'synthetic op04','sip.example.invalid')",
        db.tenants[0],
    )
    db.extra["trunks"].extend(await db.admin.fetchval(
        "SELECT array_agg(id) FROM tenant_sip_trunks WHERE tenant_id=$1 AND id<>$2", db.tenants[0], db.trunks[0],
    ))
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.admin.execute(
            "INSERT INTO tenant_sip_trunks(tenant_id,trunk_name,sip_domain) VALUES($1,'SYNTHETIC OP04','sip.example.invalid')",
            db.tenants[0],
        )


async def test_an_expired_assignment_keeps_its_earlier_end_when_the_line_is_deleted(trunk_db):
    db = trunk_db
    (assignment,) = await seed_inbound_history(db, db.trunks[0], statuses=("paused",))
    ended = await db.admin.fetchval(
        "UPDATE inbound_did_assignments SET valid_to=NOW()-INTERVAL '1 hour' WHERE id=$1 RETURNING valid_to", assignment
    )
    assert (await delete(db))["archived_inbound_numbers"] == 1
    row = await db.admin.fetchrow("SELECT status, valid_to FROM inbound_did_assignments WHERE id=$1", assignment)
    assert row["status"] == "archived" and row["valid_to"] == ended


async def test_delete_unpins_campaigns_clears_pool_and_route_policies(trunk_db):
    db = trunk_db
    await db.admin.execute(
        "UPDATE campaigns SET calling_config=jsonb_build_object('trunk',jsonb_build_object('id',$2::text),'keep','me') WHERE id=$1",
        db.campaigns[0], str(db.trunks[0]),
    )
    await db.admin.execute(
        "UPDATE tenants SET calling_rules=calling_rules||jsonb_build_object('pool_trunk',jsonb_build_object('id',$2::text)) WHERE id=$1",
        db.tenants[0], str(db.trunks[0]),
    )
    policy = await db.admin.fetchval(
        "INSERT INTO tenant_route_policies(tenant_id,policy_name,match_pattern,target_trunk_id) "
        "VALUES($1,'synthetic route','.*',$2) RETURNING id", db.tenants[0], db.trunks[0],
    )
    db.extra["policies"].append(policy)
    # Another campaign pinned to a DIFFERENT line is not this line's business.
    other_campaign = uuid4()
    await db.admin.execute(
        "INSERT INTO campaigns(id,tenant_id,name,calling_config) VALUES($1,$2,'other',jsonb_build_object('trunk',jsonb_build_object('id',$3::text)))",
        other_campaign, db.tenants[0], str(uuid4()),
    )
    db.extra["campaigns"].append(other_campaign)

    result = await delete(db)
    assert result["deleted"] is True and result["mode"] == "removed"
    assert result["unpinned_campaigns"] == 1 and result["archived_inbound_numbers"] == 0
    config = await db.admin.fetchval("SELECT calling_config FROM campaigns WHERE id=$1", db.campaigns[0])
    assert json.loads(config) == {"keep": "me"}
    assert await db.admin.fetchval("SELECT calling_rules ? 'pool_trunk' FROM tenants WHERE id=$1", db.tenants[0]) is False
    assert not await db.admin.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_route_policies WHERE id=$1)", policy)
    assert await db.admin.fetchval("SELECT calling_config ? 'trunk' FROM campaigns WHERE id=$1", other_campaign) is True
    assert not await db.admin.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_sip_trunks WHERE id=$1)", db.trunks[0])


async def test_an_active_line_is_turned_off_and_removed_in_one_call(trunk_db):
    db = trunk_db
    await db.admin.execute("UPDATE tenant_sip_trunks SET is_active=TRUE WHERE id=$1", db.trunks[0])
    result = await delete(db)
    assert result["deleted"] is True and result["mode"] == "removed"
    db.remove.assert_awaited_once()
    assert not await db.admin.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_sip_trunks WHERE id=$1)", db.trunks[0])


@pytest.mark.parametrize("dependency", ["campaign", "pool"])
async def test_delete_is_refused_while_another_tenant_uses_the_shared_line(trunk_db, dependency):
    db = trunk_db
    # Legacy data: tenant 1 holds a pin / pool allotment on a line tenant 0 owns.
    if dependency == "campaign":
        await db.admin.execute(
            "UPDATE campaigns SET calling_config=jsonb_build_object('trunk',jsonb_build_object('id',$2::text)) WHERE id=$1",
            db.campaigns[1], str(db.trunks[0]),
        )
    else:
        await db.admin.execute(
            "UPDATE tenants SET calling_rules=jsonb_build_object('pool_trunk',jsonb_build_object('id',$2::text)) WHERE id=$1",
            db.tenants[1], str(db.trunks[0]),
        )
    response = await delete(db)
    assert response.status_code == 409
    db.remove.assert_not_awaited()
    assert await db.admin.fetchval("SELECT deleted_at IS NULL FROM tenant_sip_trunks WHERE id=$1", db.trunks[0])
    if dependency == "campaign":
        assert await db.admin.fetchval("SELECT calling_config->'trunk'->>'id' FROM campaigns WHERE id=$1", db.campaigns[1]) == str(db.trunks[0])
    else:
        assert await db.admin.fetchval("SELECT calling_rules->'pool_trunk'->>'id' FROM tenants WHERE id=$1", db.tenants[1]) == str(db.trunks[0])


@pytest.mark.parametrize("hours_old,status,expected", [
    (5, "in_progress", 200),   # a stale row must not block deletion forever
    (1, "in_progress", 409),   # a call from the last 4 hours may still be live
    (1, "completed", 200),     # finished calls never block
])
async def test_only_a_recent_unfinished_call_blocks_deletion(trunk_db, hours_old, status, expected):
    db = trunk_db
    call = uuid4()
    db.extra["calls"].append(call)
    await db.admin.execute(
        "INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status,created_at) "
        "VALUES($1,$2,$3,'+15550000001',$4,NOW()-make_interval(hours => $5::int))",
        call, db.tenants[0], db.campaigns[0], status, hours_old,
    )
    result = await delete(db)
    assert getattr(result, "status_code", 200) == expected
    if expected == 409:
        db.remove.assert_not_awaited()
        assert await db.admin.fetchval("SELECT deleted_at IS NULL FROM tenant_sip_trunks WHERE id=$1", db.trunks[0])
    else:
        assert result["deleted"] is True


async def test_another_tenants_open_call_never_blocks_this_deletion(trunk_db):
    db = trunk_db
    call = uuid4()
    db.extra["calls"].append(call)
    await db.admin.execute(
        "INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status,created_at) "
        "VALUES($1,$2,$3,'+15550000002','in_progress',NOW())",
        call, db.tenants[1], db.campaigns[1],
    )
    assert (await delete(db))["deleted"] is True


async def test_a_deleted_line_is_invisible_and_cannot_be_used(trunk_db):
    db = trunk_db
    await seed_inbound_history(db, db.trunks[0])
    assert (await delete(db))["mode"] == "archived"
    trunk_id = db.trunks[0]

    assert (await delete(db, key="second-delete")).status_code == 404
    activate = await trunks.activate_sip_trunk(trunk_id, request(), "act-key", None, db.actor, db.pool)
    deactivate = await trunks.deactivate_sip_trunk(trunk_id, request(), "deact-key", None, db.actor, db.pool)
    update = await trunks.update_sip_trunk(
        trunk_id, SIPTrunkUpdateRequest(metadata={"region": "eu"}), request(), "upd-key", None, db.actor, db.pool
    )
    probe = await trunks.test_sip_trunk(trunk_id, request(), db.actor, db.pool)
    assert [r.status_code for r in (activate, deactivate, update, probe)] == [404, 404, 404, 404]

    async def assign():
        campaign = await trunks.set_campaign_trunk_assignment(
            trunks.CampaignTrunkBody(campaign_id=db.campaigns[0], trunk_id=trunk_id), request(), db.actor, db.pool
        )
        pool_assignment = await trunks.set_pool_assignment(
            trunks.PoolAssignmentBody(pool_trunk_id=trunk_id), request(), db.actor, db.pool
        )
        return campaign.status_code, pool_assignment.status_code

    assert await assign() == (400, 400)
    assert await db.admin.fetchval("SELECT calling_config ? 'trunk' FROM campaigns WHERE id=$1", db.campaigns[0]) is False
    # Nothing on the retired row changed after the first delete.
    row = await db.admin.fetchrow("SELECT is_active, deleted_at FROM tenant_sip_trunks WHERE id=$1", trunk_id)
    assert row["is_active"] is False and row["deleted_at"] is not None

    # Defence in depth: even a retired row that something flagged active and
    # healthy stays unassignable, unlistable and un-activatable. Only the
    # deleted_at predicate stops these, not is_active.
    await db.admin.execute(
        "UPDATE tenant_sip_trunks SET is_active=TRUE, direction='both', "
        "metadata='{\"pool\":true,\"register\":false}', live_registration_status='reachable', "
        "live_status_checked_at=NOW() WHERE id=$1", trunk_id,
    )
    assert await assign() == (400, 400)
    assert str(trunk_id) not in {str(t.id) for t in await trunks.list_sip_trunks(request(), db.actor, db.pool)}
    assert (await trunks.activate_sip_trunk(trunk_id, request(), "act-key-2", None, db.actor, db.pool)).status_code == 404
    assert await db.admin.fetchval("SELECT calling_config ? 'trunk' FROM campaigns WHERE id=$1", db.campaigns[0]) is False


async def test_the_test_endpoint_stores_the_steps_and_refuses_a_second_click_within_ten_seconds(trunk_db, monkeypatch):
    from app.api.v1.endpoints.telephony_sip import trunk_probe

    async def resolve(*, host, port, socktype):
        return socket.AF_INET, ("203.0.113.9", port)

    reply = {"ok": True, "latency_ms": 12, "transport": "udp", "target": "sip.example.invalid:5060",
             "sip_code": "200", "detail": "raw probe text"}
    monkeypatch.setattr(trunk_probe, "resolve_sip_target", resolve)
    monkeypatch.setattr(trunk_probe, "probe_sip_endpoint", AsyncMock(return_value=reply))
    monkeypatch.setattr(trunks, "_trunk_test_redis", lambda: None)
    trunks._test_history.clear()

    db_id = trunk_db.trunks[0]
    first = await trunks.test_sip_trunk(db_id, request(), trunk_db.actor, trunk_db.pool)
    assert first.status == "reachable" and first.ok is True
    stored = await trunk_db.admin.fetchrow(
        "SELECT last_tested_at, last_test_result FROM tenant_sip_trunks WHERE id=$1", db_id
    )
    saved = json.loads(stored["last_test_result"])
    assert stored["last_tested_at"] is not None
    assert [s["key"] for s in saved["steps"]] == ["address", "reachability", "credentials", "registration"]
    assert saved["status"] == "reachable" and saved["detail"] == saved["verdict"]

    # last_tested_at, read back from the real row, is what enforces the 10-second gap.
    second = await trunks.test_sip_trunk(db_id, request(), trunk_db.actor, trunk_db.pool)
    assert second.status_code == 429 and 1 <= int(second.headers["Retry-After"]) <= 11
    await trunk_db.admin.execute(
        "UPDATE tenant_sip_trunks SET last_tested_at = NOW() - INTERVAL '11 seconds' WHERE id=$1", db_id
    )
    assert (await trunks.test_sip_trunk(db_id, request(), trunk_db.actor, trunk_db.pool)).status == "reachable"


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
        # The delete waited for the in-flight assignment, then cleaned up the pin
        # it created: no campaign is left pointing at a removed line.
        outcome = await asyncio.wait_for(deletion, timeout=5)
        assert outcome["deleted"] is True and outcome["unpinned_campaigns"] == 1
        assert await db.admin.fetchval("SELECT calling_config ? 'trunk' FROM campaigns WHERE id=$1", db.campaigns[0]) is False
        assert not await db.admin.fetchval("SELECT EXISTS(SELECT 1 FROM tenant_sip_trunks WHERE id=$1)", db.trunks[0])
    finally:
        proceed.set()
        for task in (assignment, deletion):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*[t for t in (assignment, deletion) if t is not None], return_exceptions=True)


# --- migration 0070 on real PostgreSQL ------------------------------------
# The migration's own statements, run against a throwaway schema (never the shared
# tables): upgrade keeps data and makes the name index partial, a second run is a
# no-op, downgrade refuses while a deleted line shares a live line's name and
# otherwise restores the old full index.

def _migration_statements(monkeypatch, direction, schema):
    import importlib

    migration = importlib.import_module("Alembic.versions.0070_trunk_soft_delete")
    captured = []
    monkeypatch.setattr(migration, "op", SimpleNamespace(execute=lambda sql: captured.append(str(sql))))
    getattr(migration, direction)()
    return [sql.replace("public.", f"{schema}.") for sql in captured]


async def _run(admin, statements):
    async with admin.transaction():
        for statement in statements:
            await admin.execute(statement)


@pytest_asyncio.fixture
async def trunk_schema():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path.endswith("_test")
    schema, tenant = "trunk70_" + uuid4().hex, uuid4()
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
        # The pre-0070 shape: the full per-tenant unique name index.
        await admin.execute(
            f"CREATE TABLE {schema}.tenant_sip_trunks (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "tenant_id uuid NOT NULL, trunk_name varchar(100) NOT NULL)"
        )
        await admin.execute(
            f"CREATE UNIQUE INDEX idx_tenant_sip_trunks_tenant_name_unique "
            f"ON {schema}.tenant_sip_trunks (tenant_id, lower(trunk_name))"
        )
        await admin.execute(
            f"INSERT INTO {schema}.tenant_sip_trunks (tenant_id, trunk_name) VALUES ($1,'Line A'), ($1,'Line B')", tenant
        )
        yield SimpleNamespace(admin=admin, schema=schema, tenant=tenant)
    finally:
        await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.close()


async def _index_definition(db):
    return await db.admin.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname=$1 AND indexname='idx_tenant_sip_trunks_tenant_name_unique'",
        db.schema,
    )


async def test_0070_upgrade_keeps_rows_frees_deleted_names_and_is_idempotent(trunk_schema, monkeypatch):
    db, table = trunk_schema, f"{trunk_schema.schema}.tenant_sip_trunks"
    upgrade = _migration_statements(monkeypatch, "upgrade", db.schema)
    await _run(db.admin, upgrade)
    assert await db.admin.fetchval(f"SELECT count(*) FROM {table} WHERE deleted_at IS NULL") == 2
    assert "WHERE (deleted_at IS NULL)" in await _index_definition(db)

    with pytest.raises(asyncpg.UniqueViolationError):  # live names are still unique, case-insensitively
        await db.admin.execute(f"INSERT INTO {table} (tenant_id, trunk_name) VALUES ($1,'LINE a')", db.tenant)
    await db.admin.execute(f"UPDATE {table} SET deleted_at = NOW() WHERE trunk_name = 'Line A'")
    await db.admin.execute(f"INSERT INTO {table} (tenant_id, trunk_name) VALUES ($1,'line a')", db.tenant)

    before = await _index_definition(db)
    await _run(db.admin, upgrade)  # a second run changes nothing and does not fail
    assert await _index_definition(db) == before
    assert await db.admin.fetchval(f"SELECT count(*) FROM {table}") == 3


async def test_0070_downgrade_refuses_a_name_shared_with_a_deleted_line_then_restores_the_old_index(trunk_schema, monkeypatch):
    db, table = trunk_schema, f"{trunk_schema.schema}.tenant_sip_trunks"
    await _run(db.admin, _migration_statements(monkeypatch, "upgrade", db.schema))
    await db.admin.execute(f"UPDATE {table} SET deleted_at = NOW() WHERE trunk_name = 'Line A'")
    await db.admin.execute(f"INSERT INTO {table} (tenant_id, trunk_name) VALUES ($1,'Line A')", db.tenant)

    downgrade = _migration_statements(monkeypatch, "downgrade", db.schema)
    with pytest.raises(asyncpg.RaiseError, match="Cannot downgrade 0070_trunk_soft_delete"):
        await _run(db.admin, downgrade)
    # The refusal rolled everything back: column and partial index are still there.
    assert await db.admin.fetchval(f"SELECT count(*) FROM {table} WHERE deleted_at IS NOT NULL") == 1
    assert "WHERE (deleted_at IS NULL)" in await _index_definition(db)

    await db.admin.execute(f"DELETE FROM {table} WHERE deleted_at IS NOT NULL")
    await _run(db.admin, downgrade)
    assert "deleted_at" not in await _index_definition(db)
    assert not await db.admin.fetchval(
        "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=$1 AND column_name='deleted_at')",
        db.schema,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.admin.execute(f"INSERT INTO {table} (tenant_id, trunk_name) VALUES ($1,'LINE A')", db.tenant)


async def test_0070_is_a_no_op_where_the_table_does_not_exist(trunk_schema, monkeypatch):
    db = trunk_schema
    await db.admin.execute(f"DROP TABLE {db.schema}.tenant_sip_trunks")
    for direction in ("upgrade", "downgrade"):
        await _run(db.admin, _migration_statements(monkeypatch, direction, db.schema))
