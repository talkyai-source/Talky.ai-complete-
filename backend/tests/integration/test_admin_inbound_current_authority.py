"""Normal-auth Inbound actor lifetime against the owned migrated PG fixture.

No authentication/service result is replaced. A synchronization-only wrapper
pauses the actual service call after the real route dependency has admitted it.
The application uses the restricted fixture role and commits its actual domain
audit. Cleanup of that append-only audit is OWNER-ONLY: inside one transaction,
temporarily use session_replication_role=replica for the exact owned audit row,
then restore the prior setting before normal FK-order cleanup. This mechanism
is permitted only in the explicitly reviewed disposable database, never runtime.
"""
import asyncio
import re
import time
from uuid import uuid4

import pytest
import pytest_asyncio

from app.domain.services.inbound_campaign_service import InboundCampaignService
from app.domain.services.telephony.inbound_admission import InboundAdmissionService
from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (
    call,
    crud_db,  # noqa: F401
    reviewed_write,
    signed_in,
)

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def inbound_authority_db(crud_db):  # noqa: F811
    db = crud_db
    ids = {name: uuid4() for name in ("campaign", "config", "phone", "trunk", "assignment")}
    tenant, actor = db.tenants[1], db.users[0]
    key = f"authority-quarantine-{uuid4()}"
    did = f"+1555{uuid4().int % 10_000_000:07d}"
    db.inbound_ids, db.inbound_key = ids, key
    db.report["inbound_cleanup_boundary"] = {
        "application_role_unchanged": True,
        "append_only_audit_not_mocked": True,
        "owner_only_transactional_replica_for_exact_owned_audit_delete": True,
        "setting_restored_before_normal_cleanup": False,
        "sequence_counters_not_claimed_identical": True,
    }
    # Verify the declared cleanup capability before exposing any owned records.
    # This does not write business rows or change application-role privileges.
    prior_replication = await db.owner.fetchval("SHOW session_replication_role")
    async with db.owner.transaction():
        await db.owner.execute("SET LOCAL session_replication_role='replica'")
        await db.owner.execute("SELECT set_config('session_replication_role',$1,TRUE)", prior_replication)
        assert await db.owner.fetchval("SHOW session_replication_role") == prior_replication
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
        assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
        assert await conn.fetchval("SHOW session_replication_role") == "origin"
    await db.owner.execute(
        f'GRANT SELECT,INSERT,UPDATE ON inbound_campaign_configs,inbound_did_assignments,'
        f'inbound_operation_idempotency TO "{role}"')
    await db.owner.execute(f'GRANT SELECT,INSERT ON inbound_audit_events TO "{role}"')
    await db.owner.execute(f'GRANT USAGE ON SEQUENCE inbound_audit_events_id_seq TO "{role}"')
    try:
        # A second owned administrator allows an actual reviewed block without
        # bypassing the last-admin invariant. It authenticates normally below.
        await db.owner.execute("UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[1])
        await db.owner.execute(
            """INSERT INTO campaigns(id,tenant_id,name,status,system_prompt,voice_id,direction)
               VALUES($1,$2,'Synthetic authority inbound','running','synthetic','synthetic','inbound')""",
            ids["campaign"], tenant)
        await db.owner.execute(
            """INSERT INTO tenant_phone_numbers(id,tenant_id,e164,provider,status,verification_method,verified_at)
               VALUES($1,$2,$3,'manual_admin','verified','manual_admin',NOW())""",
            ids["phone"], tenant, did)
        await db.owner.execute(
            """INSERT INTO tenant_sip_trunks(id,tenant_id,trunk_name,sip_domain,direction,is_active,
                   live_registration_status,live_status_checked_at)
               VALUES($1,$2,'Synthetic authority trunk','authority.invalid','inbound',TRUE,'loaded',NOW())""",
            ids["trunk"], tenant)
        await db.owner.execute(
            """INSERT INTO inbound_campaign_configs(id,tenant_id,campaign_id,name,status,config_checksum,
                   created_by,updated_by)
               VALUES($1,$2,$3,'Synthetic authority configuration','paused',$4,$5,$5)""",
            ids["config"], tenant, ids["campaign"], "a" * 64, actor)
        await db.owner.execute(
            """INSERT INTO inbound_did_assignments(id,tenant_id,phone_number_id,campaign_id,config_id,
                   sip_trunk_id,canonical_did,status,version,created_by,updated_by)
               VALUES($1,$2,$3,$4,$5,$6,$7,'paused',1,$8,$8)""",
            ids["assignment"], tenant, ids["phone"], ids["campaign"], ids["config"], ids["trunk"], did, actor)
        yield db
    finally:
        async with db.owner.transaction():
            await db.owner.execute("SET LOCAL app.bypass_rls='on'")
            previous = await db.owner.fetchval("SHOW session_replication_role")
            await db.owner.execute("SET LOCAL session_replication_role='replica'")
            # Never broaden this predicate to all platform/null-tenant audits.
            removed = await db.owner.execute(
                """DELETE FROM inbound_audit_events
                   WHERE resource_type='inbound_did_assignment' AND resource_id=$1
                     AND actor_id=$2 AND tenant_id IS NULL AND idempotency_key=$3
                     AND event_type='inbound_assignment_quarantined'""",
                ids["assignment"], actor, key)
            await db.owner.execute("SELECT set_config('session_replication_role',$1,TRUE)", previous)
            assert await db.owner.fetchval("SHOW session_replication_role") == previous
            db.report["inbound_cleanup_boundary"].update(
                setting_restored_before_normal_cleanup=True, exact_audit_delete=removed)
            await db.owner.execute(
                """DELETE FROM inbound_operation_idempotency WHERE scope_key='platform'
                   AND operation=$1 AND idempotency_key=$2 AND actor_id=$3""",
                f"platform_inbound_assignment.quarantine:{ids['assignment']}", key, actor)
            await db.owner.execute("DELETE FROM inbound_did_assignments WHERE id=$1 AND tenant_id=$2", ids["assignment"], tenant)
            await db.owner.execute("DELETE FROM inbound_campaign_configs WHERE id=$1 AND tenant_id=$2", ids["config"], tenant)
            await db.owner.execute("DELETE FROM tenant_phone_numbers WHERE id=$1 AND tenant_id=$2", ids["phone"], tenant)
            await db.owner.execute("DELETE FROM tenant_sip_trunks WHERE id=$1 AND tenant_id=$2", ids["trunk"], tenant)
            # The real SIP trigger writes immutable INSERT/DELETE policy rows.
            # Select their exact owned IDs after the ordinary trunk DELETE, then
            # use the same owner-only cleanup capability for only those rows.
            policy_ids = await db.owner.fetch(
                """SELECT id FROM tenant_policy_audit_log WHERE tenant_id=$1
                   AND table_name='tenant_sip_trunks' AND record_id=$2
                   AND source='db_trigger' AND actor_user_id IS NULL""",
                tenant, ids["trunk"])
            await db.owner.execute("SET LOCAL session_replication_role='replica'")
            policy_removed = await db.owner.execute(
                """DELETE FROM tenant_policy_audit_log WHERE id=ANY($1::uuid[])
                   AND tenant_id=$2 AND table_name='tenant_sip_trunks' AND record_id=$3
                   AND source='db_trigger' AND actor_user_id IS NULL""",
                [row["id"] for row in policy_ids], tenant, ids["trunk"])
            await db.owner.execute("SELECT set_config('session_replication_role',$1,TRUE)", previous)
            assert await db.owner.fetchval("SHOW session_replication_role") == previous
            db.report["inbound_cleanup_boundary"].update(
                exact_sip_policy_audit_delete=policy_removed,
                exact_sip_policy_audit_ids=[str(row["id"]) for row in policy_ids])
            await db.owner.execute("DELETE FROM campaigns WHERE id=$1 AND tenant_id=$2", ids["campaign"], tenant)
        assert await db.owner.fetchval("SHOW session_replication_role") == prior_replication


@pytest.mark.parametrize("revocation", ["block", "demote"])
async def test_quarantine_rechecks_actor_after_admitted_admin_is_blocked(inbound_authority_db, monkeypatch, revocation):
    db = inbound_authority_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = InboundCampaignService.set_assignment_quarantine

    async def delayed(service, **kwargs):
        if kwargs["assignment_id"] == str(db.inbound_ids["assignment"]):
            entered.set()
            await asyncio.wait_for(release.wait(), 20)
        return await original(service, **kwargs)

    monkeypatch.setattr(InboundCampaignService, "set_assignment_quarantine", delayed)
    async with signed_in(db, 0) as actor, signed_in(db, 1) as revoker:
        path = f"/admin/inbound/assignments/{db.inbound_ids['assignment']}/quarantine"
        pending = asyncio.create_task(call(db, actor, "POST", path,
            headers={"Idempotency-Key": db.inbound_key},
            json={"expected_version": 1, "reason": "Synthetic reviewed quarantine"}))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            revoked = await reviewed_write(db, revoker, "PATCH", db.users[0],
                **({"is_active": False} if revocation == "block" else {"role": "readonly"}))
            assert revoked.status_code == 200
            if revocation == "block":
                assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", db.users[0]) is False
            release.set()
            response = await asyncio.wait_for(pending, 20)
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        # Observe committed results from the separate owner connection; no
        # transaction/result substitution and no domain audit mock is used.
        assignment = dict(await db.owner.fetchrow(
            "SELECT status,version FROM inbound_did_assignments WHERE id=$1", db.inbound_ids["assignment"]))
        audit_count = await db.owner.fetchval(
            "SELECT count(*) FROM inbound_audit_events WHERE resource_id=$1 AND idempotency_key=$2",
            db.inbound_ids["assignment"], db.inbound_key)
        operation_count = await db.owner.fetchval(
            "SELECT count(*) FROM inbound_operation_idempotency WHERE scope_key='platform' AND idempotency_key=$1",
            db.inbound_key)
        db.report["inbound_authority_observation"] = {
            "barrier": "Actual service entry after normal route dependency admission",
            "revocation_http_status": revoked.status_code,
            "quarantine_http_status": response.status_code,
            "committed_assignment": assignment,
            "committed_domain_audits": audit_count,
            "committed_idempotency_rows": operation_count,
            "domain_receipt_observed_from_independent_connection": True,
            "provider_calls": False,
        }
        assert response.status_code == 403, "An administrator blocked after admission must not quarantine"
        assert assignment == {"status": "paused", "version": 1}
        assert audit_count == operation_count == 0


@pytest.mark.parametrize("operation", ["controls", "request", "approve", "billing"])
async def test_other_admin_routes_recheck_after_admission(inbound_authority_db, monkeypatch, operation):
    db = inbound_authority_db
    entered, release = asyncio.Event(), asyncio.Event()
    reason = "Synthetic reviewed authority decision"
    if operation == "controls":
        cls, name, method, path = InboundCampaignService, "set_platform_controls", "PATCH", "/admin/inbound/controls"
        body = {"inbound_enabled": False, "recording_enabled": False, "transfer_enabled": False,
                "settlement_enabled": False, "expected_version": 1, "reason": reason}
    elif operation == "request":
        cls, name, method, path = InboundCampaignService, "create_reassignment_request", "POST", "/admin/inbound/reassignments"
        body = {"assignment_id": str(db.inbound_ids["assignment"]), "target_tenant_id": str(db.tenants[2]),
                "target_campaign_id": str(uuid4()), "expected_version": 1, "reason": reason}
    elif operation == "approve":
        cls, name, method = InboundCampaignService, "approve_reassignment", "POST"
        path, body = f"/admin/inbound/reassignments/{uuid4()}/approve", {"reason": reason}
    else:
        cls, name, method = InboundAdmissionService, "resolve_billing_hold", "POST"
        path = f"/admin/inbound/tenants/{db.tenants[1]}/calls/{uuid4()}/billing-hold/resolve"
        body = {"hold_reason": "provider_answer_ambiguous", "decision": "release_unanswered",
                "evidence_type": "carrier_cdr", "evidence_reference": "synthetic-evidence",
                "evidence_sha256": "a" * 64, "adjudication_reason": reason}
    original = getattr(cls, name)

    async def delayed(service, *args, **kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 20)
        return await original(service, *args, **kwargs)

    monkeypatch.setattr(cls, name, delayed)
    controls_before = await db.owner.fetchrow("SELECT * FROM platform_runtime_controls WHERE id=1")
    async with signed_in(db, 0) as actor, signed_in(db, 1) as revoker:
        pending = asyncio.create_task(call(db, actor, method, path,
            headers={"Idempotency-Key": db.inbound_key}, json=body))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            assert (await reviewed_write(db, revoker, "PATCH", db.users[0], role="readonly")).status_code == 200
            release.set()
            response = await asyncio.wait_for(pending, 20)
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        assert response.status_code == 403, response.text
        assert await db.owner.fetchrow("SELECT * FROM platform_runtime_controls WHERE id=1") == controls_before
        assert await db.owner.fetchval(
            "SELECT count(*) FROM inbound_operation_idempotency WHERE idempotency_key=$1", db.inbound_key) == 0
        assert await db.owner.fetchval(
            "SELECT count(*) FROM inbound_audit_events WHERE idempotency_key=$1", db.inbound_key) == 0


async def quarantine(db, actor, **changes):
    return await call(db, actor, "POST",
        f"/admin/inbound/assignments/{db.inbound_ids['assignment']}/quarantine",
        headers={"Idempotency-Key": db.inbound_key},
        json={"expected_version": 1, "reason": "Synthetic reviewed quarantine", **changes})


async def test_committed_quarantine_receipt_replays_and_changed_intent_conflicts(inbound_authority_db):
    db = inbound_authority_db
    async with signed_in(db, 0) as actor:
        original = await quarantine(db, actor)
        assert original.status_code == 200, original.text
        # Treat the first response as lost; a new request must return the actual
        # original durable outcome, without needing the old reviewed version.
        replay = await quarantine(db, actor)
        assert replay.status_code == 200, replay.text
        assert replay.json() == {**original.json(), "idempotent_replay": True}
        changed = await quarantine(db, actor, reason="Different reviewed reason")
        assert changed.status_code == 409
    assert dict(await db.owner.fetchrow("SELECT status,version FROM inbound_did_assignments WHERE id=$1",
        db.inbound_ids["assignment"])) == {"status": "quarantined", "version": 2}
    assert await db.owner.fetchval("SELECT count(*) FROM inbound_audit_events WHERE idempotency_key=$1", db.inbound_key) == 1


async def test_domain_audit_failure_rolls_back_quarantine_and_same_key_recovers(inbound_authority_db):
    db = inbound_authority_db
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    async with signed_in(db, 0) as actor:
        await db.owner.execute(f'REVOKE INSERT ON inbound_audit_events FROM "{role}"')
        try:
            failed = await quarantine(db, actor)
            assert failed.status_code == 503, failed.text
            assert failed.json()["error"]["code"] == "inbound_admin_outcome_unconfirmed"
            assert await db.owner.fetchval("SELECT version FROM inbound_did_assignments WHERE id=$1", db.inbound_ids["assignment"]) == 1
            assert await db.owner.fetchval("SELECT count(*) FROM inbound_operation_idempotency WHERE idempotency_key=$1", db.inbound_key) == 0
        finally:
            await db.owner.execute(f'GRANT INSERT ON inbound_audit_events TO "{role}"')
        assert (await quarantine(db, actor)).status_code == 200


async def test_domain_lock_wait_is_bounded_and_releases_authority_fence(inbound_authority_db):
    db = inbound_authority_db
    async with signed_in(db, 0) as actor:
        async with db.owner.transaction():
            await db.owner.fetchrow("SELECT id FROM inbound_campaign_configs WHERE id=$1 FOR UPDATE", db.inbound_ids["config"])
            started = time.monotonic()
            blocked = await quarantine(db, actor)
            assert blocked.status_code == 503, blocked.text
            assert 2.5 <= time.monotonic() - started < 9
            # Another connection can acquire the real authority fence after the
            # failed request; no connection/GUC remains owned by the request.
            async with db.pool.acquire() as conn, conn.transaction():
                assert await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended('talky:platform-admin-authority',0))")
        assert (await quarantine(db, actor)).status_code == 200


async def test_admitted_mutation_commits_before_waiting_revocation(inbound_authority_db, monkeypatch):
    db = inbound_authority_db
    entered, release = asyncio.Event(), asyncio.Event()
    original = InboundCampaignService._claim_operation
    from app.api.v1.endpoints.admin import user_operations
    original_fence = user_operations.lock_user_administration
    revocation_at_fence = asyncio.Event()
    revocation_pid = None

    async def observed_revocation_fence(conn):
        nonlocal revocation_pid
        revocation_pid = conn.get_server_pid()
        revocation_at_fence.set()
        await original_fence(conn)

    monkeypatch.setattr(user_operations, "lock_user_administration", observed_revocation_fence)

    async def delayed(service, conn, **kwargs):
        if kwargs["idempotency_key"] == db.inbound_key:
            entered.set()
            await asyncio.wait_for(release.wait(), 6)
        return await original(service, conn, **kwargs)

    monkeypatch.setattr(InboundCampaignService, "_claim_operation", delayed)
    async with signed_in(db, 0) as actor, signed_in(db, 1) as revoker:
        pending = asyncio.create_task(quarantine(db, actor))
        revoked = None
        try:
            await asyncio.wait_for(entered.wait(), 10)
            revoked = asyncio.create_task(reviewed_write(db, revoker, "PATCH", db.users[0], is_active=False))
            await asyncio.wait_for(revocation_at_fence.wait(), 5)
            deadline = time.monotonic() + 2
            while True:
                waiting = await db.owner.fetchval(
                    "SELECT wait_event_type='Lock' AND wait_event='advisory' FROM pg_stat_activity WHERE pid=$1",
                    revocation_pid)
                if waiting or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.01)
            assert waiting is True, "Actual revocation SQL must wait on the held authority advisory fence"
            assert not revoked.done(), "Authority revocation must wait for admitted transaction"
            release.set()
            assert (await pending).status_code == 200
            assert (await revoked).status_code == 200
        finally:
            release.set()
            tasks = [pending, *([revoked] if revoked else [])]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", db.users[0]) is False
    assert await db.owner.fetchval("SELECT version FROM inbound_did_assignments WHERE id=$1", db.inbound_ids["assignment"]) == 2
