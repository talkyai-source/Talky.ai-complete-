"""QA-SEC-100: configured CAPTCHA reaches server-side enforcement."""
import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis
import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import importlib
from app.api.v1.endpoints.auth import password_reset as reset, signup
from app.api.v1.endpoints.auth.schemas import LoginRequest, SignupStartRequest
from app.core.security import auth_abuse

login = importlib.import_module("app.api.v1.endpoints.auth.login")


def request():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                    "query_string": b"", "client": ("203.0.113.99", 1234)})


@pytest.fixture
def guarded(monkeypatch):
    monkeypatch.setenv("AUTH_TURNSTILE_REQUIRED", "true")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "synthetic-secret")
    monkeypatch.setenv("TURNSTILE_ALLOWED_HOSTNAMES", "unit.invalid")
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)

    @asynccontextmanager
    async def forbidden_db(*args):
        raise AssertionError("Account lookup reached before CAPTCHA verification")
        yield

    monkeypatch.setattr(login, "_login_transaction", forbidden_db)
    monkeypatch.setattr(signup, "acquire_with_tenant", forbidden_db)
    monkeypatch.setattr(reset, "acquire_with_tenant", forbidden_db)
    monkeypatch.setattr(signup, "_get_redis_or_503", lambda: redis)
    monkeypatch.setattr(reset, "_get_redis_or_503", lambda: redis)


@pytest.mark.parametrize("route", ["login", "signup", "forgot"])
async def test_configured_bot_check_rejects_missing_token_before_account_lookup(guarded, route):
    db = SimpleNamespace(pool=None)
    with pytest.raises(HTTPException) as exc:
        if route == "login":
            await inspect.unwrap(login.login)(request(), LoginRequest(email="owner@example.com", password="old-password"),
                                               db, SimpleNamespace(log=AsyncMock()))
        elif route == "signup":
            await inspect.unwrap(signup.signup_start)(request(), SignupStartRequest(
                email="owner@example.com", name="Owner", business_name="Company"), db)
        else:
            await reset.forgot_password(request(), reset.ForgotPasswordRequest(email="owner@example.com"),
                                        db, SimpleNamespace(log=AsyncMock()))
    assert exc.value.status_code == 403


@pytest.mark.parametrize("schema,fields", [
    (LoginRequest, {"password": "legacy-password"}),
    (SignupStartRequest, {"name": "Owner", "business_name": "Company"}),
    (reset.ForgotPasswordRequest, {}),
])
def test_auth_contract_preserves_turnstile_token(schema, fields):
    body = schema(email="owner@example.com", turnstile_token="synthetic-token", **fields)
    assert body.turnstile_token == "synthetic-token"


async def test_signup_disposable_domain_is_rejected_before_lookup(monkeypatch):
    monkeypatch.delenv("AUTH_TURNSTILE_REQUIRED", raising=False)

    @asynccontextmanager
    async def forbidden_db(*args):
        raise AssertionError("Disposable address reached account lookup")
        yield

    monkeypatch.setattr(signup, "acquire_with_tenant", forbidden_db)
    with pytest.raises(HTTPException) as exc:
        await inspect.unwrap(signup.signup_start)(request(), SignupStartRequest(
            email="owner@mailinator.com", name="Owner", business_name="Company"), SimpleNamespace(pool=None))
    assert exc.value.status_code == 400


@pytest.fixture
def siteverify(guarded, monkeypatch):
    post = AsyncMock(return_value=httpx.Response(200, request=httpx.Request("POST", "https://unit.invalid"),
                                                json={"success": True, "hostname": "unit.invalid", "action": "login"}))
    client = SimpleNamespace(post=post)

    @asynccontextmanager
    async def mock_client(**kwargs):
        assert kwargs == {"timeout": 5.0, "trust_env": False}
        yield client

    monkeypatch.setattr(auth_abuse.httpx, "AsyncClient", mock_client)
    return post


async def test_valid_challenge_is_verified_once_and_bound_to_host_and_action(siteverify):
    await auth_abuse.verify_turnstile("synthetic-token", "203.0.113.99", "login")
    assert siteverify.await_count == 1
    assert siteverify.call_args.args == ("https://challenges.cloudflare.com/turnstile/v0/siteverify",)
    assert siteverify.call_args.kwargs["data"]["response"] == "synthetic-token"
    assert siteverify.call_args.kwargs["data"]["remoteip"] == "203.0.113.99"


@pytest.mark.parametrize("payload", [
    {"success": False, "error-codes": ["timeout-or-duplicate"]},
    {"success": True, "hostname": "other.invalid", "action": "login"},
    {"success": True, "hostname": "unit.invalid", "action": "signup"},
    {"success": "true", "hostname": "unit.invalid", "action": "login"},
    {"success": True, "hostname": [], "action": "login"},
    None,
])
async def test_failed_expired_replayed_or_wrong_scope_challenge_is_rejected(siteverify, payload):
    siteverify.return_value = httpx.Response(200, request=httpx.Request("POST", "https://unit.invalid"), json=payload)
    with pytest.raises(HTTPException) as exc:
        await auth_abuse.verify_turnstile("synthetic-token", "203.0.113.99", "login")
    assert exc.value.status_code in {403, 503}


async def test_verification_outage_fails_closed_without_leaking_exception(siteverify, caplog):
    siteverify.side_effect = httpx.ConnectTimeout("private token must not be logged")
    with pytest.raises(HTTPException) as exc:
        await auth_abuse.verify_turnstile("synthetic-token", "203.0.113.99", "login")
    assert exc.value.status_code == 503
    assert "private token" not in caplog.text


@pytest.mark.parametrize("missing", ["TURNSTILE_SECRET_KEY", "TURNSTILE_ALLOWED_HOSTNAMES"])
async def test_required_mode_cannot_silently_skip_incomplete_configuration(guarded, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(HTTPException) as exc:
        await auth_abuse.verify_turnstile("synthetic-token", "203.0.113.99", "login")
    assert exc.value.status_code == 503


async def test_signup_ip_daily_budget_survives_changing_email(monkeypatch):
    monkeypatch.delenv("AUTH_TURNSTILE_REQUIRED", raising=False)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    send = AsyncMock(return_value=True)

    @asynccontextmanager
    async def acquire(*args):
        yield SimpleNamespace(fetchrow=AsyncMock(return_value=None))

    monkeypatch.setattr(signup, "acquire_with_tenant", acquire)
    monkeypatch.setattr(signup, "_get_redis_or_503", lambda: redis)
    monkeypatch.setattr(signup, "get_email_service", lambda: SimpleNamespace(send_signup_code_email=send))
    for n in range(10):
        await inspect.unwrap(signup.signup_start)(request(), SignupStartRequest(
            email=f"owner{n}@example.com", name="Owner", business_name="Company"), SimpleNamespace(pool=None))
    with pytest.raises(HTTPException) as exc:
        await inspect.unwrap(signup.signup_start)(request(), SignupStartRequest(
            email="fresh@example.com", name="Owner", business_name="Company"), SimpleNamespace(pool=None))
    assert exc.value.status_code == 429
    assert send.await_count == 10


def test_disposable_subdomains_are_blocked_but_similar_permanent_domains_are_allowed():
    with pytest.raises(HTTPException):
        auth_abuse.reject_disposable_signup_email("owner@inbox.mailinator.com")
    auth_abuse.reject_disposable_signup_email("owner@notmailinator.com")
