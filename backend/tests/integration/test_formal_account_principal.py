"""Current formal parent authority through real auth and restricted PostgreSQL."""
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from app.core.security.principal import load_current_principal, PrincipalUnavailable
from tests.integration.test_cp08_current_identity import identity_db, auth, _refresh  # noqa: F401

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def parent_db(identity_db):  # noqa: F811
    db = identity_db
    db.partner = uuid4()
    await db.admin.execute(
        "INSERT INTO white_label_partners(id,tenant_id,company_name) VALUES($1,$2,'Synthetic principal parent')",
        db.partner, db.tenants[1],
    )
    try:
        yield db
    finally:
        await db.admin.execute("UPDATE tenants SET white_label_partner_id=NULL WHERE id=ANY($1::uuid[])", db.tenants)
        await db.admin.execute("DELETE FROM white_label_partners WHERE id=$1", db.partner)


async def link_held_parent(db):
    await db.admin.execute("UPDATE white_label_partners SET status='suspended' WHERE id=$1", db.partner)
    await db.admin.execute("UPDATE tenants SET white_label_partner_id=$2 WHERE id=$1", db.tenants[0], db.partner)


@pytest.mark.parametrize("transport", ["cookie", "bearer"])
async def test_relink_to_held_parent_blocks_existing_authenticated_requests(parent_db, transport):
    db = parent_db
    assert (await db.http.get("/identity", headers=auth(transport))).status_code == 200
    await link_held_parent(db)
    assert await db.admin.fetchval("SELECT status FROM tenants WHERE id=$1", db.tenants[0]) == "active"
    assert await db.admin.fetchval("SELECT status FROM tenant_users WHERE user_id=$1", db.users[0]) == "active"
    assert (await db.http.get("/identity", headers=auth(transport))).status_code == 401
    # The unrelated parent-owning tenant's member is not a child by implication.
    assert (await db.http.get("/identity", headers={"authorization": "Bearer 1"})).status_code == 200


async def test_relinked_parent_hold_denies_refresh_and_commits_family_revocation(parent_db):
    db = parent_db
    raw, family = await _refresh(db)
    await link_held_parent(db)
    rejected = await db.http.post("/auth/refresh", headers={"cookie": f"talky_rt={raw}"})
    assert rejected.status_code == 401
    assert not rejected.headers.get_list("set-cookie")
    assert await db.admin.fetchval(
        "SELECT bool_and(revoked_at IS NOT NULL) FROM refresh_tokens WHERE family_id=$1", family)


@pytest.mark.parametrize("held", [False, True])
async def test_parent_lookup_preserves_tenant_rls_for_following_connector_work(parent_db, held):
    db = parent_db
    await db.admin.execute("UPDATE tenants SET white_label_partner_id=$2 WHERE id=$1", db.tenants[0], db.partner)
    if held:
        await db.admin.execute("UPDATE white_label_partners SET status='suspended' WHERE id=$1", db.partner)
    async with acquire_with_tenant(db.pool, str(db.tenants[0])) as conn:
        before = await conn.fetchval("SELECT current_setting('app.bypass_rls',TRUE)")
        if held:
            with pytest.raises(PrincipalUnavailable, match="account_restricted"):
                await load_current_principal(conn, db.users[0])
        else:
            assert (await load_current_principal(conn, db.users[0]))["tenant_id"] == str(db.tenants[0])
        assert (await conn.fetchval("SELECT current_setting('app.bypass_rls',TRUE)") or "") == (before or "")
        assert await conn.fetchval("SELECT count(*) FROM user_profiles WHERE id=$1", db.users[1]) == 0
        assert await conn.fetchval("SELECT count(*) FROM white_label_partners WHERE id=$1", db.partner) == 0


async def test_failed_parent_read_rolls_back_temporary_rls_scope(parent_db):
    db = parent_db
    await db.admin.execute(f'REVOKE SELECT ON white_label_partners FROM "{db.role}"')
    async with acquire_with_tenant(db.pool, str(db.tenants[0])) as conn:
        before = await conn.fetchval("SELECT current_setting('app.bypass_rls',TRUE)")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await load_current_principal(conn, db.users[0])
        assert (await conn.fetchval("SELECT current_setting('app.bypass_rls',TRUE)") or "") == (before or "")
        assert await conn.fetchval("SELECT count(*) FROM user_profiles WHERE id=$1", db.users[1]) == 0


async def test_billing_restriction_keeps_authentication_available(parent_db):
    db = parent_db
    await db.admin.execute("UPDATE tenants SET subscription_status='suspended' WHERE id=$1", db.tenants[0])
    assert (await db.http.get("/identity", headers=auth())).status_code == 200


async def test_global_platform_admin_does_not_lose_recovery_access_to_a_held_home_tenant(parent_db):
    db = parent_db
    await db.admin.execute("UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[0])
    await link_held_parent(db)
    result = await db.http.get("/identity", headers=auth())
    assert result.status_code == 200
    assert result.json()["role"] == "platform_admin"
