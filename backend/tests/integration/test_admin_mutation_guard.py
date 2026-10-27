"""Current Admin lookup restores tenant RLS scope and bounds authority contention.

Direct SQL/helper checks on the normal journey's owned restricted role. These
are not additional HTTP authentication cases and perform no external effects.
"""
import asyncio
import time

import pytest
from fastapi import HTTPException

from app.core.security import platform_admin_guard as guard
from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import crud_db  # noqa: F401

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


async def scoped(conn, tenant, bypass="off"):
    await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(tenant))
    await conn.execute("SELECT set_config('app.bypass_rls',$1,true)", bypass)


async def assert_scope(conn, db, *, bypass="off"):
    assert await conn.fetchval("SELECT current_setting('app.bypass_rls',true)") == bypass
    assert await conn.fetchval("SELECT current_setting('app.current_tenant_id',true)") == str(db.tenants[1])
    if bypass != "on":
        assert await conn.fetchval("SELECT id FROM user_profiles WHERE id=$1", db.users[0]) is None
    assert await conn.fetchval("SELECT id FROM user_profiles WHERE id=$1", db.users[1]) == db.users[1]


@pytest.mark.parametrize("tenantless,bypass", [(False, "off"), (True, "off"), (False, "on"), (False, "")])
async def test_exact_platform_lookup_restores_original_scope(crud_db, tenantless, bypass):
    db = crud_db
    if tenantless:
        await db.owner.execute("UPDATE user_profiles SET tenant_id=NULL WHERE id=$1", db.users[0])
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await scoped(conn, db.tenants[1], bypass)
            await guard.require_platform_admin_mutation(conn, db.users[0])
            await assert_scope(conn, db, bypass=bypass)
    RUN_EVIDENCE.append({"case": "scope_success", "tenantless": tenantless, "prior_bypass": bypass,
                         "original_scope_restored": True})


@pytest.mark.parametrize("condition", ["blocked", "unverified", "ordinary_member", "missing"])
async def test_current_actor_denial_restores_scope_without_business_write(crud_db, condition):
    db = crud_db
    actor = db.users[0]
    if condition == "blocked":
        await db.owner.execute("UPDATE user_profiles SET is_active=FALSE WHERE id=$1", actor)
    elif condition == "unverified":
        await db.owner.execute("UPDATE user_profiles SET is_verified=FALSE WHERE id=$1", actor)
    elif condition == "ordinary_member":
        actor = db.users[2]
    else:
        from uuid import uuid4
        actor = uuid4()
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await scoped(conn, db.tenants[1])
            with pytest.raises(HTTPException) as denied:
                await guard.require_platform_admin_mutation(conn, actor)
            assert denied.value.status_code == 403
            await assert_scope(conn, db)
    RUN_EVIDENCE.append({"case": "scope_denied", "condition": condition, "status": 403,
                         "original_scope_restored": True})


async def test_failed_authority_sql_rolls_back_read_savepoint_and_restores_scope(crud_db):
    db = crud_db
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    await db.owner.execute(f'REVOKE SELECT ON tenant_users FROM "{role}"')
    try:
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                await scoped(conn, db.tenants[1])
                with pytest.raises(HTTPException) as unavailable:
                    await guard.require_platform_admin_mutation(conn, db.users[0])
                assert unavailable.value.status_code == 503
                # A failed SQL statement must not poison the surrounding tenant
                # transaction or leave its temporary internal read bypass on.
                await assert_scope(conn, db)
    finally:
        await db.owner.execute(f'GRANT SELECT ON tenant_users TO "{role}"')
    RUN_EVIDENCE.append({"case": "scope_sql_fault", "status": 503, "transaction_usable": True,
                         "original_scope_restored": True})


async def test_waiting_on_authority_is_bounded_and_never_admits_action(crud_db):
    db = crud_db
    async with db.owner.transaction():
        await guard.lock_user_administration(db.owner)
        started = time.monotonic()
        async with db.pool.acquire() as conn:
            async with conn.transaction():
                await scoped(conn, db.tenants[1])
                with pytest.raises(HTTPException) as unavailable:
                    await asyncio.wait_for(guard.require_platform_admin_mutation(conn, db.users[0]), 6)
                assert await conn.fetchval("SELECT 1") == 1
                await assert_scope(conn, db)
        elapsed = time.monotonic() - started
        assert unavailable.value.status_code == 503
        assert 2.5 <= elapsed < 6
    # The cancelled waiter did not retain the lock or leak its transaction.
    async with db.pool.acquire() as conn:
        async with conn.transaction():
            await scoped(conn, db.tenants[1])
            await guard.require_platform_admin_mutation(conn, db.users[0])
            await assert_scope(conn, db)
    RUN_EVIDENCE.append({"case": "bounded_authority_wait", "status": 503,
                         "elapsed_seconds": round(elapsed, 3), "later_authorized_check_passes": True})


async def test_guard_refuses_autocommit_without_a_mutation_transaction(crud_db):
    db = crud_db
    async with db.pool.acquire() as conn:
        assert not conn.is_in_transaction()
        with pytest.raises(HTTPException) as error:
            await guard.require_platform_admin_mutation(conn, db.users[0])
        assert error.value.status_code == 503
        assert not conn.is_in_transaction()


@pytest.mark.parametrize("isolation", ["repeatable_read", "serializable"])
async def test_guard_never_admits_from_snapshot_predating_committed_revocation(crud_db, isolation):
    db = crud_db
    async with db.pool.acquire() as conn:
        async with conn.transaction(isolation=isolation):
            await scoped(conn, db.tenants[1], "on")
            assert await conn.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", db.users[0]) is True
            await db.owner.execute("UPDATE user_profiles SET is_active=FALSE WHERE id=$1", db.users[0])
            # This is the actual stale snapshot that an advisory lock alone
            # cannot repair; the mutation helper must refuse this context.
            assert await conn.fetchval("SELECT is_active FROM user_profiles WHERE id=$1", db.users[0]) is True
            await scoped(conn, db.tenants[1])
            with pytest.raises(HTTPException) as denied:
                await guard.require_platform_admin_mutation(conn, db.users[0])
            assert denied.value.status_code == 503
            assert await conn.fetchval("SELECT 1") == 1
            await assert_scope(conn, db)
    RUN_EVIDENCE.append({"case": "stale_snapshot_refused", "isolation": isolation, "status": 503,
                         "old_snapshot_independently_observed": True, "original_scope_restored": True})
