"""Restricted-role incident lifecycle checks on an explicitly disposable local DB.

Requires migrated TEST_ADMIN_DATABASE_URL ending in _test. No provider calls;
only this test's random incident IDs, role and optional fault trigger are changed.
"""
import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from fastapi import HTTPException
from app.api.v1.endpoints.admin.health import incidents

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def incident_db():
    dsn = os.getenv("TEST_ADMIN_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit migrated disposable TEST_ADMIN_DATABASE_URL required")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"127.0.0.1", "localhost"} and parsed.path.endswith("_test")
    owner = await asyncpg.connect(dsn)
    role = "ad02_" + uuid4().hex
    created, faults, principals, pool = [], [], [], None
    try:
        assert await owner.fetchval("SELECT to_regclass('public.incidents')") is not None, "Apply 0072_admin_incidents first"
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON incidents TO "{role}"')
        await owner.execute(f'GRANT SELECT ON user_profiles,tenants,tenant_users,roles TO "{role}"')
        for _ in range(2):
            principal_id = uuid4()
            principals.append(principal_id)
            await owner.execute("""INSERT INTO user_profiles(id,email,name,role,is_active,is_verified,email_verified_at)
                VALUES($1,$2,'Synthetic incident administrator','platform_admin',TRUE,TRUE,NOW())""",
                principal_id, f"{principal_id}@example.com")
        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute("SET statement_timeout='5s'")
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, setup=setup)
        async def new_incident(severity="warning"):
            incident_id = uuid4(); created.append(incident_id)
            await owner.execute("INSERT INTO incidents(id,title,severity) VALUES($1,$2,$3)", incident_id, "AD02 isolated fixture", severity)
            return incident_id
        async def fail_update(incident_id):
            name = "ad02_fault_" + uuid4().hex
            await owner.execute(f"""CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN IF NEW.id='{incident_id}'::uuid THEN RAISE EXCEPTION 'synthetic AD02 write fault'; END IF; RETURN NEW; END $$""")
            await owner.execute(f"CREATE TRIGGER {name} BEFORE UPDATE ON incidents FOR EACH ROW EXECUTE FUNCTION {name}()")
            faults.append(name)
        yield SimpleNamespace(owner=owner, pool=pool, client=SimpleNamespace(pool=pool), new_incident=new_incident,
                              fail_update=fail_update, admin=SimpleNamespace(id=principals[0]),
                              other_admin=SimpleNamespace(id=principals[1]))
    finally:
        if pool is not None:
            await pool.close()
        for name in faults:
            await owner.execute(f"DROP TRIGGER {name} ON incidents")
            await owner.execute(f"DROP FUNCTION {name}()")
        if created:
            await owner.execute("DELETE FROM incidents WHERE id=ANY($1::uuid[])", created)
        if principals:
            await owner.execute("DELETE FROM user_profiles WHERE id=ANY($1::uuid[])", principals)
        await owner.execute(f'DROP OWNED BY "{role}"')
        await owner.execute(f'DROP ROLE IF EXISTS "{role}"')
        await owner.close()


async def test_incident_rls_denies_tenant_scope_but_platform_handler_can_read(incident_db):
    db = incident_db; incident_id = await db.new_incident()
    async with db.pool.acquire() as conn:
        flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        assert tuple(flags.values()) == (False, False)
        async with conn.transaction():
            await conn.execute("SET LOCAL app.bypass_rls='off'")
            await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(uuid4()))
            assert await conn.fetchval("SELECT COUNT(*) FROM incidents WHERE id=$1", incident_id) == 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute("SET LOCAL app.bypass_rls='off'")
                await conn.execute("INSERT INTO incidents(title,severity) VALUES('Denied tenant write','warning')")
    result = await incidents.get_incident(incident_id, db.admin, db.client)
    assert result.id == str(incident_id)


async def test_incident_real_list_and_acknowledge_receipt_survive_replay(incident_db):
    db = incident_db; incident_id = await db.new_incident("critical")
    listed = await incidents.list_incidents(db.admin, db.client, 1, 100, "open", "critical")
    assert str(incident_id) in [item.id for item in listed.items]
    first = await incidents.acknowledge_incident(incident_id, db.admin, db.client)
    second = await incidents.acknowledge_incident(incident_id, db.other_admin, db.client)
    assert first == second
    saved = await db.owner.fetchrow("SELECT * FROM incidents WHERE id=$1", incident_id)
    assert saved["acknowledged_by"] == db.admin.id
    assert first["acknowledged_at"] == saved["acknowledged_at"].isoformat()


async def test_concurrent_acknowledge_and_resolve_cannot_reopen_incident(incident_db):
    db = incident_db; incident_id = await db.new_incident()
    outcomes = await asyncio.gather(incidents.acknowledge_incident(incident_id, db.admin, db.client),
        incidents.resolve_incident(incident_id, db.other_admin, db.client), return_exceptions=True)
    assert isinstance(outcomes[1], dict) and outcomes[1]["status"] == "resolved"
    assert await db.owner.fetchval("SELECT status FROM incidents WHERE id=$1", incident_id) == "resolved"
    with pytest.raises(HTTPException) as rejected:
        await incidents.acknowledge_incident(incident_id, db.admin, db.client)
    assert rejected.value.status_code == 409


async def test_incident_write_failure_is_unconfirmed_and_transaction_rolls_back(incident_db):
    db = incident_db; incident_id = await db.new_incident(); await db.fail_update(incident_id)
    with pytest.raises(HTTPException) as error:
        await incidents.acknowledge_incident(incident_id, db.admin, db.client)
    assert error.value.status_code == 503
    saved = await db.owner.fetchrow("SELECT status,acknowledged_at FROM incidents WHERE id=$1", incident_id)
    assert saved["status"] == "open" and saved["acknowledged_at"] is None
