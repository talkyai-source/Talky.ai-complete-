"""Actual inbox health helper on disposable private PostgreSQL table copies.

No provider calls, public writes or full deployment/RLS parity claims. The
canonical account->connector FK and a restrictive forced tenant policy are
installed on private LIKE copies; full triggers/tenant FK/grants are not copied.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import asyncpg

MAIN = Path(__file__).resolve().parents[1]
SOURCE = MAIN.parent/'inbox-original-account-20261005'
OUT = MAIN/'docs/sessions/artifacts/inbox-health-actual-pg'
DSN = 'postgresql://talky@127.0.0.1:55434/cp04_acceptance_test'
sys.path.insert(0,str(SOURCE/'backend'))
os.environ.update(ENVIRONMENT='test',DATABASE_URL='postgresql://test:test@127.0.0.1:1/unavailable_test')
from app.infrastructure.assistant.tools import inbox
from app.services.connector_resolver import _authorization_snapshot_for_row

TENANT,OTHER,CID,AID,BID=[uuid4() for _ in range(5)]
STAMP=datetime(2026,10,5,tzinfo=timezone.utc)


def source_hashes():
    return {p:hashlib.sha256((SOURCE/p).read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        for p in ('backend/app/infrastructure/assistant/tools/inbox.py','backend/app/services/connector_resolver.py')}


async def snapshot(conn):
    data={'head':await conn.fetchval('SELECT version_num FROM public.alembic_version')}
    for table in ('connectors','connector_accounts'):
        data[table]=dict(await conn.fetchrow(f'''SELECT count(*) n,
            md5(coalesce(string_agg(h,'|' ORDER BY h),'')) digest FROM
            (SELECT md5(row_to_json(t)::text) h FROM public.{table} t) s'''))
    data['catalog']=[dict(r) for r in await conn.fetch('''SELECT relname,relrowsecurity,
        relforcerowsecurity,relacl::text FROM pg_class WHERE oid IN
        ('public.connectors'::regclass,'public.connector_accounts'::regclass) ORDER BY relname''')]
    return data


async def scope(conn,tenant=TENANT):
    await conn.execute("SELECT set_config('app.current_tenant_id',$1,true)",str(tenant))


async def add_b(conn,*,status='active',stamp=None):
    await conn.execute('''INSERT INTO connector_accounts(id,connector_id,tenant_id,status,
        last_refreshed_at,access_token_encrypted,refresh_token_encrypted)
        VALUES($1,$2,$3,$4,$5,'synthetic-B-access','synthetic-B-refresh')''',
        BID,CID,TENANT,status,stamp if stamp is not None else STAMP+timedelta(seconds=1))


async def seed(conn,*,other_status=None):
    async with conn.transaction():
        await scope(conn)
        await conn.execute('DELETE FROM connector_accounts WHERE tenant_id=$1',TENANT)
        await conn.execute('DELETE FROM connectors WHERE tenant_id=$1',TENANT)
        await conn.execute("INSERT INTO connectors(id,tenant_id,type,provider,status) VALUES($1,$2,'email','gmail','active')",CID,TENANT)
        await conn.execute('''INSERT INTO connector_accounts(id,connector_id,tenant_id,status,
            last_refreshed_at,token_expires_at,access_token_encrypted,refresh_token_encrypted)
            VALUES($1,$2,$3,'active',$4,$5,'synthetic-A-access','synthetic-A-refresh')''',
            AID,CID,TENANT,STAMP,STAMP+timedelta(hours=1))
        row=await conn.fetchrow('SELECT * FROM connector_accounts WHERE id=$1',AID)
        if other_status:
            await add_b(conn,status=other_status,stamp=STAMP-timedelta(seconds=1))
    proof=_authorization_snapshot_for_row(str(TENANT),str(CID),'gmail',row)
    assert proof is not None
    json_row=dict(row)
    for key in ('last_refreshed_at','token_expires_at'):
        json_row[key]=json_row[key].isoformat().replace('+00:00','Z')
    assert _authorization_snapshot_for_row(str(TENANT),str(CID),'gmail',json_row)==proof
    return proof


async def state(conn):
    async with conn.transaction():
        await scope(conn)
        return {'parent':await conn.fetchval('SELECT status FROM connectors WHERE id=$1',CID),
            'accounts':{str(r['id']):r['status'] for r in await conn.fetch('SELECT id,status FROM connector_accounts ORDER BY id')}}


class BarrierPool:
    """Pass every SQL statement to PostgreSQL; optional barriers only schedule it."""
    def __init__(self,pool,*,pause=None,fail_parent=False):
        self.pool,self.pause,self.fail_parent=pool,pause,fail_parent
        self.reached,self.release,self.attempted=asyncio.Event(),asyncio.Event(),asyncio.Event()
        self.pid=None
        self.queries=[]

    @asynccontextmanager
    async def acquire(self,**kwargs):
        async with self.pool.acquire(**kwargs) as conn:
            self.pid=conn.get_server_pid()
            owner=self
            class Proxy:
                def __getattr__(self,name): return getattr(conn,name)
                async def fetchrow(self,sql,*args): return await self.call('fetchrow',sql,args)
                async def fetch(self,sql,*args): return await self.call('fetch',sql,args)
                async def fetchval(self,sql,*args): return await self.call('fetchval',sql,args)
                async def call(self,method,sql,args):
                    norm=' '.join(sql.split())
                    kind=('parent_lock' if norm.startswith('SELECT id FROM connectors') else
                          'all_accounts_lock' if norm.startswith('SELECT id FROM connector_accounts') else
                          'account_write' if norm.startswith('UPDATE connector_accounts') else
                          'parent_write' if norm.startswith('UPDATE connectors') else 'active_rank')
                    owner.queries.append(kind)
                    owner.attempted.set()
                    if kind=='parent_write' and owner.fail_parent:
                        raise RuntimeError('synthetic parent failure after real account UPDATE')
                    result=await getattr(conn,method)(sql,*args)
                    if kind==owner.pause:
                        owner.reached.set()
                        await owner.release.wait()
                    return result
            yield Proxy()


async def expire(pool,proof,*,tenant=TENANT):
    return await inbox._mark_email_authorization_expired(
        SimpleNamespace(pool=pool),str(tenant),str(CID),authorization=proof)


async def wait_lock(observer,pid):
    for _ in range(150):
        if await observer.fetchval("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",pid):
            return
        await asyncio.sleep(.01)
    raise AssertionError('PostgreSQL lock wait was not observed')


async def run():
    assert not OUT.exists(),'Preserve earlier actual-helper evidence'
    OUT.mkdir(parents=True)
    report={'scope':__doc__,'source_directory':str(SOURCE),'source_before':source_hashes(),'cases':[]}
    owner=await asyncpg.connect(DSN,timeout=5,command_timeout=10)
    suffix=uuid4().hex
    schema=role='inbox_health_actual_'+suffix
    report.update(schema=schema,role=role)
    role_created=schema_created=False
    pool=reader=writer=None

    def passed(name,**details): report['cases'].append({'case':name,'passed':True,**details})

    try:
        assert await owner.fetchval('SELECT current_database()')=='cp04_acceptance_test'
        report['public_before']=await snapshot(owner)
        assert report['public_before']['head']=='0061_dnc_runtime_contract'
        await owner.execute(f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS')
        role_created=True
        await owner.execute(f'CREATE SCHEMA "{schema}"')
        schema_created=True
        for table in ('connectors','connector_accounts'):
            await owner.execute(f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)')
        await owner.execute(f'ALTER TABLE "{schema}".connector_accounts ADD FOREIGN KEY(connector_id) REFERENCES "{schema}".connectors(id) ON DELETE CASCADE')
        for table in ('connectors','connector_accounts'):
            await owner.execute(f'ALTER TABLE "{schema}".{table} ENABLE ROW LEVEL SECURITY')
            await owner.execute(f'ALTER TABLE "{schema}".{table} FORCE ROW LEVEL SECURITY')
            await owner.execute(f'''CREATE POLICY probe_tenant ON "{schema}".{table}
                USING(tenant_id=NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)
                WITH CHECK(tenant_id=NULLIF(current_setting('app.current_tenant_id',true),'')::uuid)''')
        await owner.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
        for table in ('connectors','connector_accounts'):
            for priv in ('INSERT','UPDATE','DELETE','TRUNCATE'):
                assert not await owner.fetchval('SELECT has_table_privilege($1,$2,$3)',role,'public.'+table,priv)
        private=f'postgresql://{role}@127.0.0.1:55434/cp04_acceptance_test'
        kwargs={'server_settings':{'search_path':schema},'timeout':5,'command_timeout':8}
        reader=await asyncpg.connect(private,**kwargs)
        writer=await asyncpg.connect(private,**kwargs)
        pool=await asyncpg.create_pool(private,min_size=1,max_size=1,**kwargs)
        assert await reader.fetchval('SHOW transaction_isolation')=='read committed'

        proof=await seed(reader)
        assert await expire(pool,proof)
        assert (await state(reader))=={'parent':'expired','accounts':{str(AID):'expired'}}
        passed('current confirmed generation expires both rows; datetime and JSON proof agree')

        proof=await seed(reader)
        async with writer.transaction():
            await scope(writer)
            await add_b(writer)
        assert not await expire(pool,proof)
        assert set((await state(reader))['accounts'].values())=={'active'}
        passed('replacement committed before health write survives')

        proof=await seed(reader)
        async with writer.transaction():
            await scope(writer)
            await writer.execute("UPDATE connector_accounts SET access_token_encrypted='synthetic-new' WHERE id=$1",AID)
        assert not await expire(pool,proof)
        assert (await state(reader))['parent']=='active'
        passed('changed credential ciphertext with identical timestamp rejects stale generation')

        proof=await seed(reader)
        audit=BarrierPool(pool)
        assert not await expire(audit,None)
        assert not await expire(audit,proof,tenant=OTHER)
        assert audit.queries==[]
        async with reader.transaction():
            await scope(reader,OTHER)
            assert await reader.fetchval('SELECT count(*) FROM connector_accounts')==0
        passed('missing proof and foreign tenant cannot query/mutate; private RLS denies foreign reads')

        proof=await seed(reader)
        async with writer.transaction():
            await scope(writer)
            await add_b(writer,stamp=STAMP)
        assert not await expire(pool,proof)
        assert (await state(reader))['parent']=='active'
        passed('equal newest active generations remain unconfirmed')

        proof=await seed(reader)
        barrier=BarrierPool(pool,pause='parent_lock')
        task=asyncio.create_task(expire(barrier,proof))
        await asyncio.wait_for(barrier.reached.wait(),2)
        async def reconnect():
            async with writer.transaction():
                await scope(writer)
                await add_b(writer)
            async with writer.transaction():
                await scope(writer)
                await writer.execute("UPDATE connectors SET status='active' WHERE id=$1",CID)
        reconnect_task=asyncio.create_task(reconnect())
        await wait_lock(owner,writer.get_server_pid())
        barrier.release.set()
        assert await task
        await reconnect_task
        result=await state(reader)
        assert result['parent']=='active' and result['accounts'][str(BID)]=='active'
        passed('health parent lock precedes FK insert; reconnect later explicitly reactivates parent',lock_wait_observed=True)

        proof=await seed(reader)
        tx=writer.transaction()
        await tx.start(); await scope(writer); await add_b(writer)
        barrier=BarrierPool(pool)
        task=asyncio.create_task(expire(barrier,proof))
        await asyncio.wait_for(barrier.attempted.wait(),2)
        await wait_lock(owner,barrier.pid)
        await tx.commit()
        assert not await task
        assert (await state(reader))['parent']=='active'
        passed('uncommitted reconnect precedes parent lock; fresh account ranking sees replacement',lock_wait_observed=True)

        for other_status in ('active','revoked'):
            proof=await seed(reader,other_status=other_status)
            tx=writer.transaction()
            await tx.start(); await scope(writer)
            await writer.execute("UPDATE connector_accounts SET status='active',last_refreshed_at=$1 WHERE id=$2",STAMP+timedelta(seconds=2),BID)
            barrier=BarrierPool(pool)
            task=asyncio.create_task(expire(barrier,proof))
            await asyncio.wait_for(barrier.attempted.wait(),2)
            await wait_lock(owner,barrier.pid)
            await tx.commit()
            assert not await task
            assert (await state(reader))['parent']=='active'
            passed(f'{other_status} older account changes before account locks; fresh ranking refuses stale expiry',lock_wait_observed=True)

        proof=await seed(reader,other_status='revoked')
        barrier=BarrierPool(pool,pause='all_accounts_lock')
        task=asyncio.create_task(expire(barrier,proof))
        await asyncio.wait_for(barrier.reached.wait(),2)
        async def reactivate_old():
            async with writer.transaction():
                await scope(writer)
                await writer.execute("UPDATE connector_accounts SET status='active',last_refreshed_at=$1 WHERE id=$2",STAMP+timedelta(seconds=3),BID)
            async with writer.transaction():
                await scope(writer)
                await writer.execute("UPDATE connectors SET status='active' WHERE id=$1",CID)
        later=asyncio.create_task(reactivate_old())
        await wait_lock(owner,writer.get_server_pid())
        barrier.release.set()
        assert await task
        await later
        result=await state(reader)
        assert result['parent']=='active' and result['accounts'][str(BID)]=='active'
        passed('all-status locks serialize later inactive-row activation; later parent activation remains separate',lock_wait_observed=True)

        proof=await seed(reader)
        assert not await expire(BarrierPool(pool,fail_parent=True),proof)
        assert (await state(reader))=={'parent':'active','accounts':{str(AID):'active'}}
        passed('real account UPDATE rolls back after injected parent-boundary failure')

        proof=await seed(reader)
        barrier=BarrierPool(pool,pause='account_write')
        task=asyncio.create_task(expire(barrier,proof))
        await asyncio.wait_for(barrier.reached.wait(),2)
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        else: raise AssertionError('Cancellation was swallowed')
        assert (await state(reader))=={'parent':'active','accounts':{str(AID):'active'}}
        assert await expire(pool,proof)
        passed('external cancellation rolls back actual account write and releases pool/row locks')

        proof=await seed(reader)
        tx=writer.transaction(); await tx.start(); await scope(writer)
        await writer.fetchrow('SELECT id FROM connectors WHERE id=$1 FOR UPDATE',CID)
        previous_timeout=inbox._EMAIL_HEALTH_TIMEOUT_SECONDS
        inbox._EMAIL_HEALTH_TIMEOUT_SECONDS=.1
        try: assert not await expire(pool,proof)
        finally:
            inbox._EMAIL_HEALTH_TIMEOUT_SECONDS=previous_timeout
            await tx.rollback()
        assert (await state(reader))=={'parent':'active','accounts':{str(AID):'active'}}
        assert await expire(pool,proof)
        passed('bounded lock timeout leaves no status update and pool is reusable',test_timeout_seconds=.1,production_timeout_seconds=previous_timeout)
        report['passed']=len(report['cases'])==13
    finally:
        for connection in (reader,writer):
            if connection: await connection.close()
        if pool: await asyncio.wait_for(pool.close(),10)
        if schema_created:
            assert await owner.fetchval('SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname=$1',schema)==await owner.fetchval('SELECT current_user')
            await owner.execute(f'DROP SCHEMA "{schema}" CASCADE')
        if role_created: await owner.execute(f'DROP ROLE "{role}"')
        report['public_after']=await snapshot(owner)
        report['public_unchanged']=report.get('public_before')==report['public_after']
        report['schema_removed']=not await owner.fetchval('SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=$1)',schema)
        report['role_removed']=not await owner.fetchval('SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)',role)
        report['source_after']=source_hashes()
        report['source_unchanged']=report['source_before']==report['source_after']
        (OUT/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        await owner.close()
    assert report.get('passed') and report['public_unchanged'] and report['source_unchanged']
    print(json.dumps({k:report[k] for k in ('passed','public_unchanged','source_unchanged','schema_removed','role_removed')},indent=2))


if __name__=='__main__':
    asyncio.run(run())
