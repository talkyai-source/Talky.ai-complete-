"""Canonical request-scope publication with real ContextVars and synthetic auth ports.

These units exercise the actual dependencies, not PostgreSQL RLS or signed-token
verification. Separate normal-auth restricted-role HTTP evidence owns those seams.
"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, Request

from app.api.v1 import dependencies as dep
from app.core.jwt_security import JWTValidationError
from app.core.security import principal, tenant_isolation as scope


USER = "00000000-0000-4000-8000-000000000001"
TENANT = "00000000-0000-4000-8000-000000000002"
SESSION = "00000000-0000-4000-8000-000000000003"
OTHER = "00000000-0000-4000-8000-000000000004"
OLD = "00000000-0000-4000-8000-000000000005"


def request(headers=None):
    return Request({"type": "http", "method": "GET", "path": "/owned-scope-probe",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]})


def provisional(req):
    """Seed the same privileged claim-derived state the middleware could supply."""
    scope.set_current_tenant_id(OLD)
    scope.set_bypass_rls(True)
    scope.set_current_user_context(dep.CurrentUser(id=OLD, email="old@example.invalid",
                                                  tenant_id=OLD, role="platform_admin"))
    req.state.tenant_id = OLD
    req.state.user_id = OLD
    req.state.user_role = "platform_admin"
    req.state.authenticated_user_id = OLD
    req.state.authenticated_session_id = OLD
    req.state.cross_tenant_access = True
    req.state.target_tenant_id = OLD


def assert_empty(req):
    assert scope.get_current_tenant_id() is None
    assert scope.get_bypass_rls() is False
    assert scope.get_current_user_context() is None
    for field in ("tenant_id", "user_id", "user_role", "authenticated_user_id",
                  "authenticated_session_id", "target_tenant_id"):
        assert getattr(req.state, field, None) is None, field
    assert getattr(req.state, "cross_tenant_access", False) is False


def assert_published(req, user, *, cross=False):
    assert scope.get_current_tenant_id() == user.tenant_id
    assert scope.get_bypass_rls() is (user.role == "platform_admin")
    assert scope.get_current_user_context() is user
    assert req.state.tenant_id == user.tenant_id
    assert req.state.user_id == req.state.authenticated_user_id == user.id
    assert req.state.user_role == user.role
    assert req.state.authenticated_session_id == SESSION
    assert req.state.cross_tenant_access is cross
    assert req.state.target_tenant_id == (OTHER if cross else None)


@pytest.fixture
def boundary(monkeypatch):
    scope.clear_tenant_context()
    observed = []

    @asynccontextmanager
    async def connection(pool, tenant_id):
        # Privileged query credentials are synthetic; provisional request scope
        # must already be retired before even the canonical storage lookup.
        observed.append((scope.get_current_tenant_id(), scope.get_bypass_rls(), scope.get_current_user_context()))
        yield object()

    row = {"id": USER, "email": "current@example.invalid", "tenant_id": TENANT,
           "role": "platform_admin", "name": "Current", "business_name": "Owned"}
    session = {"id": SESSION, "user_id": USER}
    account = AsyncMock(return_value=row)
    sessions = AsyncMock(return_value=session)
    cookies = AsyncMock(return_value=session)
    client = Mock(return_value=SimpleNamespace(pool=object()))
    claims = {"sub": USER, "sid": SESSION, "tenant_id": TENANT, "role": "platform_admin"}
    decode = Mock(side_effect=lambda token: {**claims, "sub": OTHER} if token == "conflicting" else dict(claims))
    monkeypatch.setattr(dep, "acquire_with_tenant", connection)
    monkeypatch.setattr(dep, "get_db_client", client)
    monkeypatch.setattr(dep, "get_session_by_id", sessions)
    monkeypatch.setattr(dep, "_resolve_cookie_session", cookies)
    monkeypatch.setattr(dep, "decode_and_validate_token", decode)
    monkeypatch.setattr(principal, "load_current_principal", account)
    meter = AsyncMock(return_value=SimpleNamespace(allowance=lambda: {}))
    monkeypatch.setattr("app.services.scripts.tenant_minutes.compute_tenant_minutes_status", meter)
    yield SimpleNamespace(row=row, claims=claims, account=account, sessions=sessions,
                          cookies=cookies, client=client, decode=decode, meter=meter, observed=observed)
    scope.clear_tenant_context()


async def authenticate(req, *, authorization=None, access_cookie=None, session_cookie=None, optional=False):
    fn = dep.get_optional_user if optional else dep.get_current_user
    return await fn(request=req, authorization=authorization, access_cookie=access_cookie,
                    session_cookie=session_cookie)


@pytest.mark.parametrize("transport", ["bearer", "access_cookie", "legacy_cookie", "matching_access", "matching_all"])
async def test_canonical_platform_scope_is_transport_independent(boundary, transport):
    req = request({"X-Tenant-ID": OTHER})
    provisional(req)
    user = await authenticate(req,
        authorization="Bearer current" if transport in {"bearer", "matching_access", "matching_all"} else None,
        access_cookie="current" if transport in {"access_cookie", "matching_access", "matching_all"} else None,
        session_cookie="current-session" if transport in {"legacy_cookie", "matching_all"} else None)

    assert_published(req, user, cross=True)
    assert user.id == USER and user.tenant_id == TENANT
    assert boundary.observed and all(value == (None, False, None) for value in boundary.observed)
    boundary.account.assert_awaited_once()


async def test_null_primary_platform_user_replaces_previous_tenant(boundary):
    boundary.row["tenant_id"] = None
    boundary.claims["tenant_id"] = None
    req = request()
    provisional(req)

    user = await authenticate(req, access_cookie="current")

    assert_published(req, user)
    assert user.tenant_id is None


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin", "user", "readonly"])
async def test_current_demotion_overwrites_old_privileged_token_context(boundary, role):
    # The synthetic token still claims platform_admin; the verified row wins.
    boundary.row["role"] = role
    req = request({"X-Tenant-ID": OTHER})
    provisional(req)

    user = await authenticate(req, authorization="Bearer formerly-privileged")

    assert_published(req, user)
    assert user.role == role
    assert user.tenant_id == TENANT


@pytest.mark.parametrize("failure,status", [
    ("missing", 401), ("malformed", 401), ("invalid_jwt", 401),
    ("jwt_unavailable", 503), ("unbound_token", 401), ("conflicting_access", 409),
    ("revoked_session", 401), ("conflicting_legacy", 401),
    ("principal_restricted", 401), ("principal_unavailable", 503),
    ("expected_identity", 409), ("client_unavailable", 503),
])
async def test_failed_authentication_cannot_leave_provisional_scope(boundary, failure, status):
    req = request({"X-Talky-Expected-User": OTHER} if failure == "expected_identity" else {})
    provisional(req)
    kwargs = {"authorization": "Bearer current"}
    if failure == "missing":
        kwargs = {}
    elif failure == "malformed":
        kwargs["authorization"] = "Basic invalid"
    elif failure in {"invalid_jwt", "jwt_unavailable"}:
        boundary.decode.side_effect = JWTValidationError("synthetic verification failure", status_code=status)
    elif failure == "unbound_token":
        boundary.claims.pop("sid")
    elif failure == "conflicting_access":
        kwargs["access_cookie"] = "conflicting"
    elif failure == "revoked_session":
        boundary.sessions.return_value = None
    elif failure == "conflicting_legacy":
        kwargs["session_cookie"] = "different-session"
        boundary.cookies.return_value = {"id": OTHER, "user_id": USER}
    elif failure == "principal_restricted":
        boundary.account.side_effect = principal.PrincipalUnavailable("account_restricted")
    elif failure == "principal_unavailable":
        boundary.account.side_effect = TimeoutError("synthetic current-principal outage")
    elif failure == "client_unavailable":
        boundary.client.side_effect = RuntimeError("synthetic pool unavailable")

    with pytest.raises(HTTPException) as caught:
        await authenticate(req, **kwargs)

    assert caught.value.status_code == status
    assert_empty(req)
    if failure in {"missing", "malformed", "invalid_jwt", "jwt_unavailable", "unbound_token", "conflicting_access"}:
        boundary.client.assert_not_called()
    if failure != "expected_identity":
        boundary.meter.assert_not_awaited()


async def test_optional_anonymous_request_retires_scope_without_storage(boundary):
    req = request()
    provisional(req)

    assert await authenticate(req, optional=True) is None

    assert_empty(req)
    boundary.client.assert_not_called()


async def test_optional_rejected_authentication_does_not_reuse_previous_user(boundary):
    req = request()
    provisional(req)
    boundary.sessions.return_value = None

    assert await authenticate(req, authorization="Bearer revoked", optional=True) is None

    assert_empty(req)
    boundary.account.assert_not_awaited()


@pytest.mark.parametrize("failure", ["conflict", "unavailable"])
async def test_optional_legacy_fallback_is_anonymous_not_stale_authority(boundary, failure):
    # Preserve this dependency's existing optional/internal-auth fallback policy;
    # none of the rejected account's privileged scope may survive it.
    req = request({"X-Talky-Expected-User": OTHER} if failure == "conflict" else {})
    provisional(req)
    if failure == "unavailable":
        boundary.account.side_effect = TimeoutError("synthetic current-principal outage")

    assert await authenticate(req, access_cookie="current", optional=True) is None

    assert_empty(req)


@pytest.mark.parametrize("setter", ["set_current_tenant_id", "set_bypass_rls", "set_current_user_context"])
async def test_partial_scope_publication_failure_is_unavailable_and_resets_every_field(boundary, monkeypatch, setter):
    req = request({"X-Tenant-ID": OTHER})
    provisional(req)
    original = getattr(scope, setter)

    def fail_after_assignment(value):
        original(value)
        raise RuntimeError("synthetic context publication failure")

    monkeypatch.setattr(scope, setter, fail_after_assignment)
    with pytest.raises(HTTPException) as caught:
        await authenticate(req, access_cookie="current")

    assert caught.value.status_code == 503
    assert_empty(req)
    boundary.account.assert_awaited_once()


async def test_cancelled_canonical_read_does_not_leave_claim_authority(boundary):
    req = request()
    provisional(req)
    boundary.account.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await authenticate(req, access_cookie="current")

    assert_empty(req)


async def test_concurrent_dependency_tasks_keep_independent_canonical_scope(boundary):
    both = asyncio.Event()
    arrivals = 0

    async def canonical(conn, user_id, *, tenant_id=None):
        nonlocal arrivals
        arrivals += 1
        if arrivals == 2:
            both.set()
        await both.wait()
        return {**boundary.row, "id": user_id,
                "tenant_id": TENANT if user_id == USER else OTHER,
                "role": "platform_admin" if user_id == USER else "tenant_admin"}

    async def session(conn, session_id, *, user_id):
        return {"id": SESSION, "user_id": user_id}

    boundary.account.side_effect = canonical
    boundary.sessions.side_effect = session

    async def resolve(token):
        req = request()
        provisional(req)
        user = await authenticate(req, authorization=f"Bearer {token}")
        await asyncio.sleep(0)
        assert_published(req, user)
        return user.id, scope.get_current_tenant_id(), scope.get_bypass_rls()

    results = await asyncio.wait_for(asyncio.gather(resolve("current"), resolve("conflicting")), timeout=1)
    assert results == [(USER, TENANT, True), (OTHER, OTHER, False)]
