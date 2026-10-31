"""Normal-auth cancellation authority after an actual owned PostgreSQL row wait.

The configured app, current-principal dependencies, sessions, RLS and SQL are real.
Only fixture-owned records are seeded; no provider/executor is invoked and no
authentication or query result is replaced. Tenant movement is explicit fixture
setup, not acceptance of a general tenant-transfer API.
"""
import asyncio
import hashlib
import json
import re
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.core.jwt_security import decode_and_validate_token
from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (
    call,
    crud_db,  # noqa: F401
    reviewed_write,
    signed_in,
)

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


@pytest_asyncio.fixture
async def cancel_authority_db(crud_db):  # noqa: F811
    db = crud_db
    db.cancel_action_ids = []
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    # No execution permission or provider configuration is introduced.
    await db.owner.execute(f'GRANT SELECT,UPDATE ON assistant_actions TO "{role}"')
    await db.owner.execute(f'GRANT SELECT ON dialer_jobs TO "{role}"')
    try:
        yield db
    finally:
        # These rows reference only inherited owned users/tenants. Remove them
        # before the parent removes those identities and verifies protected data.
        await db.owner.execute(
            "DELETE FROM assistant_actions WHERE id=ANY($1::uuid[]) AND tenant_id=ANY($2::uuid[])",
            db.cancel_action_ids, db.tenants,
        )


async def set_owned_role(db, index, role):
    """Setup before login; canonical profile and membership agree for tenant roles."""
    assert role in {"platform_admin", "tenant_admin", "partner_admin", "user"}
    async with db.owner.transaction():
        assert await db.owner.execute(
            "UPDATE user_profiles SET role=$2 WHERE id=$1", db.users[index], role,
        ) == "UPDATE 1"
        if role != "platform_admin":
            assert await db.owner.execute(
                """UPDATE tenant_users SET role_id=(SELECT id FROM roles WHERE name=$3)
                   WHERE user_id=$1 AND tenant_id=$2 AND status='active' AND is_primary=TRUE""",
                db.users[index], db.tenants[index], role,
            ) == "UPDATE 1"


async def seed_pending_claim(db, tenant_index=1, user_index=1):
    identity = uuid4()
    parameters = {"to": "synthetic-action-authority@example.com"}
    digest = hashlib.sha256(
        json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    db.cancel_action_ids.append(identity)
    await db.owner.execute(
        """INSERT INTO assistant_actions
           (id,tenant_id,user_id,type,status,triggered_by,idempotency_key,input_data,output_data)
           VALUES($1,$2,$3,'send_email','pending','assistant',$4,$5::jsonb,'{}'::jsonb)""",
        identity, db.tenants[tenant_index], db.users[user_index],
        "synthetic-cancel-authority-" + str(identity),
        json.dumps({"request_hash": digest, "parameters": parameters}),
    )
    return identity


async def saved_action(db, identity):
    row = await db.owner.fetchrow("SELECT * FROM assistant_actions WHERE id=$1", identity)
    assert row is not None
    result = dict(row)
    for key in ("input_data", "output_data"):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return json.loads(json.dumps(result, default=str))


async def waiting_cancel_backend(db, blocker_pid, task):
    """Observe the actual route SQL blocked by this test's exact row owner."""
    async with asyncio.timeout(2):
        while True:
            # This observer holds a transaction; do not reuse its first empty
            # pg_stat_activity snapshot while the request starts waiting.
            await db.owner.execute("SELECT pg_stat_clear_snapshot()")
            pid = await db.owner.fetchval(
                """SELECT a.pid FROM pg_stat_activity a
                   WHERE a.datname=current_database() AND a.pid<>pg_backend_pid()
                     AND a.wait_event_type='Lock'
                     AND $1=ANY(pg_blocking_pids(a.pid))
                     AND position('assistant_actions' IN a.query)>0
                     AND position('FOR UPDATE' IN a.query)>0
                   ORDER BY a.pid LIMIT 1""", blocker_pid,
            )
            if pid is not None:
                return pid
            assert not task.done(), "Cancellation ended before the actual action-row wait"
            await asyncio.sleep(0.01)


async def move_owned_primary(db):
    """Commit a canonical tenant change while preserving both exact owned tenants."""
    # The restricted fixture already has the required owned membership grants.
    # No live/production account, auth principal or route response is replaced.
    async with acquire_with_tenant(db.pool, None) as conn:
        assert await conn.execute(
            "UPDATE tenant_users SET is_primary=FALSE WHERE user_id=$1 AND tenant_id=$2 AND is_primary=TRUE",
            db.users[1], db.tenants[1],
        ) == "UPDATE 1"
        await conn.execute(
            """INSERT INTO tenant_users(user_id,tenant_id,role_id,status,is_primary)
               SELECT $1,$2,id,'active',TRUE FROM roles WHERE name='tenant_admin'""",
            db.users[1], db.tenants[2],
        )
        assert await conn.execute(
            "UPDATE user_profiles SET tenant_id=$2 WHERE id=$1 AND tenant_id=$3",
            db.users[1], db.tenants[2], db.tenants[1],
        ) == "UPDATE 1"


@pytest.mark.parametrize("change,expected", [
    ("demote", 403), ("block", 401), ("session_logout", 401), ("tenant_shift", 401),
    ("narrow_to_own_tenant", 403),
])
async def test_cancel_rechecks_current_actor_after_real_action_row_wait(cancel_authority_db, change, expected, monkeypatch):
    db = cancel_authority_db
    # A remains the independent MFA administrator, preserving the last-admin
    # invariant. B authenticates normally with the canonical setup role.
    if change != "tenant_shift":
        await set_owned_role(db, 1, "platform_admin")
    identity = await seed_pending_claim(db, 2 if change == "narrow_to_own_tenant" else 1)
    before = await saved_action(db, identity)
    query_entered = asyncio.Event()
    original_fetchrow = asyncpg.Connection.fetchrow

    async def observe_action_lock(conn, query, *args, **kwargs):
        if ("assistant_actions" in query and "FOR UPDATE" in query
                and args and str(args[0]) == str(identity)):
            query_entered.set()
        return await original_fetchrow(conn, query, *args, **kwargs)

    # Forward the exact SQL unchanged. Starting the short stats observation
    # after query entry excludes normal HTTP/auth startup from that deadline;
    # the actual blocked backend is still required before changing authority.
    monkeypatch.setattr(asyncpg.Connection, "fetchrow", observe_action_lock)
    pending = None
    observation = {"case": "authority_changed_during_action_row_wait", "change": change,
                   "provider_or_executor_invoked": False, "expected_http_status": expected}
    RUN_EVIDENCE.append(observation)
    db.report.setdefault("action_cancel_authority", []).append(observation)
    async with signed_in(db, 0) as administrator, signed_in(db, 1) as actor:
        # talky_sid is an opaque cookie, not the database session UUID. Use the
        # actual validated access token's sid without recording its credentials.
        claims = decode_and_validate_token(actor.headers["Authorization"].split()[1])
        assert claims["sub"] == str(db.users[1])
        actor_session = UUID(claims["sid"])
        try:
            async with db.owner.transaction():
                await db.owner.fetchrow("SELECT id FROM assistant_actions WHERE id=$1 FOR UPDATE", identity)
                blocker_pid = db.owner.get_server_pid()
                # The owner's own row-lock read above is not the request.
                query_entered.clear()
                pending = asyncio.create_task(call(db, actor, "POST", f"/admin/actions/{identity}/cancel"))
                await asyncio.wait_for(query_entered.wait(), 8)
                observation["waiter_pid"] = await waiting_cancel_backend(db, blocker_pid, pending)
                observation["actual_row_lock_wait_observed"] = True
                if change in {"demote", "block", "narrow_to_own_tenant"}:
                    replacement_role = "tenant_admin" if change == "narrow_to_own_tenant" else "readonly"
                    response = await asyncio.wait_for(reviewed_write(
                        db, administrator, "PATCH", db.users[1],
                        **({"is_active": False} if change == "block" else {"role": replacement_role}),
                    ), 3)
                    assert response.status_code == 200, response.text
                    receipt = response.json()
                    observation["authority_change_http_status"] = response.status_code
                    observation["authority_change_audit_id"] = receipt["audit_event_id"]
                    assert await db.owner.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM audit_logs WHERE event_id=$1 AND actor_id=$2)",
                        UUID(receipt["audit_event_id"]), db.users[0],
                    )
                    if change == "block":
                        assert await db.owner.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", db.users[1]) is False
                    else:
                        assert await db.owner.fetchval(
                            """SELECT r.name FROM tenant_users tu JOIN roles r ON r.id=tu.role_id
                               WHERE tu.user_id=$1 AND tu.tenant_id=$2""", db.users[1], db.tenants[1],
                        ) == replacement_role
                    if change == "narrow_to_own_tenant":
                        current = await call(db, actor, "GET", "/auth/me")
                        assert current.status_code == 200
                        assert current.json()["role"] == "tenant_admin"
                        assert current.json()["tenant_id"] == str(db.tenants[1])
                        observation["still_authorized_in_original_tenant"] = True
                elif change == "session_logout":
                    response = await asyncio.wait_for(call(db, actor, "POST", "/auth/logout"), 3)
                    assert response.status_code == 200, response.text
                    assert await db.owner.fetchval(
                        "SELECT revoked FROM security_sessions WHERE id=$1 AND user_id=$2", actor_session, db.users[1],
                    ) is True
                    observation["authority_change_http_status"] = response.status_code
                else:
                    await asyncio.wait_for(move_owned_primary(db), 3)
                    assert await db.owner.fetchval("SELECT tenant_id FROM user_profiles WHERE id=$1", db.users[1]) == db.tenants[2]
                    observation["authority_change_setup"] = "Exact owned SQL profile/membership move; no transfer API claim"
                observation["authority_change_committed_before_row_release"] = True
            response = await asyncio.wait_for(pending, 6)
            after = await saved_action(db, identity)
            observation.update(response_http_status=response.status_code, before=before, after=after,
                               action_unchanged=before == after)
            # Store actual committed baseline state before the intended final
            # assertions: old code admits 200 and changes this exact action.
            assert response.status_code == expected, response.text
            assert after == before, "Denied current authority must not change any saved action field"
        finally:
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("role,index,tenant_index", [
    ("platform_admin", 0, 2), ("tenant_admin", 1, 1), ("partner_admin", 1, 1),
])
async def test_current_supported_admin_roles_can_cancel_owned_claim(cancel_authority_db, role, index, tenant_index):
    db = cancel_authority_db
    if role == "partner_admin":
        await set_owned_role(db, index, role)
    identity = await seed_pending_claim(db, tenant_index, index)
    async with signed_in(db, index) as actor:
        me = await call(db, actor, "GET", "/auth/me")
        assert me.status_code == 200 and me.json()["role"] == role
        response = await call(db, actor, "POST", f"/admin/actions/{identity}/cancel")
        after = await saved_action(db, identity)
        RUN_EVIDENCE.append({"case": "supported_role", "role": role, "http_status": response.status_code,
                             "saved_status": after["status"], "original_tenant_preserved": after["tenant_id"] == str(db.tenants[tenant_index])})
        assert response.status_code == 200, response.text
        assert response.json()["action_id"] == str(identity) and response.json()["new_status"] == "cancelled"
        assert after["status"] == "cancelled" and after["outcome_status"] == "cancelled_by_admin"
        assert after["output_data"]["cancelled_by_user_id"] == str(db.users[index])
        assert after["tenant_id"] == str(db.tenants[tenant_index])


@pytest.mark.parametrize("ordinary,expected", [(True, 403), (False, 404)], ids=["ordinary", "foreign-tenant"])
async def test_ordinary_and_foreign_tenant_denials_preserve_exact_action(cancel_authority_db, ordinary, expected):
    db = cancel_authority_db
    if ordinary:
        await set_owned_role(db, 1, "user")
    identity = await seed_pending_claim(db, 1 if ordinary else 2)
    before = await saved_action(db, identity)
    async with signed_in(db, 1) as actor:
        response = await call(db, actor, "POST", f"/admin/actions/{identity}/cancel")
        after = await saved_action(db, identity)
        RUN_EVIDENCE.append({"case": "ordinary" if ordinary else "foreign_tenant",
                             "http_status": response.status_code, "action_unchanged": after == before})
        assert response.status_code == expected, response.text
        assert after == before
