"""Regression coverage for the verified-signup dashboard handoff.

The September 2 production signup created ``user_profiles`` but omitted the
matching ``tenant_users`` membership. Authentication therefore succeeded while
RBAC-protected dashboard reads failed. The profile and membership must be
created in the same transaction, and the response must expose the canonical
role used by the JWT.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import inspect
import json
from types import SimpleNamespace

from starlette.responses import Response

from app.api.v1.endpoints.auth import signup
from app.api.v1.endpoints.auth.schemas import SignupCompleteRequest
from app.services.scripts import seed_platform_sip_trunk


TENANT_ID = "11111111-1111-4111-8111-111111111111"
ROLE_ID = "22222222-2222-4222-8222-222222222222"


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Connection:
    def __init__(self):
        self.statements: list[str] = []

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, query: str, *args):
        normalized = " ".join(query.split())
        if "FROM plans" in normalized:
            return {"id": "free", "minutes": 530}
        if "FROM user_profiles" in normalized:
            return None
        if "INSERT INTO tenants" in normalized:
            return {
                "id": TENANT_ID,
                "business_name": "Example Co",
                "minutes_allocated": 530,
            }
        raise AssertionError(f"Unexpected fetchrow: {normalized}")

    async def fetchval(self, query: str, *args):
        assert "FROM roles" in query
        return ROLE_ID

    async def execute(self, query: str, *args):
        self.statements.append(" ".join(query.split()))
        return "INSERT 0 1"


class _Redis:
    def __init__(self, payload: dict[str, str]):
        self.payload = json.dumps(payload).encode()
        self.deleted: list[str] = []

    async def get(self, key: str):
        return self.payload

    async def delete(self, key: str):
        self.deleted.append(key)


class _AuditLogger:
    def __init__(self):
        self.events: list[dict[str, object]] = []

    async def log(self, **event):
        self.events.append(event)


async def test_signup_creates_primary_membership_and_returns_canonical_role(monkeypatch):
    code = "123456"
    redis = _Redis(
        {
            "code_hash": signup._hash_signup_code(code),
            "name": "Example Owner",
            "business_name": "Example Co",
        }
    )
    conn = _Connection()
    scopes: list[str | None] = []

    @asynccontextmanager
    async def acquire(_pool, tenant_id):
        scopes.append(tenant_id)
        yield conn

    async def create_session(*args, **kwargs):
        return "raw-session", "session-id"

    async def issue_cookie_auth(*args, **kwargs):
        return None

    async def seed_for_tenant(*args, **kwargs):
        return None

    monkeypatch.setattr(signup, "_get_redis_or_503", lambda: redis)
    monkeypatch.setattr(signup, "acquire_with_tenant", acquire)
    monkeypatch.setattr(signup, "validate_password_strength", lambda _password: None)
    monkeypatch.setattr(signup, "hash_password", lambda _password: "hashed")
    monkeypatch.setattr(signup, "create_session", create_session)
    monkeypatch.setattr(signup, "issue_cookie_auth", issue_cookie_auth)
    monkeypatch.setattr(signup, "create_jwt", lambda *args: "access-token")
    monkeypatch.setattr(signup, "set_session_cookie", lambda *args: None)
    monkeypatch.setattr(signup, "get_client_ip", lambda _request: "127.0.0.1")
    monkeypatch.setattr(signup, "get_user_agent", lambda _request: "test-agent")
    monkeypatch.setattr(seed_platform_sip_trunk, "seed_for_tenant", seed_for_tenant)

    audit = _AuditLogger()
    body = SignupCompleteRequest(
        email="owner@example.com",
        code=code,
        password="A-valid-test-password-42!",
        confirm_password="A-valid-test-password-42!",
    )

    # SlowAPI preserves the original callable via __wrapped__. Unwrap it so
    # this unit test exercises account creation without a rate-limit backend.
    handler = inspect.unwrap(signup.signup_complete)
    result = await handler(
        request=SimpleNamespace(),
        response=Response(),
        body=body,
        db_client=SimpleNamespace(pool=object()),
        audit_logger=audit,
    )

    profile_index = next(i for i, sql in enumerate(conn.statements) if "INSERT INTO user_profiles" in sql)
    membership_index = next(i for i, sql in enumerate(conn.statements) if "INSERT INTO tenant_users" in sql)

    assert profile_index < membership_index
    assert "is_primary, status, joined_at" in conn.statements[membership_index]
    assert scopes == [None, TENANT_ID]
    assert result.role == "tenant_admin"
    assert result.access_token == "access-token"
    assert redis.deleted == [signup._signup_redis_key("owner@example.com")]
    assert len(audit.events) == 1
