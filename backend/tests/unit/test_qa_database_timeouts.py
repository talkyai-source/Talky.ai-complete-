"""F2-05: bounded acquisition, truthful 503s, and transaction-local limits."""
import asyncio
import runpy
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

import app.api.v1.dependencies  # noqa: F401 - resolve existing circular imports first
from app.core import db, db_utils
from app.core.error_handlers import db_pool_timeout_exception_handler


TENANT = "11111111-1111-1111-1111-111111111111"


class Connection:
    def __init__(self):
        self.events = []
        self.execute_error = None

    @asynccontextmanager
    async def transaction(self, **kwargs):
        self.events.append(("begin", kwargs))
        try:
            yield
        except BaseException:
            self.events.append(("rollback",))
            raise
        else:
            self.events.append(("commit",))

    async def execute(self, sql, *args):
        self.events.append(("execute", sql, args))
        if self.execute_error is not None:
            raise self.execute_error


class Pool:
    """One real asyncio semaphore; supports asyncpg's await/context protocols."""
    def __init__(self):
        self.semaphore = asyncio.Semaphore(1)
        self.conn = Connection()
        self.waiting = asyncio.Event()
        self.releases = 0
        self.context_exceptions = []

    def acquire(self, *, timeout=None):
        pool = self

        class Lease:
            async def __aenter__(self):
                pool.waiting.set()
                async with asyncio.timeout(timeout):
                    await pool.semaphore.acquire()
                return pool.conn

            def __await__(self):
                return self.__aenter__().__await__()

            async def __aexit__(self, exc_type, exc, traceback):
                pool.context_exceptions.append(exc)
                await pool.release(pool.conn)

        return Lease()

    async def release(self, conn):
        assert conn is self.conn
        self.releases += 1
        self.semaphore.release()


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_read_pool", None)
    monkeypatch.setattr(db, "_ACQUIRE_TIMEOUT_S", 0.02)
    monkeypatch.delenv("PG_STATEMENT_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("PG_LOCK_TIMEOUT_MS", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic/primary")
    monkeypatch.delenv("READ_DATABASE_URL", raising=False)


@pytest.mark.parametrize("kwargs", [{}, {"timeout": None}])
async def test_default_tenant_acquisition_is_bounded_and_catchable(kwargs):
    pool = Pool()
    async with pool.acquire():
        async def acquire():
            async with db_utils.acquire_with_tenant(pool, TENANT, **kwargs):
                pytest.fail("A saturated pool must not yield a connection")

        started = time.monotonic()
        with pytest.raises(db.DatabasePoolTimeoutError) as caught:
            await asyncio.wait_for(acquire(), 0.4)
        assert isinstance(caught.value, asyncio.TimeoutError)
        assert isinstance(caught.value, RuntimeError)
        assert time.monotonic() - started < 0.3
        assert pool.releases == 0
    async with db_utils.acquire_with_tenant(pool, TENANT):
        pass
    assert pool.releases == 2


@pytest.mark.parametrize("wrapper", ["tenant", "raw"])
async def test_explicit_acquisition_timeout_uses_existing_http_503_contract(monkeypatch, wrapper):
    monkeypatch.setattr(db, "_ACQUIRE_TIMEOUT_S", 1.0)
    pool = Pool()
    app = FastAPI()
    app.add_exception_handler(db.DatabasePoolTimeoutError, db_pool_timeout_exception_handler)

    @app.get("/synthetic-query")
    async def endpoint():
        context = (db_utils.acquire_with_tenant(pool, TENANT, timeout=0.02) if wrapper == "tenant"
                   else db_utils.acquire_connection(pool, timeout=0.02))
        async with context:
            return {"unexpected": True}

    async with pool.acquire():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                     base_url="http://synthetic") as client:
            response = await asyncio.wait_for(client.get("/synthetic-query"), 0.4)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["error"]["code"] == "service_unavailable"


async def test_explicit_acquisition_budget_can_exceed_default(monkeypatch):
    monkeypatch.setattr(db, "_ACQUIRE_TIMEOUT_S", 0.001)
    pool = Pool()
    holder = pool.acquire()
    await holder.__aenter__()

    async def release_holder():
        await asyncio.sleep(0.025)
        await holder.__aexit__(None, None, None)

    releasing = asyncio.create_task(release_holder())
    try:
        async with db_utils.acquire_with_tenant(pool, TENANT, timeout=0.2):
            pass
    finally:
        await releasing
    assert pool.releases == 2


async def test_acquisition_deadline_does_not_limit_caller_body():
    pool = Pool()
    async with db_utils.acquire_with_tenant(pool, TENANT, timeout=0.001):
        await asyncio.sleep(0.025)
    assert pool.conn.events[-1] == ("commit",)
    assert pool.releases == 1


@pytest.mark.parametrize("where", ["setup", "body"])
async def test_non_acquisition_timeout_keeps_identity_and_releases(where):
    pool = Pool()
    original = asyncio.TimeoutError("synthetic caller timeout")
    if where == "setup":
        pool.conn.execute_error = original
    with pytest.raises(asyncio.TimeoutError) as caught:
        async with db_utils.acquire_with_tenant(pool, TENANT):
            raise original
    assert caught.value is original
    assert not isinstance(caught.value, db.DatabasePoolTimeoutError)
    assert pool.conn.events[-1] == ("rollback",)
    assert pool.context_exceptions == [original]
    assert pool.releases == 1


async def test_cancelled_waiter_does_not_release_another_callers_connection():
    pool = Pool()
    async with pool.acquire():
        pool.waiting.clear()

        async def waiter():
            async with db_utils.acquire_with_tenant(pool, TENANT, timeout=1):
                pytest.fail("cancelled waiter entered")

        task = asyncio.create_task(waiter())
        await pool.waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.releases == 0
    async with db_utils.acquire_with_tenant(pool, TENANT):
        pass
    assert pool.releases == 2


async def test_cancelled_body_rolls_back_and_releases():
    pool = Pool()
    entered = asyncio.Event()

    async def body():
        async with db_utils.acquire_with_tenant(pool, TENANT):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(body())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.conn.events[-1] == ("rollback",)
    assert isinstance(pool.context_exceptions[0], asyncio.CancelledError)
    assert pool.releases == 1


@pytest.mark.parametrize("wrapper", ["tenant", "primary", "read"])
@pytest.mark.parametrize("configured,expected", [(False, (60000, 10000)), (True, (1250, 250))])
async def test_query_limits_are_applied_inside_each_transaction(monkeypatch, wrapper, configured, expected):
    from app.core.security import tenant_isolation

    if configured:
        monkeypatch.setenv("PG_STATEMENT_TIMEOUT_MS", "1250")
        monkeypatch.setenv("PG_LOCK_TIMEOUT_MS", "250")
    monkeypatch.setattr(tenant_isolation, "get_current_tenant_id", lambda: TENANT)
    monkeypatch.setattr(tenant_isolation, "get_bypass_rls", lambda: False)
    pool = Pool()
    monkeypatch.setattr(db, "_pool", pool)
    monkeypatch.setattr(db, "_read_pool", pool)
    for _ in range(2):
        pool.conn.events.clear()
        context = (db_utils.acquire_with_tenant(pool, TENANT) if wrapper == "tenant"
                   else db.get_db() if wrapper == "primary" else db.get_read_db())
        async with context as conn:
            assert conn.events[0][0] == "begin"
            queries = [e[1] for e in conn.events if e[0] == "execute"]
            assert f"SET LOCAL statement_timeout = '{expected[0]}ms'" in queries
            assert f"SET LOCAL lock_timeout = '{expected[1]}ms'" in queries
            assert any("current_tenant_id" in query for query in queries)
            if wrapper == "tenant":
                # Utility SET LOCAL preserves callers' later SET TRANSACTION.
                assert all(query.startswith("SET LOCAL ") for query in queries)
        assert conn.events[-1] == ("commit",)
    assert pool.releases == 2


async def test_pool_startup_preserves_pgbouncer_compatible_parameters(monkeypatch):
    monkeypatch.setenv("READ_DATABASE_URL", "postgresql://synthetic/read")
    creator = AsyncMock(side_effect=[SimpleNamespace(), SimpleNamespace()])
    monkeypatch.setattr(db.asyncpg, "create_pool", creator)
    await db.init_db_pool()
    assert creator.await_count == 2
    assert [call.kwargs["server_settings"] for call in creator.await_args_list] == [
        {"application_name": "talkyai-backend"},
        {"application_name": "talkyai-backend-ro"},
    ]
    assert all(call.kwargs["command_timeout"] == 60 for call in creator.await_args_list)


@pytest.mark.parametrize("name", ["PG_STATEMENT_TIMEOUT_MS", "PG_LOCK_TIMEOUT_MS"])
@pytest.mark.parametrize("value", ["0", "-1", "NaN", "inf", "1.5", "2147483648", ""])
async def test_invalid_query_limits_fail_before_creating_any_pool(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setenv("READ_DATABASE_URL", "postgresql://synthetic/read")
    creator = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(db.asyncpg, "create_pool", creator)
    with pytest.raises(ValueError, match=name):
        await db.init_db_pool()
    creator.assert_not_awaited()


@pytest.mark.parametrize("name", ["PG_STATEMENT_TIMEOUT_MS", "PG_LOCK_TIMEOUT_MS"])
async def test_custom_pool_query_limits_are_validated_before_leasing(monkeypatch, name):
    monkeypatch.setenv(name, "0")
    pool = Pool()
    with pytest.raises(ValueError, match=name):
        async with db_utils.acquire_with_tenant(pool, TENANT):
            pytest.fail("invalid timeout config reached caller")
    assert not pool.waiting.is_set()


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "inf", "invalid"])
def test_invalid_default_acquire_timeout_rejects_configuration(monkeypatch, value):
    monkeypatch.setenv("PG_POOL_ACQUIRE_TIMEOUT", value)
    with pytest.raises(ValueError, match="PG_POOL_ACQUIRE_TIMEOUT"):
        runpy.run_path(db.__file__)


async def test_raw_acquisition_preserves_caller_transaction_policy_and_releases():
    pool = Pool()
    async with db_utils.acquire_connection(pool) as conn:
        assert conn is pool.conn
        assert conn.events == []  # No SQL, RLS grant, or transaction opened.
        await asyncio.sleep(0.025)  # Acquisition deadline does not bound the body.
    assert pool.conn.events == []
    assert pool.releases == 1


async def test_raw_default_acquisition_is_bounded_and_compatible():
    pool = Pool()
    async with pool.acquire():
        async def waiter():
            async with db_utils.acquire_connection(pool):
                pytest.fail("saturated raw pool yielded a connection")

        with pytest.raises(db.DatabasePoolTimeoutError) as caught:
            await asyncio.wait_for(waiter(), 0.4)
        assert isinstance(caught.value, asyncio.TimeoutError)
        assert isinstance(caught.value, RuntimeError)
        assert pool.releases == 0
    assert pool.releases == 1


@pytest.mark.parametrize("exception_type", [asyncio.TimeoutError, asyncio.CancelledError])
async def test_raw_caller_exception_keeps_identity_and_releases(exception_type):
    pool = Pool()
    original = exception_type("synthetic caller failure")
    with pytest.raises(exception_type) as caught:
        async with db_utils.acquire_connection(pool):
            raise original
    assert caught.value is original
    assert not isinstance(caught.value, db.DatabasePoolTimeoutError)
    assert pool.context_exceptions == [original]
    assert pool.conn.events == []
    assert pool.releases == 1
