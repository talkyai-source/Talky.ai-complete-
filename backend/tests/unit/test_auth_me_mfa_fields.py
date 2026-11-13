"""G2: /auth/me says whether MFA is on and whether this session completed it.

The Admin app refuses password-only sign-ins in the browser only. It had no
server answer for "is MFA enabled" or "did this session pass the second
factor". The session row the auth dependency already loads carries
``mfa_verified``; the profile row carries ``mfa_enabled``.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import re

from fastapi import FastAPI, Request
import httpx
import pytest

from app.api.v1.endpoints.auth import profile
from tests.unit.test_auth_canonical_scope import (  # noqa: F401  (fixture)
    SESSION, USER, authenticate, boundary, provisional, request,
)


@pytest.mark.parametrize("stored,expected", [(True, True), (False, False)])
async def test_bearer_auth_publishes_this_sessions_factor_evidence(boundary, stored, expected):
    boundary.sessions.return_value = {"id": SESSION, "user_id": USER, "mfa_verified": stored}
    req = request()
    await authenticate(req, authorization="Bearer current")
    assert req.state.session_mfa_verified is expected


async def test_a_session_without_the_factor_column_is_unknown_not_false(boundary):
    # The legacy cookie path can supply a session without the factor column.
    req = request()
    await authenticate(req, session_cookie="current-session")
    assert req.state.session_mfa_verified is None


async def test_a_failed_authentication_cannot_keep_an_earlier_factor_claim(boundary):
    boundary.sessions.return_value = None
    req = request()
    provisional(req)
    req.state.session_mfa_verified = True
    with pytest.raises(Exception):
        await authenticate(req, authorization="Bearer current")
    assert getattr(req.state, "session_mfa_verified", None) is None


TENANT = "aabbccdd-1234-4abc-8def-0123456789ab"
ACTOR = "bbccddee-1234-4abc-8def-0123456789ab"


def _mounted(monkeypatch, *, mfa_enabled, session_mfa_verified):
    class Connection:
        async def fetchrow(self, query, *args):
            row = {"tenant_id": TENANT, "partner_id": None, "tenant_status": "active",
                   "partner_status": None, "tenant_suspended_at": None,
                   "tenant_suspension_reason": None, "partner_suspended_at": None,
                   "partner_suspension_reason": None, "account_timezone": None,
                   "subscription_status": "active"}
            if re.search(r"up\.mfa_enabled\s+AS\s+mfa_enabled", query, re.I):
                row["mfa_enabled"] = mfa_enabled
            return row

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        yield Connection()

    async def acceptance_status(conn, *, tenant_id, user_id):
        return {"legal_acceptance_required": False}

    monkeypatch.setattr(profile, "acquire_with_tenant", acquire)
    monkeypatch.setattr("app.domain.services.legal_acceptance.acceptance_status", acceptance_status)
    user = SimpleNamespace(id=ACTOR, tenant_id=TENANT, email="admin@example.test", name="Admin",
                           business_name="Synthetic", role="platform_admin", minutes_remaining=None,
                           minutes_state="unavailable")
    app = FastAPI()

    @app.middleware("http")
    async def factor_state(req: Request, call_next):
        # Stands in for get_current_user's publication of the session row.
        req.state.session_mfa_verified = session_mfa_verified
        return await call_next(req)

    app.include_router(profile.router, prefix="/api/v1/auth")
    app.dependency_overrides[profile.get_current_user] = lambda: user
    app.dependency_overrides[profile.get_db_client] = lambda: SimpleNamespace(pool=object())
    return app


@pytest.mark.parametrize("enabled,verified", [(True, True), (True, False), (False, False), (True, None)])
async def test_auth_me_reports_mfa_enabled_and_this_sessions_verification(monkeypatch, enabled, verified):
    app = _mounted(monkeypatch, mfa_enabled=enabled, session_mfa_verified=verified)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic.invalid") as client:
        response = await client.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mfa_enabled"] is enabled
    assert body["mfa_verified"] is verified


def test_get_me_keeps_its_pre_g2_call_shape():
    """G2 put ``request`` first, which broke every direct (current_user,
    db_client) caller. It is last and optional; FastAPI still injects it."""
    import inspect

    from fastapi.dependencies.utils import get_dependant

    params = list(inspect.signature(profile.get_me).parameters)
    assert params[:2] == ["current_user", "db_client"]
    assert get_dependant(path="/me", call=profile.get_me).request_param_name == "request"
