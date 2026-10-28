"""Normal-auth PostgreSQL proof for logout/refresh lock ordering.

The transport and database are real. The infrastructure connection observer
only pauses after real row locks; it never changes SQL/results or injects an
exception. No principal/JWT/password/session dependency is replaced.
"""
import asyncio
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar

import httpx
import pytest

from tests.integration.test_admin_real_auth_journey import API_ORIGIN, ORIGIN, journey_db

pytestmark = pytest.mark.integration
OPERATION = ContextVar("auth_lock_order_operation", default=None)
RUN_EVIDENCE = []


@pytest.mark.parametrize("logout_path", ["/auth/logout", "/auth/logout-all"])
async def test_real_refresh_and_logout_share_profile_fence(journey_db, monkeypatch, logout_path):  # noqa: F811 - imported pytest fixture
    from app.core import container, db as core_db
    from app.core.postgres_adapter import Client

    db = journey_db
    # This focused race observes every database error. Supply the optional
    # current-user meter's exact read prerequisites rather than ignoring its
    # permission fallback. The owned fixture removes and verifies these grants.
    async with db.pool.acquire() as connection:
        role = await connection.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    await db.owner.execute(f'GRANT SELECT ON billing_ledger,topup_orders,topup_packages TO "{role}"')
    refresh_locked = asyncio.Event()
    logout_at_boundary = asyncio.Event()
    release = asyncio.Event()
    observation = {"logout_path": logout_path, "boundaries": [], "sqlstates": [], "sql_errors": [], "http_statuses": []}
    RUN_EVIDENCE.append(observation)

    class ObservedConnection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        async def fetchrow(self, sql, *args, **kwargs):
            normalized = " ".join(sql.upper().split())
            operation = OPERATION.get()
            if operation == "logout" and "FROM USER_PROFILES" in normalized and "FOR UPDATE" in normalized:
                # Record entry before the real profile lock, which may wait
                # behind refresh. Never bypass or release that lock ourselves.
                observation["boundaries"].append("logout_profile_lock_entered")
                logout_at_boundary.set()
            try:
                result = await self.connection.fetchrow(sql, *args, **kwargs)
            except Exception as exc:
                if getattr(exc, "sqlstate", None):
                    observation["sqlstates"].append(exc.sqlstate)
                    observation["sql_errors"].append({"code": exc.sqlstate, "operation": operation,
                        "query_prefix": normalized[:180]})
                raise
            if (operation == "refresh" and "FROM REFRESH_TOKENS" in normalized
                    and "FOR UPDATE" in normalized and not refresh_locked.is_set()):
                observation["boundaries"].append("refresh_token_row_locked")
                refresh_locked.set()
                await asyncio.wait_for(release.wait(), 5)
            return result

        async def execute(self, sql, *args, **kwargs):
            normalized = " ".join(sql.upper().split())
            try:
                result = await self.connection.execute(sql, *args, **kwargs)
            except Exception as exc:
                if getattr(exc, "sqlstate", None):
                    observation["sqlstates"].append(exc.sqlstate)
                    observation["sql_errors"].append({"code": exc.sqlstate, "operation": OPERATION.get(),
                        "query_prefix": normalized[:180]})
                raise
            if (OPERATION.get() == "logout" and normalized.startswith("UPDATE SECURITY_SESSIONS")
                    and "REVOKED" in normalized and not logout_at_boundary.is_set()):
                observation["boundaries"].append("logout_session_row_locked_before_profile")
                logout_at_boundary.set()
                await asyncio.wait_for(release.wait(), 5)
            return result

    class ObservedPool:
        def __getattr__(self, name):
            return getattr(db.pool, name)

        @asynccontextmanager
        async def acquire(self, **kwargs):
            async with db.pool.acquire(**kwargs) as conn:
                yield ObservedConnection(conn)

    transport = httpx.ASGITransport(app=db.app, client=("127.0.0.1", 59102), raise_app_exceptions=False)
    options = {"transport": transport, "base_url": API_ORIGIN,
               "headers": {"Origin": ORIGIN, "User-Agent": "Synthetic-Auth-Lock-Order"}}
    async with httpx.AsyncClient(**options) as login:
        response = await login.post("/api/v1/auth/login", json={
            "email": f"{db.users[1]}@example.com", "password": db.password,
        })
        assert response.status_code == 200
        original_cookies = httpx.Cookies(login.cookies)
        assert {cookie.name for cookie in original_cookies.jar} >= {"talky_at", "talky_rt", "talky_sid"}

    observed_pool = ObservedPool()
    monkeypatch.setattr(container._container, "_db_pool", observed_pool)
    monkeypatch.setattr(container._container, "_db_client", Client(observed_pool))
    monkeypatch.setattr(core_db, "_pool", observed_pool)

    async def perform(client, operation, path):
        token = OPERATION.set(operation)
        try:
            return await client.post("/api/v1" + path)
        finally:
            OPERATION.reset(token)

    async with httpx.AsyncClient(**options, cookies=original_cookies) as refresher, \
            httpx.AsyncClient(**options, cookies=original_cookies) as logout:
        tasks = []
        try:
            tasks.append(asyncio.create_task(perform(refresher, "refresh", "/auth/refresh")))
            await asyncio.wait_for(refresh_locked.wait(), 5)
            tasks.append(asyncio.create_task(perform(logout, "logout", logout_path)))
            await asyncio.wait_for(logout_at_boundary.wait(), 5)
            release.set()
            responses = await asyncio.wait_for(asyncio.gather(*tasks), 10)
            observation["http_statuses"] = [value.status_code for value in responses]
            # Before the repair PostgreSQL resolves the real S->R / R->S
            # cycle with 40P01 and one HTTP500. No error is manufactured.
            assert observation["sqlstates"] == [], observation
            assert observation["http_statuses"] == [200, 200], observation
            assert "logout_profile_lock_entered" in observation["boundaries"]
            assert "logout_session_row_locked_before_profile" not in observation["boundaries"]

            live_sessions = await db.owner.fetchval(
                "SELECT count(*) FROM security_sessions WHERE user_id=$1 AND NOT revoked", db.users[1])
            live_refresh = await db.owner.fetchval(
                "SELECT count(*) FROM refresh_tokens WHERE user_id=$1 AND revoked_at IS NULL", db.users[1])
            observation["remaining_live_sessions"] = live_sessions
            observation["remaining_live_refresh"] = live_refresh
            assert live_sessions == live_refresh == 0
            assert (await refresher.get("/api/v1/auth/me")).status_code == 401
            rejected = await refresher.post("/api/v1/auth/refresh")
            assert rejected.status_code == 401 and "set-cookie" not in rejected.headers
            observation["issued_successor_denied_after_logout"] = True
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
