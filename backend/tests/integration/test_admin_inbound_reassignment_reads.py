"""Normal-auth HTTP recovery of actual saved Inbound decisions on owned PG.

No principal or service result is mocked. Historical pagination rows are owned
fixture data (rejected, not invented approvals). Exact owner-only immutable audit
cleanup follows the existing Inbound fixture and restores replication immediately.
"""
import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (
    call, crud_db, reviewed_write, signed_in,  # noqa: F401
)
from tests.integration.test_admin_inbound_current_authority import inbound_authority_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def reassignment_db(inbound_authority_db):  # noqa: F811
    db = inbound_authority_db
    ids = {name: uuid4() for name in ("campaign", "config", "phone", "trunk", "call")}
    db.read_ids = ids
    source, target = db.tenants[1:3]
    assignment = db.inbound_ids["assignment"]
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    db.read_role = role
    await db.owner.execute(f'GRANT SELECT,INSERT,UPDATE ON inbound_reassignment_requests,tenant_phone_numbers TO "{role}"')
    await db.owner.execute(f'GRANT SELECT ON tenant_sip_trunks TO "{role}"')
    await db.owner.execute(f'GRANT UPDATE ON campaigns TO "{role}"')
    try:
        did = await db.owner.fetchval("SELECT canonical_did FROM inbound_did_assignments WHERE id=$1", assignment)
        await db.owner.execute("""INSERT INTO campaigns(id,tenant_id,name,status,system_prompt,voice_id,direction)
            VALUES($1,$2,'Synthetic recovery target','running','synthetic','synthetic','inbound')""", ids["campaign"], target)
        await db.owner.execute("""INSERT INTO inbound_campaign_configs(id,tenant_id,campaign_id,name,status,config_checksum,created_by,updated_by)
            VALUES($1,$2,$3,'Synthetic recovery target','paused',$4,$5,$5)""", ids["config"], target, ids["campaign"], "a"*64, db.users[0])
        await db.owner.execute("""INSERT INTO tenant_phone_numbers(id,tenant_id,e164,provider,status,verification_method,verified_at)
            VALUES($1,$2,$3,'manual_admin','verified','manual_admin',NOW())""", ids["phone"], target, did)
        await db.owner.execute("""INSERT INTO tenant_sip_trunks(id,tenant_id,trunk_name,sip_domain,direction,is_active,
            live_registration_status,live_status_checked_at) VALUES($1,$2,'Synthetic recovery target','recovery.invalid','inbound',TRUE,'loaded',NOW())""", ids["trunk"], target)
        await db.owner.execute("""INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status,talklee_call_id,direction,
            provider,provider_call_id,called_did,called_did_id,assignment_id,route_version,config_version,route_snapshot,
            admission_status,consent_status,processing_status,billing_status,reserved_seconds,duration_seconds)
            VALUES($1,$2,$3,$4::text,'completed',$5,'inbound','asterisk',$6,$4::text,$7,$8,1,1,'{}',
            'allowed','not_required','completed','finalized',0,30)""", ids["call"], source, db.inbound_ids["campaign"], did,
            "IN"+ids["call"].hex[:18], "history-"+str(ids["call"]), db.inbound_ids["phone"], assignment)
        yield db
    finally:
        async with db.owner.transaction():
            await db.owner.execute("SET LOCAL app.bypass_rls='on'")
            rows = await db.owner.fetch("SELECT id,approved_assignment_id FROM inbound_reassignment_requests WHERE assignment_id=$1 AND target_tenant_id=$2 AND target_config_id=$3", assignment, target, ids["config"])
            request_ids = [row["id"] for row in rows]
            moved_ids = [row["approved_assignment_id"] for row in rows if row["approved_assignment_id"]]
            resources = [assignment, *request_ids, *moved_ids]
            audit_ids = await db.owner.fetch("SELECT id FROM inbound_audit_events WHERE actor_id=ANY($1::uuid[]) AND resource_id=ANY($2::uuid[])", db.users[:2], resources)
            old = await db.owner.fetchval("SHOW session_replication_role")
            assert old == "origin"
            await db.owner.execute("SET LOCAL session_replication_role='replica'")
            await db.owner.execute("DELETE FROM inbound_audit_events WHERE id=ANY($1::bigint[]) AND actor_id=ANY($2::uuid[]) AND resource_id=ANY($3::uuid[])", [row["id"] for row in audit_ids], db.users[:2], resources)
            await db.owner.execute("SELECT set_config('session_replication_role',$1,TRUE)", old)
            assert await db.owner.fetchval("SHOW session_replication_role") == old
            await db.owner.execute("DELETE FROM inbound_operation_idempotency WHERE actor_id=ANY($1::uuid[]) AND resource_id=ANY($2::uuid[])", db.users[:2], resources)
            await db.owner.execute("DELETE FROM inbound_reassignment_requests WHERE id=ANY($1::uuid[]) AND assignment_id=$2", request_ids, assignment)
            await db.owner.execute("DELETE FROM calls WHERE id=$1 AND tenant_id=$2", ids["call"], source)
            await db.owner.execute("DELETE FROM inbound_did_assignments WHERE id=ANY($1::uuid[]) AND config_id=$2 AND tenant_id=$3", moved_ids, ids["config"], target)
            await db.owner.execute("DELETE FROM inbound_campaign_configs WHERE id=$1 AND tenant_id=$2", ids["config"], target)
            await db.owner.execute("DELETE FROM tenant_phone_numbers WHERE id=$1 AND tenant_id=$2", ids["phone"], target)
            await db.owner.execute("DELETE FROM tenant_sip_trunks WHERE id=$1 AND tenant_id=$2", ids["trunk"], target)
            policy = await db.owner.fetch("SELECT id FROM tenant_policy_audit_log WHERE tenant_id=$1 AND table_name='tenant_sip_trunks' AND record_id=$2 AND source='db_trigger' AND actor_user_id IS NULL", target, ids["trunk"])
            await db.owner.execute("SET LOCAL session_replication_role='replica'")
            await db.owner.execute("DELETE FROM tenant_policy_audit_log WHERE id=ANY($1::uuid[]) AND tenant_id=$2 AND record_id=$3", [row["id"] for row in policy], target, ids["trunk"])
            await db.owner.execute("SELECT set_config('session_replication_role',$1,TRUE)", old)
            assert await db.owner.fetchval("SHOW session_replication_role") == old
            await db.owner.execute("DELETE FROM campaigns WHERE id=$1 AND tenant_id=$2", ids["campaign"], target)


async def seed_history(db, count, at=None):
    identifiers = [uuid4() for _ in range(count)]
    at = at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    await db.owner.executemany("""INSERT INTO inbound_reassignment_requests(id,tenant_id,source_tenant_id,
        assignment_id,target_tenant_id,target_campaign_id,target_config_id,expected_assignment_version,
        status,reason,requested_by,idempotency_key,requested_at)
        VALUES($1,$2,$2,$3,$4,$5,$6,1,'rejected','Synthetic retained history',$7,$8,$9)""",
        [(item, db.tenants[1], db.inbound_ids["assignment"], db.tenants[2], db.read_ids["campaign"],
          db.read_ids["config"], db.users[0], "history-"+str(item), at) for item in identifiers])
    return identifiers


async def test_actual_approval_recovers_saved_decision_after_response_is_discarded(reassignment_db):
    db = reassignment_db
    async with signed_in(db, 0) as requester, signed_in(db, 1) as approver:
        created = await call(db, requester, "POST", "/admin/inbound/reassignments",
            headers={"Idempotency-Key": "request-"+str(uuid4())}, json={
                "assignment_id": str(db.inbound_ids["assignment"]), "target_tenant_id": str(db.tenants[2]),
                "target_campaign_id": str(db.read_ids["campaign"]), "expected_version": 1,
                "reason": "Synthetic reviewed source ownership"})
        assert created.status_code == 201, created.text
        request_id = created.json()["id"]
        before = await call(db, requester, "GET", f"/admin/inbound/reassignments/{request_id}")
        assert before.status_code == 200 and before.json() == created.json()
        # Use the actual mutator, discard its response for recovery; this HTTP
        # test does not claim browser transport withholding (covered separately).
        response = await call(db, approver, "POST", f"/admin/inbound/reassignments/{request_id}/approve",
            headers={"Idempotency-Key": "approve-"+str(uuid4())}, json={"reason": "Synthetic second administrator approval"})
        assert response.status_code == 200, response.text
        expected = response.json()
    async with signed_in(db, 1) as fresh:
        recovered = await call(db, fresh, "GET", f"/admin/inbound/reassignments/{request_id}")
        assert recovered.status_code == 200 and recovered.json() == expected
        history = await call(db, fresh, "GET", "/admin/inbound/reassignments?status=all&limit=50")
        assert history.status_code == 200
        assert next(row for row in history.json()["items"] if row["id"] == request_id) == expected
        assert expected["approved_by"] == str(db.users[1]) and expected["requested_by"] == str(db.users[0])
        assert expected["decision_reason"] == "Synthetic second administrator approval" and expected["decided_at"]
    assert dict(await db.owner.fetchrow("SELECT tenant_id,assignment_id FROM calls WHERE id=$1", db.read_ids["call"])) == {
        "tenant_id": db.tenants[1], "assignment_id": db.inbound_ids["assignment"]}
    assert await db.owner.fetchval("SELECT count(*) FROM inbound_audit_events WHERE resource_id=$1 AND event_type='inbound_reassignment_approved'", UUID(request_id)) == 1
    assert await db.owner.fetchval("SELECT count(*) FROM inbound_did_assignments WHERE config_id=$1", db.read_ids["config"]) == 1


async def test_keyset_reaches_more_than_500_equal_timestamp_rows_without_duplicates(reassignment_db):
    db = reassignment_db
    seeded = await seed_history(db, 503)
    seen = []
    cursor = None
    first = True
    async with signed_in(db) as actor:
        while True:
            response = await call(db, actor, "GET", "/admin/inbound/reassignments",
                params={"status": "rejected", "limit": 100, **({"cursor": cursor} if cursor else {})})
            assert response.status_code == 200, response.text
            page = response.json()
            assert set(page) == {"items", "has_more", "next_cursor"}
            owned = [UUID(row["id"]) for row in page["items"] if UUID(row["id"]) in seeded]
            seen.extend(owned)
            if first:
                # A later insertion sorts before the continuation; it must not
                # shift the older page boundaries or duplicate existing rows.
                await seed_history(db, 1, datetime.now(timezone.utc))
                first = False
            if not page["has_more"]:
                assert page["next_cursor"] is None
                break
            assert page["next_cursor"] and page["next_cursor"] != cursor
            cursor = page["next_cursor"]
        assert seen == sorted(seeded, reverse=True) and len(seen) == len(set(seen)) == 503


async def test_exact_missing_and_invalid_cursor_are_distinct_from_storage_unavailability(reassignment_db):
    db = reassignment_db
    identifiers = await seed_history(db, 2)
    async with signed_in(db) as actor:
        missing = await call(db, actor, "GET", f"/admin/inbound/reassignments/{uuid4()}")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "reassignment_not_found"
        malformed = await call(db, actor, "GET", "/admin/inbound/reassignments/not-a-uuid")
        assert malformed.status_code == 422
        first = await call(db, actor, "GET", "/admin/inbound/reassignments?status=rejected&limit=1")
        cursor = first.json()["next_cursor"]
        for params in ({"status": "all", "cursor": cursor}, {"status": "rejected", "cursor": "bad!"}):
            bad = await call(db, actor, "GET", "/admin/inbound/reassignments", params=params)
            assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_cursor"
        await db.owner.execute(f'REVOKE SELECT ON inbound_reassignment_requests FROM "{db.read_role}"')
        try:
            for path in (f"/admin/inbound/reassignments/{identifiers[0]}", "/admin/inbound/reassignments?status=all"):
                failed = await call(db, actor, "GET", path)
                assert failed.status_code == 503 and failed.json()["error"]["code"] == "inbound_reassignment_read_unavailable"
        finally:
            await db.owner.execute(f'GRANT SELECT ON inbound_reassignment_requests TO "{db.read_role}"')
        assert (await call(db, actor, "GET", f"/admin/inbound/reassignments/{identifiers[0]}")).status_code == 200


async def test_normal_tenant_and_currently_demoted_admin_cannot_read_history(reassignment_db):
    db = reassignment_db
    identifier = (await seed_history(db, 1))[0]
    paths = (f"/admin/inbound/reassignments/{identifier}", "/admin/inbound/reassignments?status=all")
    async with signed_in(db, 2) as tenant:
        for path in paths:
            assert (await call(db, tenant, "GET", path)).status_code == 403
    async with signed_in(db, 0) as former_admin, signed_in(db, 1) as revoker:
        assert (await reviewed_write(db, revoker, "PATCH", db.users[0], role="readonly")).status_code == 200
        for path in paths:
            assert (await call(db, former_admin, "GET", path)).status_code == 403
    assert await db.owner.fetchval("SELECT count(*) FROM inbound_reassignment_requests WHERE id=$1", identifier) == 1


async def test_cancelled_read_releases_real_transaction_without_mutation(reassignment_db, monkeypatch):
    import asyncpg
    db = reassignment_db
    identifier = (await seed_history(db, 1))[0]
    started = asyncio.Event()
    original = asyncpg.Connection.fetchrow

    async def waiting(conn, query, *args, **kwargs):
        if query == "SELECT * FROM inbound_reassignment_requests WHERE id=$1":
            started.set()
            await asyncio.Event().wait()
        return await original(conn, query, *args, **kwargs)

    # Synchronization-only port blocks before the SELECT; no result/principal
    # is manufactured. HTTP cancellation must propagate through the real stack.
    monkeypatch.setattr(asyncpg.Connection, "fetchrow", waiting)
    async with signed_in(db) as actor:
        task = asyncio.create_task(call(db, actor, "GET", f"/admin/inbound/reassignments/{identifier}"))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        monkeypatch.setattr(asyncpg.Connection, "fetchrow", original)
        assert (await call(db, actor, "GET", f"/admin/inbound/reassignments/{identifier}")).status_code == 200
