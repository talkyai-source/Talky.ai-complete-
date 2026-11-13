"""F5-04: actual auth boundaries, synthetic current principal/session evidence."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import Depends, FastAPI, HTTPException, Request
import httpx
import pytest

from app.api.v1 import dependencies as dep
from app.core import config
from app.core.security import internal_auth, principal, sessions

USER = "00000000-0000-4000-8000-000000000011"
SESSION = "00000000-0000-4000-8000-000000000012"
TENANT = "00000000-0000-4000-8000-000000000013"


@pytest.fixture
def case(monkeypatch):
    # Import before patching config so the helper cannot retain a first-case
    # enabled lambda after fixture teardown and affect default-off callers.
    from app.core.security import platform_admin_mfa as policy

    # Defaults only: model_construct never reads an application .env file.
    settings = SimpleNamespace(**config.Settings.model_construct().model_dump())
    settings.platform_admin_mfa_required = True
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(policy, "get_settings", lambda: settings)
    application = FastAPI()
    row = {"id": USER, "email": "synthetic@example.invalid", "role": "platform_admin",
           "tenant_id": None, "name": "Synthetic", "business_name": None, "mfa_enabled": False}
    session = {"id": SESSION, "user_id": USER, "mfa_verified": False}
    claims = {"sub": USER, "sid": SESSION, "role": "platform_admin", "tenant_id": None}
    state = SimpleNamespace(fast_cookie=False)

    @application.middleware("http")
    async def cookie_middleware(request, call_next):
        if state.fast_cookie:
            # Models the existing middleware shortcut: only id/user_id are
            # trustworthy session lookup hints; this stale flag is not proof.
            request.state.session_id = SESSION
            request.state.session_user_id = USER
            request.state.session_mfa_verified = True
        return await call_next(request)

    @application.get("/session-probe")
    async def session_probe(user=Depends(dep.get_current_user)):
        return {"id": user.id, "role": user.role}

    @application.get("/admin-probe")
    async def admin_probe(user=Depends(dep.require_platform_admin)):
        return {"id": user.id}

    @asynccontextmanager
    async def connection(pool, tenant_id):
        assert tenant_id is None
        yield object()

    read_session = AsyncMock(side_effect=lambda *a, **kw: dict(session))
    read_principal = AsyncMock(side_effect=lambda *a, **kw: dict(row))
    monkeypatch.setattr(dep, "acquire_with_tenant", connection)
    monkeypatch.setattr(internal_auth, "acquire_with_tenant", connection)
    monkeypatch.setattr(dep, "get_db_client", lambda: SimpleNamespace(pool=object()))
    monkeypatch.setattr(dep, "get_session_by_id", read_session)
    monkeypatch.setattr(sessions, "get_session_by_id", read_session)
    monkeypatch.setattr(internal_auth, "get_session_by_id", read_session)
    monkeypatch.setattr(principal, "load_current_principal", read_principal)
    monkeypatch.setattr(internal_auth, "load_current_principal", read_principal)
    monkeypatch.setattr(dep, "decode_and_validate_token", lambda token: dict(claims))
    monkeypatch.setattr(internal_auth, "decode_and_validate_token", lambda token: dict(claims))
    cookie = AsyncMock(return_value={"id": SESSION, "user_id": USER})
    monkeypatch.setattr(dep, "validate_session", cookie)
    meter = AsyncMock(return_value=SimpleNamespace(allowance=lambda: {}))
    monkeypatch.setattr("app.services.scripts.tenant_minutes.compute_tenant_minutes_status", meter)
    publish = Mock(wraps=dep._publish_current_user_scope)
    monkeypatch.setattr(dep, "_publish_current_user_scope", publish)
    return SimpleNamespace(app=application, settings=settings, row=row, session=session, claims=claims,
                           state=state, sessions=read_session, account=read_principal, cookie=cookie,
                           meter=meter, publish=publish)


async def request(case, transport="bearer", path="/session-probe"):
    headers = {"Authorization": "Bearer synthetic-valid"} if transport == "bearer" else {}
    cookies = {"talky_at": "synthetic-valid"} if transport == "access_cookie" else {}
    if transport in {"legacy_cookie", "fast_cookie"}:
        cookies[dep.SESSION_COOKIE_NAME] = "synthetic-session"
        case.state.fast_cookie = transport == "fast_cookie"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app),
                                 base_url="https://synthetic.example.invalid", cookies=cookies) as client:
        return await client.get(path, headers=headers)


def test_mfa_policy_setting_exists_and_is_disabled_by_default():
    field = config.Settings.model_fields.get("platform_admin_mfa_required")
    assert field is not None, "F5-04 requires PLATFORM_ADMIN_MFA_REQUIRED as a default-false setting"
    assert field.default is False


@pytest.mark.parametrize("transport", ["bearer", "access_cookie", "legacy_cookie", "fast_cookie"])
async def test_enabled_policy_blocks_password_only_platform_admin_before_scope_publish(case, transport):
    response = await request(case, transport, "/admin-probe")
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "platform_admin_mfa_required"
    assert "set-cookie" not in response.headers, "a step-up denial must not erase unrelated session cookies"
    case.publish.assert_not_called()
    case.meter.assert_not_awaited()


@pytest.mark.parametrize("verified", [None, False, 0, 1, "true"])
async def test_only_strict_database_true_is_mfa_evidence(case, verified):
    case.session["mfa_verified"] = verified
    case.row["mfa_enabled"] = True
    case.claims.update(mfa_verified=True, amr=["pwd", "mfa"])
    response = await request(case)
    assert response.status_code == 403, "enrollment, truthy values and JWT claims are not session verification"
    case.publish.assert_not_called()


@pytest.mark.parametrize("transport", ["bearer", "access_cookie", "legacy_cookie", "fast_cookie"])
async def test_database_verified_second_factor_allows_current_admin(case, transport):
    case.session["mfa_verified"] = True
    response = await request(case, transport, "/admin-probe")
    assert response.status_code == 200, response.text
    case.sessions.assert_awaited_once()
    assert case.sessions.await_args.kwargs["user_id"] == USER
    assert case.sessions.await_args.args[1] == SESSION
    case.publish.assert_called_once()


async def test_fast_cookie_reloads_authoritative_mfa_flag_not_cached_middleware_hint(case):
    response = await request(case, "fast_cookie")
    assert response.status_code == 403
    case.cookie.assert_not_awaited()
    case.sessions.assert_awaited_once()
    assert case.sessions.await_args.kwargs["user_id"] == USER


@pytest.mark.parametrize("transport", ["bearer", "fast_cookie"])
async def test_flag_off_preserves_existing_admin_sessions(case, transport):
    case.settings.platform_admin_mfa_required = False
    response = await request(case, transport, "/admin-probe")
    assert response.status_code == 200, response.text
    if transport == "fast_cookie":
        case.sessions.assert_not_awaited(), "disabled rollout must not introduce extra legacy lookups"


async def test_current_role_promotion_requires_mfa_even_when_signed_role_is_ordinary(case):
    case.claims["role"] = "tenant_admin"
    response = await request(case)
    assert response.status_code == 403


@pytest.mark.parametrize("role", ["tenant_admin", "partner_admin", "user"])
async def test_current_nonplatform_roles_are_not_enrolled_by_this_flag(case, role):
    case.row["role"] = role
    response = await request(case)
    assert response.status_code == 200
    assert response.json()["role"] == role


async def test_session_verification_outage_is_unavailable_not_mfa_success(case):
    case.sessions.side_effect = ConnectionError("synthetic-private-storage-error")
    response = await request(case, "fast_cookie")
    assert response.status_code == 503
    assert "synthetic-private-storage-error" not in response.text
    case.publish.assert_not_called()


async def test_revoked_session_cannot_be_rescued_by_enrollment_or_jwt_mfa_claim(case):
    case.sessions.side_effect = None
    case.sessions.return_value = None
    case.row["mfa_enabled"] = True
    case.claims["mfa_verified"] = True
    response = await request(case)
    assert response.status_code == 401
    case.account.assert_not_awaited()


@pytest.mark.parametrize("verified", [False, True])
async def test_nonhttp_session_principal_uses_the_same_verified_mfa_rule(case, verified):
    case.session["mfa_verified"] = verified
    if verified:
        result = await principal.load_session_principal(object(), case.claims)
        assert result["id"] == USER
    else:
        with pytest.raises(principal.PrincipalUnavailable) as caught:
            await principal.load_session_principal(object(), case.claims)
        assert caught.value.code == "platform_admin_mfa_required"


async def test_flag_off_preserves_nonhttp_and_internal_user_session_paths(case):
    case.settings.platform_admin_mfa_required = False
    assert (await principal.load_session_principal(object(), case.claims))["id"] == USER
    req = Request({"type": "http", "method": "POST", "path": "/internal-shaped-user-route",
                   "headers": [(b"authorization", b"Bearer synthetic-valid")]})
    ctx = internal_auth.CallerContext(is_internal=False, tenant_id=TENANT)
    assert await internal_auth.require_active_session(req, ctx, db_pool=object()) is None


@pytest.mark.parametrize("verified", [False, True])
async def test_internal_auth_user_bearer_fallback_requires_mfa(case, verified):
    case.session["mfa_verified"] = verified
    req = Request({"type": "http", "method": "POST", "path": "/internal-shaped-user-route",
                   "headers": [(b"authorization", b"Bearer synthetic-valid")]})
    ctx = internal_auth.CallerContext(is_internal=False, tenant_id=TENANT)
    if verified:
        assert await internal_auth.require_active_session(req, ctx, db_pool=object()) is None
    else:
        with pytest.raises(HTTPException) as caught:
            await internal_auth.require_active_session(req, ctx, db_pool=object())
        assert caught.value.status_code == 403
        assert caught.value.detail["code"] == "platform_admin_mfa_required"


async def test_authenticated_service_identity_keeps_its_existing_nonuser_bypass(case, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "synthetic-service-token-only")
    req = Request({"type": "http", "method": "POST", "path": "/internal-service",
                   "headers": [(b"x-internal-service-token", b"synthetic-service-token-only")]})
    ctx = internal_auth.require_internal_or_tenant(req)
    assert ctx.is_internal is True
    assert await internal_auth.require_active_session(req, ctx, db_pool=object()) is None
    case.sessions.assert_not_awaited()
    case.account.assert_not_awaited()


def test_untrusted_internal_header_does_not_become_service_identity(case, monkeypatch):
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "synthetic-service-token-only")
    req = Request({"type": "http", "method": "POST", "path": "/internal-service",
                   "headers": [(b"x-internal-service-token", b"wrong-token")]})
    with pytest.raises(HTTPException) as caught:
        internal_auth.require_internal_or_tenant(req)
    assert caught.value.status_code == 401
