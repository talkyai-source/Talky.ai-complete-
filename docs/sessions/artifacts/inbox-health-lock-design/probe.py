"""Disposable PostgreSQL concurrency probe for a proposed inbox health write.

This is a SQL design probe, NOT a run of a repaired application method. Copies
retain column/index shape and add the real account -> connector FK used by the
locking argument; tenant FK, production grants/triggers/policies are not copied.
All writes use a fresh private schema and restricted role on the existing local
test server. No public rows are changed and no provider request is made.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import asyncpg


DSN = "postgresql://talky@127.0.0.1:55434/cp04_acceptance_test"
OUT = Path(__file__).with_suffix(".json")
TENANT, OTHER, CONNECTOR, ACCOUNT_A, ACCOUNT_B = [uuid4() for _ in range(5)]
GENERATION = datetime(2026, 10, 5, tzinfo=timezone.utc)


async def public_snapshot(conn):
    result = {"head": await conn.fetchval("SELECT version_num FROM public.alembic_version")}
    for table in ("connectors", "connector_accounts"):
        result[table] = dict(await conn.fetchrow(f"""SELECT count(*) n,
            md5(coalesce(string_agg(h,'|' ORDER BY h),'')) digest FROM
            (SELECT md5(row_to_json(t)::text) h FROM public.{table} t) s"""))
    result["catalog"] = [dict(r) for r in await conn.fetch("""SELECT relname,
        relrowsecurity, relforcerowsecurity, relacl::text FROM pg_class
        WHERE oid IN ('public.connectors'::regclass,'public.connector_accounts'::regclass)
        ORDER BY relname""")]
    return result


async def set_scope(conn, tenant=TENANT):
    await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)", str(tenant))


async def seed(conn):
    async with conn.transaction():
        await set_scope(conn)
        await conn.execute("DELETE FROM connector_accounts WHERE tenant_id=$1", TENANT)
        await conn.execute("DELETE FROM connectors WHERE tenant_id=$1", TENANT)
        await conn.execute("""INSERT INTO connectors(id,tenant_id,type,provider,status)
            VALUES($1,$2,'email','gmail','active')""", CONNECTOR, TENANT)
        await conn.execute("""INSERT INTO connector_accounts(id,connector_id,tenant_id,
            status,last_refreshed_at,access_token_encrypted,refresh_token_encrypted)
            VALUES($1,$2,$3,'active',$4,'synthetic-access-A','synthetic-refresh-A')""",
            ACCOUNT_A, CONNECTOR, TENANT, GENERATION)


async def insert_b(conn):
    await conn.execute("""INSERT INTO connector_accounts(id,connector_id,tenant_id,
        status,last_refreshed_at,access_token_encrypted,refresh_token_encrypted)
        VALUES($1,$2,$3,'active',$4,'synthetic-access-B','synthetic-refresh-B')""",
        ACCOUNT_B, CONNECTOR, TENANT, GENERATION + timedelta(seconds=1))


async def lock_parent(conn, tenant=TENANT):
    return await conn.fetchrow("""SELECT id FROM connectors
        WHERE id=$1 AND tenant_id=$2 AND provider='gmail' AND status='active'
        FOR UPDATE""", CONNECTOR, tenant)


async def expire_after_parent_lock(conn, *, fail_after_account=False):
    # A fresh statement after the parent lock sees any FK-protected insert
    # which committed while the parent lock was being acquired.
    rows = await conn.fetch("""SELECT id,last_refreshed_at,access_token_encrypted,
        refresh_token_encrypted FROM connector_accounts WHERE connector_id=$1
        AND tenant_id=$2 AND status='active' ORDER BY last_refreshed_at DESC
        LIMIT 2 FOR UPDATE""", CONNECTOR, TENANT)
    # Ambiguous equal latest generation is held rather than arbitrarily picked.
    if not rows or (len(rows) > 1 and rows[0]['last_refreshed_at'] == rows[1]['last_refreshed_at']):
        return False
    row = rows[0]
    if (row['id'] != ACCOUNT_A or row['last_refreshed_at'] != GENERATION
            or row['access_token_encrypted'] != 'synthetic-access-A'
            or row['refresh_token_encrypted'] != 'synthetic-refresh-A'):
        return False
    await conn.execute("UPDATE connector_accounts SET status='expired' WHERE id=$1 AND tenant_id=$2", ACCOUNT_A, TENANT)
    if fail_after_account:
        raise RuntimeError('synthetic transaction failure')
    await conn.execute("UPDATE connectors SET status='expired' WHERE id=$1 AND tenant_id=$2", CONNECTOR, TENANT)
    return True


async def state(conn):
    async with conn.transaction():
        await set_scope(conn)
        parent = await conn.fetchval("SELECT status FROM connectors WHERE id=$1", CONNECTOR)
        rows = await conn.fetch("SELECT id,status FROM connector_accounts ORDER BY id")
    return {'parent': parent, 'accounts': {str(r['id']): r['status'] for r in rows}}


async def wait_for_lock(observer, pid):
    for _ in range(150):
        waiting = await observer.fetchval("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1", pid)
        if waiting:
            return
        await asyncio.sleep(.01)
    raise AssertionError('Expected PostgreSQL lock wait was not observed')


async def run():
    assert not OUT.exists(), 'Preserve earlier evidence'
    owner = await asyncpg.connect(DSN, timeout=5, command_timeout=10)
    suffix = uuid4().hex
    schema = role = 'inbox_health_probe_' + suffix
    created_role = created_schema = False
    conns = []
    report = {'scope': __doc__, 'cases': [], 'schema': schema, 'role': role}
    try:
        assert await owner.fetchval('SELECT current_database()') == 'cp04_acceptance_test'
        report['before'] = await public_snapshot(owner)
        assert report['before']['head'] == '0061_dnc_runtime_contract'
        await owner.execute(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS')
        created_role = True
        await owner.execute(f'CREATE SCHEMA "{schema}"')
        created_schema = True
        for table in ('connectors', 'connector_accounts'):
            await owner.execute(f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)')
        await owner.execute(f'ALTER TABLE "{schema}".connector_accounts ADD FOREIGN KEY(connector_id) REFERENCES "{schema}".connectors(id) ON DELETE CASCADE')
        for table in ('connectors', 'connector_accounts'):
            await owner.execute(f'ALTER TABLE "{schema}".{table} ENABLE ROW LEVEL SECURITY')
            await owner.execute(f'ALTER TABLE "{schema}".{table} FORCE ROW LEVEL SECURITY')
            await owner.execute(f'''CREATE POLICY probe_tenant ON "{schema}".{table}
                USING (tenant_id=NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)
                WITH CHECK (tenant_id=NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)''')
        await owner.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        for table in ('connectors', 'connector_accounts'):
            for privilege in ('INSERT','UPDATE','DELETE','TRUNCATE'):
                assert not await owner.fetchval('SELECT has_table_privilege($1,$2,$3)', role, 'public.'+table, privilege)
        for _ in range(2):
            conns.append(await asyncpg.connect(f'postgresql://{role}@127.0.0.1:55434/cp04_acceptance_test',
                server_settings={'search_path':schema},timeout=5,command_timeout=5))
        a,b=conns
        assert await a.fetchval('SHOW transaction_isolation') == 'read committed'

        await seed(a)
        async with a.transaction():
            await set_scope(a)
            assert await lock_parent(a)
            assert await expire_after_parent_lock(a)
        observed=await state(a)
        assert observed['parent']=='expired' and observed['accounts'][str(ACCOUNT_A)]=='expired'
        report['cases'].append({'case':'current confirmed authorization expires atomically','state':observed})

        await seed(a)
        async with b.transaction():
            await set_scope(b)
            await insert_b(b)
        async with a.transaction():
            await set_scope(a)
            assert await lock_parent(a)
            assert not await expire_after_parent_lock(a)
        observed=await state(a)
        assert observed['parent']=='active' and set(observed['accounts'].values())=={'active'}
        report['cases'].append({'case':'new account committed before health lock is preserved','state':observed})

        await seed(a)
        tx= a.transaction()
        await tx.start()
        await set_scope(a)
        assert await lock_parent(a)
        async def reconnect():
            async with b.transaction():
                await set_scope(b)
                await insert_b(b)
            # Canonical callback activates in a separate transaction.
            async with b.transaction():
                await set_scope(b)
                await b.execute("UPDATE connectors SET status='active' WHERE id=$1",CONNECTOR)
        pending=asyncio.create_task(reconnect())
        await wait_for_lock(owner,b.get_server_pid())
        assert await expire_after_parent_lock(a)
        await tx.commit()
        await pending
        observed=await state(a)
        assert observed['parent']=='active' and observed['accounts'][str(ACCOUNT_B)]=='active'
        report['cases'].append({'case':'health first blocks FK insert then reconnect activates new account','lock_wait_observed':True,'state':observed})

        await seed(a)
        tx=b.transaction()
        await tx.start()
        await set_scope(b)
        await insert_b(b)
        async def pending_expiry():
            async with a.transaction():
                await set_scope(a)
                assert await lock_parent(a)
                return await expire_after_parent_lock(a)
        pending=asyncio.create_task(pending_expiry())
        await wait_for_lock(owner,a.get_server_pid())
        await tx.commit()
        assert not await pending
        observed=await state(a)
        assert observed['parent']=='active' and set(observed['accounts'].values())=={'active'}
        report['cases'].append({'case':'uncommitted reconnect blocks health lock then fresh statement sees new account','lock_wait_observed':True,'state':observed})

        await seed(a)
        async with a.transaction():
            await set_scope(a)
            assert await lock_parent(a)
            async with b.transaction():
                await set_scope(b)
                await b.execute("UPDATE connector_accounts SET last_refreshed_at=$1,access_token_encrypted='synthetic-new-access' WHERE id=$2", GENERATION+timedelta(seconds=2),ACCOUNT_A)
            assert not await expire_after_parent_lock(a)
        observed=await state(a)
        assert observed['parent']=='active' and observed['accounts'][str(ACCOUNT_A)]=='active'
        report['cases'].append({'case':'same account newer token committed while parent locked survives stale failure','state':observed})

        await seed(a)
        try:
            async with a.transaction():
                await set_scope(a)
                assert await lock_parent(a)
                await expire_after_parent_lock(a,fail_after_account=True)
        except RuntimeError as exc:
            assert str(exc)=='synthetic transaction failure'
        observed=await state(a)
        assert observed['parent']=='active' and observed['accounts'][str(ACCOUNT_A)]=='active'
        report['cases'].append({'case':'mid transaction failure rolls back account and parent','state':observed})

        async with a.transaction():
            await set_scope(a,OTHER)
            assert await lock_parent(a,OTHER) is None
            assert await a.fetchval('SELECT count(*) FROM connector_accounts')==0
        report['cases'].append({'case':'cross tenant no parent or account visible','passed':True})
        report['passed']=len(report['cases'])==7
    finally:
        for conn in conns:
            await conn.close()
        if created_schema:
            assert await owner.fetchval('SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=$1',schema)==await owner.fetchval('SELECT current_user')
            await owner.execute(f'DROP SCHEMA "{schema}" CASCADE')
        if created_role:
            await owner.execute(f'DROP ROLE "{role}"')
        report['after']=await public_snapshot(owner)
        report['public_unchanged']=report.get('before')==report['after']
        report['schema_removed']=not await owner.fetchval('SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=$1)',schema)
        report['role_removed']=not await owner.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)',role)
        OUT.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        await owner.close()
    assert report['public_unchanged'] and report['passed']
    print(json.dumps({k:report[k] for k in ('passed','public_unchanged','schema_removed','role_removed')},indent=2))


if __name__=='__main__':
    asyncio.run(run())
