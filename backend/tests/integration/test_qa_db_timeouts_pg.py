"""F2-05 against a dedicated loopback PostgreSQL database; never production.

Set QA_DB_TIMEOUT_TEST_DSN to a non-superuser connection in a disposable
loopback database ending in _test. No application schema or providers needed.
"""
import asyncio
import os
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.core import db
from app.core.db_utils import acquire_with_tenant
from app.core.error_handlers import db_pool_timeout_exception_handler

pytestmark = pytest.mark.integration
TENANT_A = '00000000-0000-4000-8000-000000000001'
TENANT_B = '00000000-0000-4000-8000-000000000002'


@pytest.fixture
def dsn():
    value = os.environ.get('QA_DB_TIMEOUT_TEST_DSN')
    if not value:
        pytest.skip('Dedicated QA_DB_TIMEOUT_TEST_DSN not configured')
    parsed = urlsplit(value)
    assert parsed.hostname in {'localhost', '127.0.0.1', '::1'}, 'Loopback only'
    assert parsed.path.endswith('_test'), 'Disposable *_test database required'
    return value


@pytest_asyncio.fixture
async def pool(dsn, monkeypatch):
    monkeypatch.setenv('PG_STATEMENT_TIMEOUT_MS', '1000')
    monkeypatch.setenv('PG_LOCK_TIMEOUT_MS', '50')
    # Shorten the existing configurable admission bound for this local test.
    monkeypatch.setattr(db, '_ACQUIRE_TIMEOUT_S', .05)
    result = await asyncpg.create_pool(dsn, min_size=10, max_size=10,
                                      command_timeout=2, ssl=False)
    async with result.acquire() as conn:
        role = await conn.fetchrow('SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user')
        assert not role['rolsuper'] and not role['rolbypassrls']
    try:
        yield result
    finally:
        await asyncio.wait_for(result.close(), 5)


async def test_fifty_http_requests_ten_slots_fail_fast_and_recover(pool):
    app = FastAPI()
    app.add_exception_handler(db.DatabasePoolTimeoutError, db_pool_timeout_exception_handler)
    entered = 0
    full = asyncio.Event()

    @app.get('/probe')
    async def probe(slow: bool = False):
        nonlocal entered
        async with acquire_with_tenant(pool, TENANT_A) as conn:
            if slow:
                entered += 1
                if entered == 10:
                    full.set()
                await conn.execute('SELECT pg_sleep(0.3)')
            return {'value': await conn.fetchval('SELECT 1')}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://synthetic') as client:
        holders = [asyncio.create_task(client.get('/probe?slow=true')) for _ in range(10)]
        await asyncio.wait_for(full.wait(), 2)
        waiters = [asyncio.create_task(client.get('/probe')) for _ in range(40)]
        responses = await asyncio.wait_for(asyncio.gather(*holders, *waiters), 3)
        counts = {status: sum(r.status_code == status for r in responses) for status in {200, 503}}
        print(f'REAL_PG_SATURATION: max=10 requests=50 statuses={counts} pending=0')
        assert counts == {200: 10, 503: 40}
        assert all(r.headers['Retry-After'] == '5' for r in responses if r.status_code == 503)
        assert (await client.get('/probe')).json() == {'value': 1}


async def test_real_statement_timeout_rolls_back_and_connection_recovers(pool, monkeypatch):
    monkeypatch.setenv('PG_STATEMENT_TIMEOUT_MS', '50')
    with pytest.raises(asyncpg.QueryCanceledError):
        async with acquire_with_tenant(pool, TENANT_A) as conn:
            await conn.execute('SELECT pg_sleep(0.2)')
    async with acquire_with_tenant(pool, TENANT_B) as conn:
        assert await conn.fetchval('SELECT 1') == 1
        assert await conn.fetchval("SELECT current_setting('app.current_tenant_id')") == TENANT_B


async def test_real_lock_timeout_rolls_back_and_connection_recovers(pool):
    async with pool.acquire() as blocker:
        await blocker.execute('SELECT pg_advisory_lock(62810271)')
        try:
            with pytest.raises(asyncpg.LockNotAvailableError):
                async with acquire_with_tenant(pool, TENANT_A) as conn:
                    await conn.execute('SELECT pg_advisory_xact_lock(62810271)', timeout=.3)
        finally:
            await blocker.execute('SELECT pg_advisory_unlock(62810271)')
    async with acquire_with_tenant(pool, TENANT_B) as conn:
        assert await conn.fetchval('SELECT pg_try_advisory_xact_lock(62810271)')


async def test_transaction_isolation_can_still_be_selected_after_entry(pool):
    async with acquire_with_tenant(pool, TENANT_A) as conn:
        await conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        assert await conn.fetchval('SHOW transaction_isolation') == 'repeatable read'
        assert await conn.fetchval('SHOW transaction_read_only') == 'on'


@pytest.mark.parametrize('wrapper', ['tenant', 'primary', 'read'])
async def test_transaction_timeouts_apply_and_do_not_leak_to_reused_connection(pool, monkeypatch, wrapper):
    import app.api.v1.dependencies  # noqa: F401
    import app.core.security.tenant_isolation as isolation
    monkeypatch.setattr(db, '_pool', pool)
    monkeypatch.setattr(db, '_read_pool', pool)
    monkeypatch.setattr(isolation, 'get_current_tenant_id', lambda: TENANT_A)
    monkeypatch.setattr(isolation, 'get_bypass_rls', lambda: False)
    context = {'tenant': lambda: acquire_with_tenant(pool, TENANT_A),
               'primary': db.get_db, 'read': db.get_read_db}[wrapper]
    async with pool.acquire() as conn:
        baseline = await conn.fetchrow("SELECT current_setting('statement_timeout') AS statement, current_setting('lock_timeout') AS lock")
    async with context() as conn:
        assert await conn.fetchval('SHOW statement_timeout') == '1s'
        assert await conn.fetchval('SHOW lock_timeout') == '50ms'
        assert await conn.fetchval("SELECT current_setting('app.current_tenant_id')") == TENANT_A
        assert await conn.fetchval('SHOW transaction_read_only') == ('on' if wrapper == 'read' else 'off')
    async with pool.acquire() as conn:
        assert await conn.fetchval('SHOW statement_timeout') == baseline['statement']
        assert await conn.fetchval('SHOW lock_timeout') == baseline['lock']
        assert await conn.fetchval("SELECT nullif(current_setting('app.current_tenant_id',true),'')") is None


async def test_cancellation_returns_actual_pool_capacity(pool):
    acquired = asyncio.Event()

    async def operation():
        async with acquire_with_tenant(pool, TENANT_A) as conn:
            acquired.set()
            await conn.execute('SELECT pg_sleep(1)')

    task = asyncio.create_task(operation())
    await asyncio.wait_for(acquired.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.get_idle_size() == pool.get_size() == 10
    async with acquire_with_tenant(pool, TENANT_B) as conn:
        assert await conn.fetchval('SELECT 1') == 1


async def test_timed_out_write_rolls_back_and_force_rls_survives_pool_reuse(dsn, monkeypatch):
    monkeypatch.setenv('PG_STATEMENT_TIMEOUT_MS', '50')
    monkeypatch.setenv('PG_LOCK_TIMEOUT_MS', '20')
    single = await asyncpg.create_pool(dsn, min_size=1, max_size=1, command_timeout=2, ssl=False)
    try:
        async with single.acquire() as conn:
            assert not await conn.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
            await conn.execute('CREATE TEMP TABLE qa_timeout_rows (tenant_id uuid, note text)')
            await conn.execute('ALTER TABLE qa_timeout_rows ENABLE ROW LEVEL SECURITY')
            await conn.execute('ALTER TABLE qa_timeout_rows FORCE ROW LEVEL SECURITY')
            await conn.execute("""CREATE POLICY tenant_scope ON qa_timeout_rows USING (
                current_setting('app.bypass_rls',true)='on' OR
                tenant_id::text=current_setting('app.current_tenant_id',true)
            )""")
        async with acquire_with_tenant(single, None) as conn:
            await conn.execute('INSERT INTO qa_timeout_rows VALUES ($1, $2),($3, $4)',
                               TENANT_A, 'original A', TENANT_B, 'original B')
        with pytest.raises(asyncpg.QueryCanceledError):
            async with acquire_with_tenant(single, TENANT_A) as conn:
                assert await conn.fetchval('SELECT count(*) FROM qa_timeout_rows') == 1
                await conn.execute("UPDATE qa_timeout_rows SET note='must roll back'")
                await conn.execute('SELECT pg_sleep(0.2)')
        async with acquire_with_tenant(single, TENANT_B) as conn:
            assert await conn.fetchval('SELECT note FROM qa_timeout_rows') == 'original B'
            assert await conn.fetchval('SELECT count(*) FROM qa_timeout_rows') == 1
        async with acquire_with_tenant(single, TENANT_A) as conn:
            assert await conn.fetchval('SELECT note FROM qa_timeout_rows') == 'original A'
        async with single.acquire() as conn:
            assert await conn.fetchval("SELECT nullif(current_setting('app.current_tenant_id',true),'')") is None
            assert await conn.fetchval("SELECT current_setting('app.bypass_rls',true)='on'") is not True
    finally:
        await asyncio.wait_for(single.close(), 5)
