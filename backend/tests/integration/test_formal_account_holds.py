"""Formal tenant/partner transitions against migrated restricted-role PostgreSQL.

Only owned disposable fixtures are mutated. No PBX, notification or provider egress.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException

from app.domain.services.suspension_service import SuspensionService, SuspensionType
from tests.integration.test_admin_authority_invariant import authority_db  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def account_db(authority_db):  # noqa: F811
    db = authority_db
    db.actor = await db.new_user()
    db.target = await db.new_user(profile_role="user")
    db.partner = uuid4()
    await db.owner.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON white_label_partners TO "{db.role}"')
    await db.owner.execute(f'GRANT INSERT ON security_sessions,refresh_tokens TO "{db.role}"')
    await db.owner.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON login_attempts TO "{db.role}"')
    await db.owner.execute(f'GRANT SELECT ON calls,call_legs,call_events TO "{db.role}"')
    await db.owner.execute(
        "INSERT INTO white_label_partners(id,tenant_id,company_name) VALUES($1,$2,'Synthetic formal account hold')",
        db.partner, db.tenant_id,
    )
    await db.owner.execute(
        "UPDATE tenants SET white_label_partner_id=$2,subscription_status='active' WHERE id=$1", db.tenant_id, db.partner,
    )
    # The inherited fixture replaces only the old propagation method. Keep
    # real service behavior here; no absent delivery queue is emulated.
    db.service = SuspensionService(db.pool, audit_logger=db.audit)
    try:
        yield db
    finally:
        await db.owner.execute("DELETE FROM suspension_events WHERE target_id=ANY($1::uuid[])",
                               [db.tenant_id, db.partner, *db.users])
        await db.owner.execute("DELETE FROM audit_logs WHERE tenant_id=$1", db.tenant_id)
        await db.owner.execute("UPDATE tenants SET white_label_partner_id=NULL,suspended_by=NULL WHERE id=$1",
                               db.tenant_id)
        await db.owner.execute("DELETE FROM white_label_partners WHERE id=$1", db.partner)


async def hold(db, target="tenant", **kwargs):
    method = db.service.suspend_tenant if target == "tenant" else db.service.suspend_partner
    identity = db.tenant_id if target == "tenant" else db.partner
    return await method(identity, SuspensionType.ADMIN, "synthetic", "Synthetic formal account hold",
                        suspended_by=db.actor, **kwargs)


async def restore(db, target="tenant", **kwargs):
    method = db.service.restore_tenant if target == "tenant" else db.service.restore_partner
    identity = db.tenant_id if target == "tenant" else db.partner
    return await method(identity, db.actor, "Reviewed synthetic formal hold", **kwargs)


@pytest.mark.parametrize("target", ["tenant", "partner"])
async def test_committed_hold_has_durable_audit_and_no_absent_queue_dependency(account_db, target):
    db = account_db
    result = await hold(db, target)
    assert result.audit_event_id
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE event_id=$1", result.audit_event_id) == 1
    assert result.propagated_to == []
    assert result.propagation_pending
    assert await db.owner.fetchval("SELECT to_regclass('public.suspension_propagation_queue')") is None


async def test_partner_restore_preserves_an_independent_tenant_hold(account_db, monkeypatch):
    db = account_db
    # Isolate restoration from the independent missing-queue defect.
    monkeypatch.setattr(db.service, "_propagate_to_services", AsyncMock(return_value={"success": [], "failed": []}))
    first = await hold(db, "tenant")
    await hold(db, "partner")
    await restore(db, "partner")
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1", db.tenant_id) == "suspended"
    assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1",
                                   first.suspension_id)


@pytest.mark.parametrize("first,last", [("tenant","partner"),("partner","tenant")])
@pytest.mark.parametrize("restore_first", ["tenant","partner"])
async def test_overlap_restores_only_after_last_hold_and_keeps_exact_members(account_db, first, last, restore_first):
    db=account_db
    one=await hold(db,first)
    two=await hold(db,last)
    receipts={first:one,last:two}
    result=await restore(db,restore_first,expected_suspension_id=receipts[restore_first].suspension_id)
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1",db.tenant_id) == "suspended"
    assert await db.owner.fetchval("SELECT status FROM tenant_users WHERE user_id=$1",db.target) == "suspended"
    if restore_first == "tenant":
        assert result.effective_status == "suspended" and result.remaining_hold_ids
        current=await db.service.get_status(tenant_id=db.tenant_id)
        assert current.is_suspended and current.effective_status == "suspended"
        assert current.active_suspension is None  # Direct-event history, not fabricated parent history.
    other="partner" if restore_first=="tenant" else "tenant"
    await restore(db,other,expected_suspension_id=receipts[other].suspension_id)
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1",db.tenant_id) == "active"
    assert await db.owner.fetchval("SELECT status FROM tenant_users WHERE user_id=$1",db.target) == "active"


@pytest.mark.parametrize("target",["tenant","partner"])
async def test_same_policy_concurrent_retry_returns_one_immutable_audit_receipt(account_db,target):
    db=account_db
    a,b=await asyncio.gather(hold(db,target),hold(db,target))
    assert a.suspension_id == b.suspension_id and a.audit_event_id == b.audit_event_id
    assert sorted([a.changed,b.changed]) == [False,True]
    assert await db.owner.fetchval("SELECT count(*) FROM suspension_events WHERE target_id=$1",
        db.tenant_id if target=="tenant" else db.partner) == 1
    with pytest.raises(HTTPException) as conflict:
        await hold(db,target,evidence={"new":"evidence"})
    assert conflict.value.status_code == 409


async def test_non_immediate_retry_cannot_silently_upgrade_policy(account_db):
    db=account_db
    saved=await hold(db,immediate=False)
    with pytest.raises(HTTPException) as conflict:
        await hold(db,immediate=True)
    assert conflict.value.status_code == 409
    row=await db.owner.fetchrow("SELECT evidence FROM suspension_events WHERE suspension_id=$1",saved.suspension_id)
    assert json.loads(row["evidence"])["_formal_account_hold"]["immediate"] is False


@pytest.mark.parametrize("target",["tenant","partner"])
async def test_lost_restore_response_retry_cannot_restore_a_later_hold(account_db,target):
    db=account_db
    a=await hold(db,target)
    restored=await restore(db,target,expected_suspension_id=a.suspension_id)
    replay=await restore(db,target,expected_suspension_id=a.suspension_id)
    assert not replay.changed and replay.audit_event_id == restored.audit_event_id
    b=await hold(db,target)
    with pytest.raises(HTTPException) as conflict:
        await restore(db,target,expected_suspension_id=a.suspension_id)
    assert conflict.value.status_code == 409
    assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1",b.suspension_id)


@pytest.mark.parametrize("target",["tenant","partner"])
@pytest.mark.parametrize("operation",["suspend","restore"])
async def test_required_audit_failure_rolls_back_account_transition(account_db,target,operation):
    db=account_db
    prior=await hold(db,target) if operation=="restore" else None
    await db.owner.execute(f'REVOKE INSERT ON audit_logs FROM "{db.role}"')
    with pytest.raises(Exception):
        if prior:
            await restore(db,target,expected_suspension_id=prior.suspension_id)
        else:
            await hold(db,target)
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1",db.tenant_id) == (
        "suspended" if prior else "active")
    assert await db.owner.fetchval("SELECT count(*) FROM suspension_events WHERE target_id=$1 AND is_active",
        db.tenant_id if target=="tenant" else db.partner) == (1 if prior else 0)


@pytest.mark.parametrize("target",["tenant","partner"])
@pytest.mark.parametrize("state",["inactive","unverified","demoted"])
async def test_current_actor_required_inside_account_transaction(account_db,target,state):
    db=account_db
    change={"inactive":"is_active=FALSE","unverified":"is_verified=FALSE,email_verified_at=NULL","demoted":"role='user'"}[state]
    await db.owner.execute(f"UPDATE user_profiles SET {change} WHERE id=$1",db.actor)
    with pytest.raises(HTTPException) as denied:
        await hold(db,target)
    assert denied.value.status_code == 403
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1",db.tenant_id) == "active"


async def test_server_policy_cannot_be_forged_and_exact_state_survives_json_roundtrip(account_db):
    db=account_db
    result=await hold(db,evidence={"user_note":"retained",
        "_formal_account_hold":{"version":1,"immediate":False,"affected_tenant_ids":[]},
        "_suspended_tenant_user_ids":[str(uuid4())]})
    event=await db.owner.fetchrow("SELECT * FROM suspension_events WHERE suspension_id=$1",result.suspension_id)
    doc=json.loads(event["evidence"]); policy=doc["_formal_account_hold"]
    assert policy["immediate"] is True and policy["affected_tenant_ids"] == [str(db.tenant_id)]
    assert policy["tenant_state"][str(db.tenant_id)]["held"]["suspended_at"] == event["suspended_at"].isoformat()
    assert doc["user_note"] == "retained"
    assert doc["_suspended_tenant_user_ids"] == [m["id"] for m in policy["member_state"]]
    metadata=await db.owner.fetchval("SELECT metadata FROM audit_logs WHERE event_id=$1",event["audit_log_id"])
    assert json.loads(metadata)["formal_account_hold_version"] == 1
    status=await db.service.get_status(tenant_id=db.tenant_id)
    assert status.active_suspension.evidence["_formal_account_hold"]["version"] == 1


async def test_prior_account_restrictions_and_subscription_are_not_reactivated(account_db):
    db=account_db
    await db.owner.execute("UPDATE tenants SET status='suspended',suspension_reason='Independent prior restriction',subscription_status='suspended' WHERE id=$1",db.tenant_id)
    result=await hold(db,"partner")
    await restore(db,"partner",expected_suspension_id=result.suspension_id)
    row=await db.owner.fetchrow("SELECT status,suspension_reason,subscription_status FROM tenants WHERE id=$1",db.tenant_id)
    assert tuple(row.values()) == ("suspended","Independent prior restriction","suspended")


async def test_manual_membership_and_account_edits_are_not_reversed(account_db):
    db=account_db
    saved=await hold(db)
    await db.owner.execute("UPDATE tenant_users SET status='suspended',updated_at=clock_timestamp() WHERE user_id=$1",db.target)
    await restore(db,expected_suspension_id=saved.suspension_id)
    assert await db.owner.fetchval("SELECT status FROM tenant_users WHERE user_id=$1",db.target) == "suspended"
    saved=await hold(db)
    await db.owner.execute("UPDATE tenants SET suspension_reason='Separate operator restriction' WHERE id=$1",db.tenant_id)
    result=await restore(db,expected_suspension_id=saved.suspension_id)
    assert result.restoration_warnings
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1",db.tenant_id) == "suspended"


@pytest.mark.parametrize("target",["tenant","partner"])
async def test_redis_hint_failure_preserves_committed_receipt(account_db,target):
    db=account_db
    db.service.redis=SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("synthetic unavailable")))
    saved=await hold(db,target)
    assert saved.audit_event_id and saved.propagated_to == []
    assert saved.propagation_failed == ["suspension_events"]
    assert saved.propagation_pending == ["telephony_policy_check"]
    restored=await restore(db,target,expected_suspension_id=saved.suspension_id)
    assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE event_id=$1",restored.audit_event_id) == 1


async def test_legacy_forged_policy_has_no_restore_permission(account_db):
    db=account_db
    saved=await hold(db)
    await db.owner.execute("UPDATE suspension_events SET audit_log_id=NULL WHERE suspension_id=$1",saved.suspension_id)
    with pytest.raises(HTTPException) as denied:
        await restore(db,expected_suspension_id=saved.suspension_id)
    assert denied.value.status_code == 409
    assert await db.owner.fetchval("SELECT status FROM tenant_users WHERE user_id=$1",db.target) == "suspended"


@pytest.mark.parametrize("target",["tenant","partner"])
async def test_granted_appeal_binds_exact_hold_and_commits_audit_atomically(account_db,target):
    db=account_db
    saved=await hold(db,target)
    granted=await db.service.review_appeal(saved.suspension_id,db.actor,"granted","Reviewed synthetic evidence")
    assert granted["audit_event_id"] and granted["restored"]
    newer=await hold(db,target)
    with pytest.raises(HTTPException) as conflict:
        await db.service.review_appeal(saved.suspension_id,db.actor,"granted","Stale retry")
    assert conflict.value.status_code == 409
    assert await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1",newer.suspension_id)


@pytest.mark.parametrize("target",["tenant","partner"])
async def test_scheduler_restores_only_exact_expired_supported_hold(account_db,target):
    db=account_db
    method=db.service.suspend_tenant if target=="tenant" else db.service.suspend_partner
    saved=await method(db.tenant_id if target=="tenant" else db.partner, SuspensionType.TEMPORARY,
        "synthetic","Temporary fixture",suspended_by=db.actor,duration_hours=1)
    restore_method=db.service.restore_tenant if target=="tenant" else db.service.restore_partner
    with pytest.raises(HTTPException) as early:
        await restore_method(db.tenant_id if target=="tenant" else db.partner,None,"Scheduler",
                             expected_suspension_id=saved.suspension_id)
    assert early.value.status_code == 409
    await db.owner.execute("UPDATE suspension_events SET suspended_until=clock_timestamp()-INTERVAL '1 second' WHERE suspension_id=$1",saved.suspension_id)
    results=await db.service.process_auto_restores()
    assert any(r["suspension_id"]==str(saved.suspension_id) and r["restored"] for r in results)
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1",db.tenant_id) == "active"


async def test_reparent_fk_does_not_deadlock_partner_status_hold(account_db,monkeypatch):
    db=account_db
    pid=[]; real=db.audit.lock_transaction
    async def capture(conn):
        pid.append(conn.get_server_pid())
        await real(conn)
    monkeypatch.setattr(db.audit,"lock_transaction",capture)
    task=None
    try:
        async with db.owner.transaction():
            await db.owner.execute("UPDATE tenants SET white_label_partner_id=NULL WHERE id=$1",db.tenant_id)
            task=asyncio.create_task(hold(db,"partner"))
            for _ in range(200):
                if pid and await db.owner.fetchval("SELECT cardinality(pg_blocking_pids($1))>0",pid[0]):
                    break
                await asyncio.sleep(.01)
            else:
                pytest.fail("Partner hold did not wait on the deliberately locked child")
            # This FK KEY SHARE is compatible with the parent's NO KEY UPDATE.
            await asyncio.wait_for(db.owner.execute(
                "UPDATE tenants SET white_label_partner_id=$2 WHERE id=$1",db.tenant_id,db.partner),2)
        result=await asyncio.wait_for(task,5)
        assert result.audit_event_id
    finally:
        if task:
            await asyncio.gather(task,return_exceptions=True)


@pytest.mark.parametrize("target",["tenant","partner"])
@pytest.mark.parametrize("kind",["login","refresh"])
@pytest.mark.parametrize("first",["hold","issuance"])
async def test_immediate_hold_and_auth_issuance_serialize_in_both_orders(account_db,monkeypatch,target,kind,first):
    import importlib
    from fastapi import Response
    from app.api.v1.endpoints.auth import refresh as refresh_api
    from app.api.v1.endpoints.auth.schemas import LoginRequest
    from app.core.security import credential_issuance
    from app.core.security.sessions import validate_session
    from app.core.db_utils import acquire_with_tenant
    from app.domain.services import minutes_quota
    from app.services.scripts import tenant_minutes
    from tests.integration.test_formal_user_credential_hold import credentials,request
    login_api=importlib.import_module("app.api.v1.endpoints.auth.login")
    db=account_db
    await db.owner.execute("UPDATE user_profiles SET email=$2,password_hash='synthetic-password' WHERE id=$1",
                           db.target,f"{db.target}@example.com")
    old=await credentials(db,db.target)
    monkeypatch.setattr(login_api,"verify_password",lambda *_:True)
    monkeypatch.setattr(login_api,"rehash_if_needed",lambda *_:None)
    monkeypatch.setattr(minutes_quota,"compute_minutes_status",AsyncMock(return_value=minutes_quota.unavailable_minutes("synthetic")))
    monkeypatch.setattr(tenant_minutes,"compute_tenant_minutes_status",AsyncMock(return_value=minutes_quota.unavailable_minutes("synthetic")))
    started,release=asyncio.Event(),asyncio.Event()
    pids={}
    module=login_api if kind=="login" else refresh_api
    real_fence=module.lock_user_credential_issuance
    async def issue_fence(conn,user):
        pids["issuance"]=conn.get_server_pid()
        result=await real_fence(conn,user)
        if first=="issuance":
            started.set()
            await asyncio.wait_for(release.wait(),10)
        return result
    monkeypatch.setattr(module,"lock_user_credential_issuance",issue_fence)
    real_profiles=db.service._account_profiles
    async def hold_fence(conn,tenant_ids):
        pids["hold"]=conn.get_server_pid()
        profiles=await real_profiles(conn,tenant_ids)
        if first=="hold":
            started.set()
            await asyncio.wait_for(release.wait(),10)
        return profiles
    monkeypatch.setattr(db.service,"_account_profiles",hold_fence)
    async def issue():
        if kind=="login":
            return await login_api.login.__wrapped__(request(),LoginRequest(email=f"{db.target}@example.com",password="synthetic-password"),
                db.client,SimpleNamespace(log=AsyncMock(),log_security_event=AsyncMock()))
        return await refresh_api.refresh.__wrapped__(request(),Response(),db.client,old.refresh)
    tasks={}
    try:
        tasks[first]=asyncio.create_task(hold(db,target) if first=="hold" else issue())
        await asyncio.wait_for(started.wait(),5)
        other="issuance" if first=="hold" else "hold"
        tasks[other]=asyncio.create_task(issue() if other=="issuance" else hold(db,target))
        for _ in range(200):
            if other in pids and await db.owner.fetchval(
                "SELECT cardinality(pg_blocking_pids($1))>0",pids[other]):
                break
            await asyncio.sleep(.01)
        else:
            pytest.fail("Second transaction did not wait on the actual profile fence")
        release.set()
        results=await asyncio.gather(tasks["hold"],tasks["issuance"],return_exceptions=True)
        assert not isinstance(results[0],BaseException),results[0]
        if first=="hold":
            assert isinstance(results[1],HTTPException) and results[1].status_code==401
        else:
            assert not isinstance(results[1],BaseException),results[1]
        assert await db.owner.fetchval(
            "SELECT count(*) FROM security_sessions WHERE user_id=$1 AND NOT revoked",db.target)==0
        await restore(db,target,expected_suspension_id=results[0].suspension_id)
        async with acquire_with_tenant(db.pool,None) as conn:
            assert await validate_session(conn,old.cookie) is None
        with pytest.raises(HTTPException) as ended:
            await refresh_api.refresh.__wrapped__(request(),Response(),db.client,old.refresh)
        assert ended.value.status_code==401
    finally:
        release.set()
        await asyncio.gather(*tasks.values(),return_exceptions=True)


async def test_non_immediate_hold_preserves_existing_session_revocation_semantics(account_db):
    from app.core.db_utils import acquire_with_tenant
    from app.core.security.sessions import validate_session
    from tests.integration.test_formal_user_credential_hold import credentials
    db=account_db
    old=await credentials(db,db.target)
    saved=await hold(db,immediate=False)
    assert saved.revoked_sessions==0
    assert not await db.owner.fetchval("SELECT revoked FROM security_sessions WHERE id=$1",old.sid)
    await restore(db,expected_suspension_id=saved.suspension_id)
    async with acquire_with_tenant(db.pool,None) as conn:
        assert await validate_session(conn,old.cookie) is not None


@pytest.mark.parametrize("target",["tenant","partner"])
async def test_denied_appeal_retains_audited_policy_and_required_audit(account_db,target):
    db=account_db
    saved=await hold(db,target)
    denied=await db.service.review_appeal(saved.suspension_id,db.actor,"denied","Evidence reviewed")
    replay=await db.service.review_appeal(saved.suspension_id,db.actor,"denied","Evidence reviewed")
    assert denied["audit_event_id"]==replay["audit_event_id"] and not replay["changed"]
    await restore(db,target,expected_suspension_id=saved.suspension_id)
    assert await db.owner.fetchval("SELECT status FROM tenants WHERE id=$1",db.tenant_id)=="active"

@pytest.mark.parametrize("target",["tenant","partner"])
async def test_mixed_membership_restore_preserves_removed_pending_independent_rows(account_db,target):
    db=account_db
    states={}
    for status in ("active","removed","pending","suspended"):
        user=await db.new_user(profile_role="user")
        await db.owner.execute("UPDATE tenant_users SET status=$2 WHERE user_id=$1",user,status)
        states[user]=status
    saved=await hold(db,target)
    await restore(db,target,expected_suspension_id=saved.suspension_id)
    for user,status in states.items():
        assert await db.owner.fetchval("SELECT status FROM tenant_users WHERE user_id=$1",user)==status


async def test_propagation_read_uses_real_record_and_never_fabricates_confirmation(account_db):
    db=account_db
    with pytest.raises(HTTPException) as missing:
        await db.service.get_propagation_status(uuid4())
    assert missing.value.status_code==404
    saved=await hold(db)
    status=await db.service.get_propagation_status(saved.suspension_id)
    assert status["hold_active"] is True and status["audit_event_id"]==saved.audit_event_id
    assert status["propagation_status"]=="unverified"
    assert "assistant_runtimes" not in status["services_confirmed"]
    assert next(d for d in status["dimensions"] if d["key"]=="assistant_runtimes")["coverage"]=="unknown"
    await db.owner.execute(f'REVOKE SELECT ON suspension_events FROM "{db.role}"')
    with pytest.raises(HTTPException) as failed:
        await db.service.get_propagation_status(saved.suspension_id)
    assert failed.value.status_code==503


@pytest.mark.parametrize("state",["inactive","unverified","demoted"])
async def test_user_appeal_denial_rechecks_current_actor(account_db,state):
    db=account_db
    saved=await db.service.suspend_user(db.target,SuspensionType.ADMIN,"synthetic","User hold",
        suspended_by=db.actor,notify_user=False)
    change={"inactive":"is_active=FALSE","unverified":"is_verified=FALSE,email_verified_at=NULL","demoted":"role='user'"}[state]
    await db.owner.execute(f"UPDATE user_profiles SET {change} WHERE id=$1",db.actor)
    with pytest.raises(HTTPException) as denied:
        await db.service.review_appeal(saved.suspension_id,db.actor,"denied","Reviewed")
    assert denied.value.status_code==403
    assert await db.owner.fetchval("SELECT appeal_decision FROM suspension_events WHERE suspension_id=$1",saved.suspension_id) is None


async def test_user_appeal_denial_audit_failure_rolls_back_decision(account_db):
    db=account_db
    saved=await db.service.suspend_user(db.target,SuspensionType.ADMIN,"synthetic","User hold",
        suspended_by=db.actor,notify_user=False)
    await db.owner.execute(f'REVOKE INSERT ON audit_logs FROM "{db.role}"')
    with pytest.raises(Exception):
        await db.service.review_appeal(saved.suspension_id,db.actor,"denied","Reviewed")
    assert await db.owner.fetchval("SELECT appeal_decision FROM suspension_events WHERE suspension_id=$1",saved.suspension_id) is None


async def test_user_appeal_denial_returns_one_committed_decision_receipt(account_db):
    db=account_db
    saved=await db.service.suspend_user(db.target,SuspensionType.ADMIN,"synthetic","User hold",
        suspended_by=db.actor,notify_user=False)
    a=await db.service.review_appeal(saved.suspension_id,db.actor,"denied","Reviewed")
    b=await db.service.review_appeal(saved.suspension_id,db.actor,"denied","Reviewed")
    assert a["audit_event_id"]==b["audit_event_id"] and not b["changed"]
    audit=await db.owner.fetchrow("SELECT action,metadata FROM audit_logs WHERE event_id=$1",a["audit_event_id"])
    assert audit["action"]=="appeal_reviewed" and json.loads(audit["metadata"])["decision"]=="denied"





