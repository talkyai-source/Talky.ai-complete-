"""Sign-in renewal through the real route, on the real rotation code (12).

Service level was already pinned by test_refresh_rotation.py
(test_a_concurrent_refresh_gets_a_sibling_instead_of_a_logout, test_reuse_detection_revokes_entire_family).
What was missing is the route's own answer: a second presentation inside the 10-second grace
returns 200 with a NEW cookie and the family stays alive; after the grace it is 401 and the family is revoked.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import get_db_client
from app.api.v1.endpoints.auth import refresh as ep
from app.core.security import refresh_tokens as rt
from app.core.security.cookies import REFRESH_COOKIE_NAME
from tests.unit.test_refresh_rotation import StubConn


class RouteConn(StubConn):
    async def fetchrow(self, sql, *args):
        norm = " ".join(sql.split())
        if norm.startswith("SELECT user_id,tenant_id FROM refresh_tokens"):
            for row in self.rows.values():
                if row["token_hash"] == args[0]:
                    return row
            return None
        return await super().fetchrow(sql, *args)


@pytest.fixture
def world(monkeypatch):
    conn = RouteConn()

    @asynccontextmanager
    async def acquire(pool, tenant, **kw):
        yield conn

    async def noop(*a, **k):
        return None

    async def principal(conn_, user_id, tenant_id=None):
        return {"id": str(user_id), "email": "u@example.com", "role": "admin", "tenant_id": None}

    async def alive(conn_, claims):
        return "sid-1", True

    monkeypatch.setattr(ep, "acquire_with_tenant", acquire)
    monkeypatch.setattr(ep, "lock_user_credential_issuance", noop)
    monkeypatch.setattr(ep, "load_current_principal", principal)
    monkeypatch.setattr(ep, "bind_refresh_to_session", alive)
    monkeypatch.setattr(ep, "encode_access_token", lambda **k: "jwt")
    app = FastAPI()
    app.include_router(ep.router, prefix="/auth")
    app.dependency_overrides[get_db_client] = lambda: __import__("types").SimpleNamespace(pool=object())
    try:
        from app.api.v1.endpoints.auth._shared import limiter
        app.state.limiter = limiter
    except Exception:  # pragma: no cover
        pass
    return app, conn


async def _post(app, token):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t.invalid") as c:
        c.cookies.set(REFRESH_COOKIE_NAME, token)
        return await c.post("/auth/refresh")


@pytest.mark.asyncio
async def test_second_presentation_inside_the_grace_gets_a_working_token_and_the_family_survives(world):
    app, conn = world
    raw, _tid, family = await rt.issue_initial_refresh_token(conn, user_id=str(uuid.uuid4()))
    first = await _post(app, raw)
    second = await _post(app, raw)  # a second tab, a moment later
    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    new_first = first.cookies.get(REFRESH_COOKIE_NAME)
    new_second = second.cookies.get(REFRESH_COOKIE_NAME)
    assert new_first and new_second and new_first != new_second and raw not in {new_first, new_second}
    assert all(r["revoked_at"] is None for r in conn.rows.values() if r["family_id"] == family)
    assert (await _post(app, new_second)).status_code == 200  # the sibling really works


@pytest.mark.asyncio
async def test_presentation_after_the_grace_is_401_and_the_whole_family_is_revoked(world):
    app, conn = world
    raw, token_id, family = await rt.issue_initial_refresh_token(conn, user_id=str(uuid.uuid4()))
    first = await _post(app, raw)
    assert first.status_code == 200
    conn.rows[token_id]["used_at"] -= timedelta(seconds=rt.REFRESH_REUSE_GRACE_SECONDS + 1)
    late = await _post(app, raw)
    assert late.status_code == 401
    family_rows = [r for r in conn.rows.values() if r["family_id"] == family]
    assert family_rows and all(r["revoked_at"] is not None for r in family_rows)
    assert any(r["revoked_reason"] == "reuse_detected" for r in family_rows)
    assert (await _post(app, first.cookies.get(REFRESH_COOKIE_NAME))).status_code == 401
