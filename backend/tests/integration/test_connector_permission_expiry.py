"""Real HTTP proof that temporary connector grants expire during row waits.

Reuse the frozen normal-auth fixture, restricted SQL role, encryption and
synthetic provider boundary. No authentication, principal, SQL result or clock
is substituted. The owned grant expires naturally and is never rewritten.
"""
import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio

from tests.integration.test_admin_connector_session_authority import (
    assert_saved_success,
    connector_session_db,  # noqa: F401
    digest,
    observe_wait_entry,
    prove_wait,
    receipts,
    seed_connector,
    state,
    write,
)
from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (
    call,
    crud_db,  # noqa: F401
    signed_in,
)

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []
WRITERS = (
    ("tenant_refresh", "connectors:update"),
    ("delete_connector", "connectors:delete"),
)


@pytest_asyncio.fixture
async def permission_expiry_db(connector_session_db):  # noqa: F811
    db = connector_session_db
    db.expiry_grant_ids = []
    try:
        # Change only the existing fixture's owned identity. The real role and
        # permission catalogue remains untouched, including its read grants.
        assert await db.owner.execute("""UPDATE user_profiles SET role='readonly'
            WHERE id=$1 AND tenant_id=$2""", db.users[1], db.tenants[1]) == "UPDATE 1"
        assert await db.owner.execute("""UPDATE tenant_users
            SET role_id=(SELECT id FROM roles WHERE name='readonly')
            WHERE user_id=$1 AND tenant_id=$2 AND status='active' AND is_primary=TRUE""",
            db.users[1], db.tenants[1]) == "UPDATE 1"
        inherited = await db.owner.fetch("""SELECT DISTINCT p.name FROM tenant_users tu
            JOIN roles r ON r.id=tu.role_id
            JOIN role_permissions rp ON rp.role_id=r.id
            JOIN permissions p ON p.id=rp.permission_id
            WHERE tu.user_id=$1 AND tu.tenant_id=$2 AND tu.status='active'""",
            db.users[1], db.tenants[1])
        inherited_names = {row["name"] for row in inherited}
        assert not {"connectors:update", "connectors:delete", "platform:admin"} & inherited_names
        assert not await db.owner.fetchval(
            "SELECT EXISTS(SELECT 1 FROM user_permissions WHERE user_id=$1)", db.users[1])
        yield db
    finally:
        # Remove exact owned grants before connector and inherited protected
        # state cleanup; never depend on a broad catalogue or permission reset.
        await db.owner.execute("""DELETE FROM user_permissions
            WHERE id=ANY($1::uuid[]) AND user_id=$2 AND tenant_id=$3""",
            db.expiry_grant_ids, db.users[1], db.tenants[1])


async def seed_grant(db, permission, ttl_seconds):
    identity = uuid4()
    db.expiry_grant_ids.append(identity)
    row = await db.owner.fetchrow("""INSERT INTO user_permissions
        (id,user_id,tenant_id,permission_id,expires_at,reason,granted_by)
        SELECT $1,$2,$3,id,clock_timestamp()+($5::double precision * INTERVAL '1 second'),
               'Synthetic connector expiry regression',$6
        FROM permissions WHERE name=$4 RETURNING *""",
        identity, db.users[1], db.tenants[1], permission, ttl_seconds, db.users[0])
    assert row is not None
    return dict(row)


async def saved_grant(db, identity):
    row = await db.owner.fetchrow("""SELECT * FROM user_permissions
        WHERE id=$1 AND user_id=$2 AND tenant_id=$3""",
        identity, db.users[1], db.tenants[1])
    assert row is not None
    return dict(row)


async def prove_readonly_login(db, actor):
    me = await call(db, actor, "GET", "/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "readonly"
    permissions = await call(db, actor, "GET", "/rbac/users/me/permissions")
    assert permissions.status_code == 200
    assert not {"connectors:update", "connectors:delete"} & set(permissions.json()["permissions"])


async def wait_for_natural_expiry(db, pending, expires_at):
    # PostgreSQL supplies the wall clock. An event/sleep is only scheduling,
    # never evidence of expiry and never a replacement for the actual wait.
    async with asyncio.timeout(6):
        while True:
            assert not pending.done(), "Connector stopped waiting before grant expiry"
            observed_at = await db.owner.fetchval("SELECT clock_timestamp()")
            if observed_at >= expires_at:
                return observed_at
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("writer,permission", WRITERS)
async def test_expired_direct_grant_denies_connector_after_real_row_wait(permission_expiry_db, monkeypatch, writer, permission):
    db = permission_expiry_db
    connector_id = await seed_connector(db, writer)
    before, before_audits = await state(db, connector_id), await receipts(db, connector_id)
    entered, entry = observe_wait_entry(db, monkeypatch, writer, connector_id, "parent")
    evidence = {"case": "direct_grant_expires_during_connector_wait", "writer": writer,
                "permission": permission, "expected_status": 403, "before_sha256": digest(before),
                "inherited_write_permissions": False, "auth_overridden": False}
    RUN_EVIDENCE.append(evidence)
    pending = None
    async with signed_in(db, 1) as actor:
        await prove_readonly_login(db, actor)
        # Commit a short, real grant only after authentication has completed.
        # No grant mutation occurs after this seed, even while the writer waits.
        grant_before = await seed_grant(db, permission, 4)
        expires_at = grant_before["expires_at"]
        evidence.update(grant_id=str(grant_before["id"]), expires_at=expires_at.isoformat(),
                        grant_before_sha256=digest(grant_before))
        try:
            async with db.owner.transaction():
                locked = await db.owner.fetchval("""SELECT id FROM connectors
                    WHERE id=$1 AND tenant_id=$2 FOR UPDATE""", connector_id, db.tenants[1])
                assert locked == connector_id
                pending = asyncio.create_task(write(db, actor, writer, connector_id))
                try:
                    await asyncio.wait_for(entered.wait(), 8)
                except TimeoutError:
                    if pending.done() and not pending.cancelled() and pending.exception() is None:
                        evidence["early_http_status"] = pending.result().status_code
                    raise
                await prove_wait(db, pending, entry["waiter_pid"], "parent")
                await db.owner.execute("SELECT pg_stat_clear_snapshot()")
                timing = await db.owner.fetchrow("""SELECT xact_start,clock_timestamp() AS observed_at
                    FROM pg_stat_activity WHERE pid=$1 AND wait_event_type='Lock'
                      AND $2=ANY(pg_blocking_pids(pid))""", entry["waiter_pid"], db.owner.get_server_pid())
                assert timing is not None
                # Failing this precondition is a timing/fixture failure, not
                # proof that a request admitted after expiry was mishandled.
                assert timing["xact_start"] <= timing["observed_at"] < expires_at
                evidence.update(actual_lock_wait_observed=True, waiter_pid=entry["waiter_pid"],
                                transaction_started_at=timing["xact_start"].isoformat(),
                                wait_observed_before_expiry_at=timing["observed_at"].isoformat())
                expired_at = await wait_for_natural_expiry(db, pending, expires_at)
                await prove_wait(db, pending, entry["waiter_pid"], "parent")
                assert timing["xact_start"] < expires_at <= expired_at
                evidence.update(expiry_observed_at=expired_at.isoformat(),
                                actual_lock_wait_after_expiry_observed=True)
            response = await asyncio.wait_for(pending, 8)
            # Record both controls before the expected baseline assertion: the
            # same authenticated session lives, but a fresh mutation is denied.
            me = await call(db, actor, "GET", "/auth/me")
            fresh = await write(db, actor, writer, connector_id)
            after, after_audits = await state(db, connector_id), await receipts(db, connector_id)
            grant_after = await saved_grant(db, grant_before["id"])
            same_state, same_audits, same_grant = after == before, after_audits == before_audits, grant_after == grant_before
            evidence.update(http_status=response.status_code, same_session_identity_status=me.status_code,
                            fresh_mutation_status=fresh.status_code, after_sha256=digest(after),
                            state_unchanged=same_state, mutation_audits_unchanged=same_audits,
                            grant_unchanged=same_grant, grant_after_sha256=digest(grant_after),
                            provider_entries=len(db.provider_calls))
            assert me.status_code == 200 and fresh.status_code == 403
            assert same_grant
            assert response.status_code == 403
            assert same_state and same_audits and not db.provider_calls
        finally:
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.parametrize("writer,permission", WRITERS)
async def test_unexpired_direct_grant_allows_readonly_connector_writer(permission_expiry_db, writer, permission):
    db = permission_expiry_db
    connector_id = await seed_connector(db, writer)
    evidence = {"case": "unexpired_direct_connector_grant", "writer": writer,
                "permission": permission, "inherited_write_permissions": False, "expected_status": 200}
    RUN_EVIDENCE.append(evidence)
    async with signed_in(db, 1) as actor:
        await prove_readonly_login(db, actor)
        grant_before = await seed_grant(db, permission, 3600)
        response = await write(db, actor, writer, connector_id)
        evidence["http_status"] = response.status_code
        evidence.update(await assert_saved_success(db, writer, connector_id, response, 1))
        grant_after = await saved_grant(db, grant_before["id"])
        same_grant = grant_after == grant_before
        observed_at = await db.owner.fetchval("SELECT clock_timestamp()")
        evidence.update(grant_id=str(grant_before["id"]), grant_unchanged=same_grant,
                        grant_before_sha256=digest(grant_before), grant_after_sha256=digest(grant_after),
                        expires_at=grant_after["expires_at"].isoformat(), observed_at=observed_at.isoformat())
        assert same_grant and observed_at < grant_after["expires_at"]
