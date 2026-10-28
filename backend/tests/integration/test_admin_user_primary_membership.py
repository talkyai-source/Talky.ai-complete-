"""Owned normal-auth initial primary-membership setup, never tenant transfer."""
import asyncio
import json
from uuid import UUID, uuid4

import pytest

from tests.integration.test_admin_users_tenants_crud import (
    crud_db, signed_in, call, prepare_write, cancel_prepared,
)
from tests.integration.test_admin_real_auth_journey import journey_db

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


async def prepare_missing(db, *, tenantless):
    await db.owner.execute("DELETE FROM tenant_users WHERE user_id=$1", db.users[1])
    await db.owner.execute("UPDATE user_profiles SET tenant_id=$2 WHERE id=$1",
                           db.users[1], None if tenantless else db.tenants[1])


async def candidate(db, admin, tenant=None):
    return await call(db, admin, "GET", f"/admin/users/{db.users[1]}/primary-membership",
                      params={"tenant_id": str(tenant or db.tenants[1])})


async def setup_body(db, admin, review, **overrides):
    body = {"tenant_id": str(db.tenants[1]), "role": "user",
            "expected_review_token": review["review_token"], **overrides}
    return await prepare_write(db, admin, "POST", f"/admin/users/{db.users[1]}/primary-membership", body)


async def configure(db, admin, review, *, prepared_body=None, **overrides):
    body = prepared_body if prepared_body is not None else await setup_body(db, admin, review, **overrides)
    return await call(db, admin, "POST", f"/admin/users/{db.users[1]}/primary-membership", json=body)


@pytest.mark.parametrize("tenantless", [True, False])
async def test_initial_setup_is_one_reviewed_audited_mutation(crud_db, tenantless):  # noqa: F811
    db = crud_db
    await prepare_missing(db, tenantless=tenantless)
    async with signed_in(db) as admin:
        original = await call(db, admin, "GET", f"/admin/users/{db.users[1]}")
        legacy_body = await prepare_write(db, admin, "PATCH", f"/admin/users/{db.users[1]}", {
            "role": "user", "expected_review_token": original.json()["review_token"]})
        legacy = await call(db, admin, "PATCH", f"/admin/users/{db.users[1]}", json=legacy_body)
        assert legacy.status_code == 409 and legacy.json()["error"]["code"] == "user_membership_required"
        await cancel_prepared(db, admin, legacy_body)
        reviewed = await candidate(db, admin)
        RUN_EVIDENCE.append({"case": "initial_primary_setup", "tenantless": tenantless,
                             "ordinary_role_patch": legacy.status_code, "candidate_status": reviewed.status_code})
        assert reviewed.status_code == 200 and reviewed.json()["eligible"] is True
        response = await configure(db, admin, reviewed.json())
        assert response.status_code == 200
        data = response.json()
        assert data["operation"] == "primary_membership_configured"
        assert data["user"]["id"] == str(db.users[1])
        assert data["user"]["tenant_id"] == str(db.tenants[1]) and data["user"]["effective_role"] == "user"
        assert data["membership"]["status"] == "active" and data["membership"]["is_primary"] is True
        audit = await db.owner.fetchrow("SELECT action,resource_id FROM audit_logs WHERE event_id=$1", UUID(data["audit_event_id"]))
        assert audit and audit["action"] == "user_primary_membership_configured" and audit["resource_id"] == db.users[1]
    async with signed_in(db, 1) as member:
        me = await call(db, member, "GET", "/auth/me")
        assert me.json()["tenant_id"] == str(db.tenants[1]) and me.json()["role"] == "user"


async def test_null_profile_reuses_only_exact_reviewed_active_membership(crud_db):  # noqa: F811
    db = crud_db
    existing = await db.owner.fetchval("SELECT id FROM tenant_users WHERE user_id=$1",db.users[1])
    await db.owner.execute("UPDATE user_profiles SET tenant_id=NULL,role='platform_admin' WHERE id=$1",db.users[1])
    async with signed_in(db) as admin:
        reviewed = (await candidate(db,admin)).json()
        assert reviewed["eligible"] and reviewed["membership"]["id"] == str(existing)
        response = await configure(db,admin,reviewed)
        assert response.status_code == 200 and response.json()["membership"]["id"] == str(existing)
        assert response.json()["user"]["role"] == "user"
        assert await db.owner.fetchval("SELECT count(*) FROM tenant_users WHERE user_id=$1",db.users[1]) == 1


@pytest.mark.parametrize("condition",["transfer","inactive","other_primary","blocked","user_hold","tenant_hold","existing_complete"])
async def test_setup_rejects_unsupported_or_restricted_state_without_effect(crud_db,condition):  # noqa: F811
    db=crud_db
    if condition not in {"transfer","existing_complete"}:
        await db.owner.execute("UPDATE user_profiles SET tenant_id=NULL WHERE id=$1",db.users[1])
    if condition == "inactive":
        await db.owner.execute("UPDATE tenant_users SET status='suspended' WHERE user_id=$1",db.users[1])
    elif condition == "other_primary":
        await db.owner.execute("UPDATE tenant_users SET tenant_id=$2 WHERE user_id=$1",db.users[1],db.tenants[2])
    elif condition == "blocked":
        await db.owner.execute("UPDATE user_profiles SET is_active=FALSE WHERE id=$1",db.users[1])
    elif condition in {"user_hold","tenant_hold"}:
        target_type="user" if condition == "user_hold" else "tenant"
        target=db.users[1] if condition == "user_hold" else db.tenants[1]
        await db.owner.execute("""INSERT INTO suspension_events(suspension_id,target_type,target_id,suspension_type,
            reason_category,reason_description,is_active) VALUES($1,$2,$3,'manual','synthetic','Synthetic owned hold',TRUE)""",uuid4(),target_type,target)
    dest=db.tenants[2] if condition == "transfer" else db.tenants[1]
    async with signed_in(db) as admin:
        reviewed=(await candidate(db,admin,dest)).json()
        assert reviewed["eligible"] is False and reviewed["reason"]
        response=await configure(db,admin,reviewed,tenant_id=str(dest))
        assert response.status_code==409 and response.json()["error"]["code"]=="primary_membership_ineligible"
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE action='user_primary_membership_configured' AND resource_id=$1",db.users[1])==0


async def test_self_and_ordinary_review_tokens_cannot_authorize_setup(crud_db):  # noqa: F811
    db=crud_db
    await prepare_missing(db,tenantless=True)
    async with signed_in(db) as admin:
        user=(await call(db,admin,"GET",f"/admin/users/{db.users[1]}")).json()
        bad=await call(db,admin,"POST",f"/admin/users/{db.users[1]}/primary-membership",json={
            "operation_id":str(uuid4()), "tenant_id":str(db.tenants[1]), "role":"user",
            "expected_review_token":user["review_token"]})
        assert bad.status_code==422
        own=await call(db,admin,"GET",f"/admin/users/{db.users[0]}/primary-membership",params={"tenant_id":str(db.tenants[0])})
        assert own.status_code==200 and not own.json()["eligible"]


@pytest.mark.parametrize("change",["candidate_aba","profile_aba","destination"])
async def test_candidate_review_rejects_changed_state(crud_db,change):  # noqa: F811
    db=crud_db
    await db.owner.execute("UPDATE user_profiles SET tenant_id=NULL WHERE id=$1",db.users[1])
    async with signed_in(db) as admin:
        reviewed=(await candidate(db,admin)).json()
        body=await setup_body(db,admin,reviewed)
        if change=="candidate_aba":
            async with db.owner.transaction():
                await db.owner.execute("UPDATE tenant_users SET status='suspended' WHERE user_id=$1",db.users[1])
                await db.owner.execute("UPDATE tenant_users SET status='active' WHERE user_id=$1",db.users[1])
        elif change=="profile_aba":
            async with db.owner.transaction():
                await db.owner.execute("UPDATE user_profiles SET name='Synthetic changed' WHERE id=$1",db.users[1])
                await db.owner.execute("UPDATE user_profiles SET name='Synthetic HTTP user' WHERE id=$1",db.users[1])
        else:
            await db.owner.execute("UPDATE tenants SET business_name='Synthetic changed destination',updated_at=clock_timestamp() WHERE id=$1",db.tenants[1])
        response=await configure(db,admin,reviewed,prepared_body=body)
        assert response.status_code==409 and response.json()["error"]["code"]=="primary_membership_changed"
        assert await db.owner.fetchval("SELECT tenant_id FROM user_profiles WHERE id=$1",db.users[1]) is None


@pytest.mark.parametrize("other_primary",[False,True])
async def test_concurrent_rbac_insert_is_not_adopted_or_deadlocked(crud_db,monkeypatch,other_primary):  # noqa: F811
    from app.api.v1.endpoints.admin import users
    from app.core.db_utils import acquire_with_tenant
    db=crud_db
    await prepare_missing(db,tenantless=other_primary)
    entered,release=asyncio.Event(),asyncio.Event()
    original=users._primary_membership_review
    async def paused(conn,user_id,tenant_id,actor_id,*,lock=False):
        result=await original(conn,user_id,tenant_id,actor_id,lock=lock)
        if lock:
            entered.set()
            await asyncio.wait_for(release.wait(),10)
        return result
    monkeypatch.setattr(users,"_primary_membership_review",paused)
    async with signed_in(db) as admin:
        reviewed=(await candidate(db,admin)).json()
        body=await setup_body(db,admin,reviewed)
        task=asyncio.create_task(configure(db,admin,reviewed,prepared_body=body))
        try:
            await asyncio.wait_for(entered.wait(),5)
            destination=db.tenants[2] if other_primary else db.tenants[1]
            if other_primary:
                # Existing tenant-scoped RBAC cannot see a NULL-primary target.
                # This separate case exercises the real restricted-role service
                # writer and primary uniqueness, not a fabricated RBAC success.
                async with acquire_with_tenant(db.pool,None) as conn:
                    await asyncio.wait_for(conn.execute("""INSERT INTO tenant_users(user_id,tenant_id,role_id,is_primary,status)
                        SELECT $1,$2,id,TRUE,'active' FROM roles WHERE name='tenant_admin'""",db.users[1],destination),5)
            else:
                inserted=await asyncio.wait_for(call(db,admin,"POST","/rbac/tenant-users",json={
                    "user_id":str(db.users[1]),"tenant_id":str(destination),"role_name":"tenant_admin","is_primary":True}),5)
                assert inserted.status_code==201
            release.set()
            response=await asyncio.wait_for(task,10)
            assert response.status_code==409 and response.json()["error"]["code"]=="primary_membership_changed"
            saved=await db.owner.fetchrow("SELECT tenant_id,r.name AS role FROM tenant_users tu JOIN roles r ON r.id=tu.role_id WHERE user_id=$1",db.users[1])
            assert saved["tenant_id"]==destination and saved["role"]=="tenant_admin"
            assert await db.owner.fetchval("SELECT tenant_id FROM user_profiles WHERE id=$1",db.users[1]) == (None if other_primary else db.tenants[1])
            RUN_EVIDENCE.append({"case":"membership_insert_race","writer":"restricted_service_sql" if other_primary else "normal_rbac_http",
                                 "other_primary":other_primary,"setup_status":409})
        finally:
            release.set()
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)


@pytest.mark.parametrize("audit_denied",[False,True])
async def test_credential_cutoff_and_audit_share_the_same_commit(crud_db,audit_denied):  # noqa: F811
    from app.core.security.passkeys import create_challenge
    from app.core.security.credential_issuance import require_current_passkey_ceremony
    from app.core.security.principal import PrincipalUnavailable
    db=crud_db
    async with signed_in(db,1) as old_session:
        await prepare_missing(db,tenantless=True)
        challenge,_=await create_challenge(db.owner,"authentication",str(db.users[1]))
        anonymous,_=await create_challenge(db.owner,"authentication")
        challenge_ids=[UUID(challenge),UUID(anonymous)]
        mfa_id=uuid4()
        await db.owner.execute("""INSERT INTO mfa_challenges(id,user_id,challenge_hash,expires_at)
            VALUES($1,$2,$3,clock_timestamp()+interval '5 minutes')""",mfa_id,db.users[1],str(uuid4()))
        try:
            async with signed_in(db) as admin:
                reviewed=(await candidate(db,admin)).json()
                body=await setup_body(db,admin,reviewed)
                if audit_denied:
                    async with db.pool.acquire() as conn: role=await conn.fetchval("SELECT current_user")
                    await db.owner.execute(f'REVOKE INSERT ON audit_logs FROM "{role}"')
                response=await configure(db,admin,reviewed,prepared_body=body)
                if audit_denied:
                    assert response.status_code==500
                    assert await db.owner.fetchval("SELECT tenant_id FROM user_profiles WHERE id=$1",db.users[1]) is None
                    assert await db.owner.fetchval("SELECT count(*) FROM tenant_users WHERE user_id=$1",db.users[1])==0
                    assert await db.owner.fetchval("SELECT count(*) FROM security_sessions WHERE user_id=$1 AND revoked=FALSE",db.users[1])==1
                    assert await db.owner.fetchval("SELECT used FROM webauthn_challenges WHERE id=$1",UUID(challenge)) is False
                    assert await db.owner.fetchval("SELECT used FROM mfa_challenges WHERE id=$1",mfa_id) is False
                    await require_current_passkey_ceremony(db.owner,db.users[1],anonymous)
                else:
                    assert response.status_code==200
                    counts=response.json()["credential_cutoff"]
                    assert counts["revoked_sessions"]==1 and counts["revoked_refresh_tokens"]>=1 and counts["invalidated_passkey_challenges"]==1
                    assert counts["invalidated_mfa_challenges"]==1
                    assert await db.owner.fetchval("SELECT count(*) FROM security_sessions WHERE user_id=$1 AND revoked=FALSE",db.users[1])==0
                    assert await db.owner.fetchval("SELECT count(*) FROM refresh_tokens WHERE user_id=$1 AND revoked_at IS NULL",db.users[1])==0
                    assert await db.owner.fetchval("SELECT used FROM webauthn_challenges WHERE id=$1",UUID(challenge)) is True
                    assert await db.owner.fetchval("SELECT used FROM mfa_challenges WHERE id=$1",mfa_id) is True
                    row=await db.owner.fetchrow("SELECT metadata FROM audit_logs WHERE event_id=$1",UUID(response.json()["audit_event_id"]))
                    metadata=json.loads(row["metadata"])
                    assert metadata["primary_membership_version"]==1 and metadata["credential_cutoff"]==counts
                    with pytest.raises(PrincipalUnavailable):
                        await require_current_passkey_ceremony(db.owner,db.users[1],anonymous)
                    await require_current_passkey_ceremony(db.owner,db.users[2],anonymous)
                    fresh,_=await create_challenge(db.owner,"authentication")
                    challenge_ids.append(UUID(fresh))
                    await require_current_passkey_ceremony(db.owner,db.users[1],fresh)
            if not audit_denied:
                assert (await call(db,old_session,"GET","/auth/me")).status_code==401
        finally:
            await db.owner.execute("DELETE FROM webauthn_challenges WHERE id=ANY($1::uuid[])",challenge_ids)


async def test_current_actor_lost_while_waiting_cannot_configure_membership(crud_db,monkeypatch):  # noqa: F811
    from app.api.v1.endpoints.admin import users
    db=crud_db
    await prepare_missing(db,tenantless=True)
    entered,release=asyncio.Event(),asyncio.Event()
    original=users.lock_user_administration
    async def paused(conn):
        await original(conn)
        entered.set()
        await asyncio.wait_for(release.wait(),10)
    async with signed_in(db) as admin:
        reviewed=(await candidate(db,admin)).json()
        body=await setup_body(db,admin,reviewed)
        monkeypatch.setattr(users,"lock_user_administration",paused)
        task=asyncio.create_task(configure(db,admin,reviewed,prepared_body=body))
        try:
            await asyncio.wait_for(entered.wait(),5)
            await db.owner.execute("UPDATE user_profiles SET role='readonly' WHERE id=$1",db.users[0])
            release.set()
            result=await asyncio.wait_for(task,10)
            assert result.status_code==403
            assert await db.owner.fetchval("SELECT count(*) FROM tenant_users WHERE user_id=$1",db.users[1])==0
        finally:
            release.set()
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)
