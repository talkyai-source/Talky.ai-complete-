"""Duplicate signup has a useful conflict response without mutating the account."""
import inspect
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asyncpg import UniqueViolationError
from starlette.responses import Response

from app.api.v1.endpoints.auth import signup
from app.api.v1.endpoints.auth.schemas import SignupCompleteRequest, SignupStartRequest
from app.core.errors import ApiError

MESSAGE = "You are already registered. Please sign in or try another email address."


class Connection:
    def __init__(self, *, existing=False, constraint=None):
        self.existing = existing
        self.constraint = constraint
        self.queries = []
        self.transaction_error = None

    @asynccontextmanager
    async def transaction(self):
        try:
            yield
        except Exception as exc:
            self.transaction_error = exc
            raise

    async def fetchrow(self, sql, *args):
        self.queries.append((sql, args))
        if "FROM user_profiles" in sql:
            assert "LOWER(email)" in sql
            assert args == ("owner@example.com",)
            return {"id": "existing"} if self.existing else None
        if "FROM plans" in sql:
            return {"id": "free", "minutes": 530}
        if "INSERT INTO tenants" in sql:
            return {"id": "11111111-1111-4111-8111-111111111111", "business_name": "Example", "minutes_allocated": 530}
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        return "22222222-2222-4222-8222-222222222222"

    async def execute(self, sql, *args):
        self.queries.append((sql, args))
        assert "INSERT INTO user_profiles" in sql
        error = UniqueViolationError("synthetic collision")
        error.constraint_name = self.constraint
        raise error


def arrange(monkeypatch, conn):
    @asynccontextmanager
    async def acquire(*args):
        yield conn
    monkeypatch.setattr(signup, "acquire_with_tenant", acquire)
    redis = SimpleNamespace(get=AsyncMock(return_value=json.dumps({"code_hash": signup._hash_signup_code("123456"), "name": "Owner", "business_name": "Example"})), delete=AsyncMock())
    monkeypatch.setattr(signup, "_get_redis_or_503", lambda: redis)
    monkeypatch.setattr(signup, "validate_password_strength", lambda _: None)
    monkeypatch.setattr(signup, "hash_password", lambda _: "fixture-hash")
    monkeypatch.setattr(signup, "get_client_ip", lambda _: "127.0.0.1")
    monkeypatch.setattr(signup, "get_user_agent", lambda _: "test")
    return redis


async def test_start_duplicate_normalizes_email_and_sends_no_code(monkeypatch):
    conn = Connection(existing=True)
    redis = arrange(monkeypatch, conn)
    monkeypatch.setattr(signup, "_get_redis_or_503", lambda: pytest.fail("Duplicate must not start OTP flow"))
    with pytest.raises(ApiError) as caught:
        await inspect.unwrap(signup.signup_start)(request=SimpleNamespace(), body=SignupStartRequest(email="Owner@Example.com", name="Owner", business_name="Example"), db_client=SimpleNamespace(pool=object()))
    assert caught.value.status_code == 409
    assert caught.value.detail == {"code": "email_already_registered", "message": MESSAGE}
    assert len(conn.queries) == 1
    redis.delete.assert_not_called()


@pytest.mark.parametrize("existing,constraint", [(True, None), (False, "user_profiles_email_key")])
async def test_complete_duplicate_or_concurrent_insert_rolls_back_and_does_not_login(monkeypatch, existing, constraint):
    conn = Connection(existing=existing, constraint=constraint)
    redis = arrange(monkeypatch, conn)
    create_session = AsyncMock()
    monkeypatch.setattr(signup, "create_session", create_session)
    audit = SimpleNamespace(log=AsyncMock())
    with pytest.raises(ApiError) as caught:
        await inspect.unwrap(signup.signup_complete)(request=SimpleNamespace(), response=Response(), body=SignupCompleteRequest(email="Owner@Example.com", code="123456", password="Valid-example-password-42!", confirm_password="Valid-example-password-42!"), db_client=SimpleNamespace(pool=object()), audit_logger=audit)
    assert caught.value.status_code == 409
    assert caught.value.detail["message"] == MESSAGE
    assert not any("INSERT INTO tenant_users" in sql for sql, _ in conn.queries)
    if constraint:
        assert conn.transaction_error is caught.value
    else:
        assert not any("INSERT INTO tenants" in sql for sql, _ in conn.queries)
    create_session.assert_not_called()
    audit.log.assert_not_called()
    redis.delete.assert_not_called()


async def test_unrelated_unique_constraint_is_not_reported_as_duplicate_email(monkeypatch):
    conn = Connection(constraint="user_profiles_pkey")
    arrange(monkeypatch, conn)
    with pytest.raises(UniqueViolationError):
        await inspect.unwrap(signup.signup_complete)(request=SimpleNamespace(), response=Response(), body=SignupCompleteRequest(email="Owner@Example.com", code="123456", password="Valid-example-password-42!", confirm_password="Valid-example-password-42!"), db_client=SimpleNamespace(pool=object()), audit_logger=SimpleNamespace(log=AsyncMock()))
