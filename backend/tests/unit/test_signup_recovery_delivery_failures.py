"""Signup and password-recovery outcomes when mail, Redis or audit fail.

Ported 2026-10-27 from the local customer-onboarding package, onto the
current paid-only signup:
  * a mail-service exception or Redis write failure during /signup/start was a
    raw 500; it is the same "could not confirm sending" 503 as a refused send;
  * an audit-log outage after /signup/complete committed reported a failed
    signup, so the customer retried and was told the email was taken;
  * /forgot-password answered 500 when Redis or the mail service failed, but
    only for registered emails, which told an attacker the account existed.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import inspect
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response

from app.api.v1.endpoints.auth import password_reset as reset
from app.api.v1.endpoints.auth import signup
from app.api.v1.endpoints.auth.schemas import SignupCompleteRequest, SignupStartRequest
from app.services.scripts import seed_platform_sip_trunk, tenant_minutes

TENANT = "11111111-1111-4111-8111-111111111111"
ROLE = "22222222-2222-4222-8222-222222222222"
EMAIL = "owner@example.com"
CODE = "123456"


class Redis:
    def __init__(self):
        self.values = {}
        self.fail_write = False
        self.counter = 0

    async def setex(self, key, ttl, value):
        if self.fail_write:
            raise ConnectionError("synthetic redis outage")
        self.values[key] = value

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)

    async def eval(self, script, numkeys, *args):
        return 1  # send / attempt claims under their caps

    async def incr(self, key):
        self.counter += 1
        return self.counter

    async def expire(self, *args):
        return True


class Connection:
    def __init__(self, *, user_exists=False):
        self.user_exists = user_exists
        self.tenant_insert_args = None

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchrow(self, sql, *args):
        if "FROM user_profiles" in sql:
            if self.user_exists:
                return {"id": "existing-user", "password_hash": "synthetic-hash"}
            return None
        if "INSERT INTO tenants" in sql:
            self.tenant_insert_args = (sql, args)
            return {"id": TENANT, "business_name": "Example", "minutes_allocated": 0}
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        assert "FROM roles" in sql
        return ROLE

    async def execute(self, sql, *args):
        return "INSERT 0 1"


def _acquire_for(conn):
    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn
    return acquire


@pytest.fixture
def signup_flow(monkeypatch):
    redis, conn = Redis(), Connection()
    send = AsyncMock(return_value=True)
    audit = SimpleNamespace(log=AsyncMock())

    async def session(db, **kwargs):
        return "synthetic-session", "synthetic-session-id"

    async def cookies(target, db, **kwargs):
        target.set_cookie("synthetic-access", "synthetic-token")

    meter = SimpleNamespace(allowance=lambda: {"minutes_remaining": 0})
    monkeypatch.setattr(signup, "_get_redis_or_503", lambda: redis)
    monkeypatch.setattr(signup, "get_email_service", lambda: SimpleNamespace(send_signup_code_email=send))
    monkeypatch.setattr(signup, "acquire_with_tenant", _acquire_for(conn))
    monkeypatch.setattr(signup, "validate_password_strength", lambda _: None)
    monkeypatch.setattr(signup, "hash_password", lambda _: "synthetic-hash")
    monkeypatch.setattr(signup, "create_session", session)
    monkeypatch.setattr(signup, "create_jwt", lambda *args: "synthetic-jwt")
    monkeypatch.setattr(signup, "issue_cookie_auth", cookies)
    monkeypatch.setattr(signup, "set_session_cookie", lambda target, token: target.set_cookie("synthetic-session", token))
    monkeypatch.setattr(signup, "get_client_ip", lambda _: "127.0.0.1")
    monkeypatch.setattr(signup, "get_user_agent", lambda _: "synthetic")
    monkeypatch.setattr(seed_platform_sip_trunk, "seed_for_tenant", AsyncMock())
    monkeypatch.setattr(tenant_minutes, "compute_tenant_minutes_status", AsyncMock(return_value=meter))
    return SimpleNamespace(redis=redis, conn=conn, send=send, audit=audit, response=Response())


async def _start():
    return await inspect.unwrap(signup.signup_start)(
        request=SimpleNamespace(),
        body=SignupStartRequest(email="Owner@example.com", name="Owner", business_name="Example"),
        db_client=SimpleNamespace(pool=object()),
    )


async def _complete(flow):
    await flow.redis.setex(signup._signup_redis_key(EMAIL), 900, json.dumps({
        "name": "Owner", "business_name": "Example", "email": EMAIL,
        "code_hash": signup._hash_signup_code(CODE),
    }))
    return await inspect.unwrap(signup.signup_complete)(
        request=SimpleNamespace(), response=flow.response,
        body=SignupCompleteRequest(email=EMAIL, code=CODE, password="Synthetic#Pass123", confirm_password="Synthetic#Pass123"),
        db_client=SimpleNamespace(pool=object()), audit_logger=flow.audit,
    )


async def test_mail_service_exception_is_a_clear_503_not_a_raw_500(signup_flow):
    signup_flow.send.side_effect = RuntimeError("synthetic SMTP failure")
    with pytest.raises(HTTPException) as failure:
        await _start()
    assert failure.value.status_code == 503
    assert "could not confirm sending" in failure.value.detail


async def test_redis_write_failure_is_503_and_sends_no_unusable_code(signup_flow):
    signup_flow.redis.fail_write = True
    with pytest.raises(HTTPException) as failure:
        await _start()
    assert failure.value.status_code == 503
    signup_flow.send.assert_not_awaited()


async def test_audit_outage_after_commit_still_reports_the_created_account(signup_flow):
    signup_flow.audit.log.side_effect = RuntimeError("synthetic audit outage")
    result = await _complete(signup_flow)
    assert result.message == "Account created successfully."
    assert len(signup_flow.response.headers.getlist("set-cookie")) == 2


async def test_new_accounts_still_get_no_plan_and_no_minutes(signup_flow):
    """Guard for the 2026-10-27 paid-only rule: the onboarding package's
    signup inserted the free plan with its minutes; that must never return."""
    await _complete(signup_flow)
    sql, args = signup_flow.conn.tenant_insert_args
    assert "VALUES ($1, NULL, 0, 0)" in " ".join(sql.split())
    assert args == ("Example",)


@pytest.fixture
def recovery(monkeypatch):
    redis, send = Redis(), AsyncMock(return_value=True)
    state = SimpleNamespace(conn=Connection(user_exists=True))
    monkeypatch.setattr(reset, "_get_redis_or_503", lambda: redis)
    monkeypatch.setattr(reset, "get_email_service", lambda: SimpleNamespace(send_password_reset_email=send))
    monkeypatch.setattr(reset, "acquire_with_tenant", lambda pool, tenant: _acquire_for(state.conn)(pool, tenant))
    return SimpleNamespace(redis=redis, send=send, state=state)


async def _forgot():
    return await reset.forgot_password(
        SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1")),
        reset.ForgotPasswordRequest(email=EMAIL),
        SimpleNamespace(pool=object()),
        SimpleNamespace(log=AsyncMock()),
    )


@pytest.mark.parametrize("outcome", ["sent", "missing", "declined", "mail_exception", "redis_failure"])
async def test_forgot_password_answers_identically_whatever_fails(recovery, outcome):
    if outcome == "missing":
        recovery.state.conn = Connection(user_exists=False)
    elif outcome == "declined":
        recovery.send.return_value = False
    elif outcome == "mail_exception":
        recovery.send.side_effect = RuntimeError("synthetic SMTP failure")
    elif outcome == "redis_failure":
        recovery.redis.fail_write = True
    result = await _forgot()
    recovery.state.conn = Connection(user_exists=False)
    assert result == await _forgot(), "the answer must not reveal whether the account exists"
