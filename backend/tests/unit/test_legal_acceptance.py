"""Legal-version acceptance is explicit, atomic and tenant-bound (QA F12-10)."""
from contextlib import asynccontextmanager
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.responses import Response

from app.api.v1.endpoints.auth import signup, profile
from app.api.v1.endpoints.auth.schemas import SignupCompleteRequest
from tests.unit.test_signup_complete_membership import _Connection, _Redis, _AuditLogger, TENANT_ID


@pytest.fixture
def flow(monkeypatch):
    from app.core import config
    from app.services.scripts import seed_platform_sip_trunk, tenant_minutes
    settings = SimpleNamespace(legal_acceptance_required=False,
                               legal_terms_version="terms-test-v1", legal_privacy_version="privacy-test-v1")
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    conn = _Connection()
    conn.legal_writes = []
    conn.committed = False
    conn.fail_receipt = False
    conn.receipt = None
    old_execute = conn.execute

    async def execute(sql, *args):
        if "INSERT INTO legal_acceptances" in sql:
            if conn.fail_receipt:
                raise RuntimeError("synthetic receipt outage")
            conn.legal_writes.append((sql, args))
        return await old_execute(sql, *args)

    conn.execute = execute
    old_fetch = conn.fetchrow

    async def fetchrow(sql, *args):
        if "FROM legal_acceptances" in sql:
            assert "tenant_id" in sql and "user_id" in sql
            assert TENANT_ID in [str(arg) for arg in args]
            return conn.receipt
        return await old_fetch(sql, *args)

    conn.fetchrow = fetchrow

    @asynccontextmanager
    async def acquire(pool, tenant_id):
        try:
            yield conn
        except BaseException:
            conn.statements.clear()
            conn.legal_writes.clear()
            raise
        else:
            conn.committed = True

    redis = _Redis({"code_hash": signup._hash_signup_code("123456"),
                    "name": "Example Owner", "business_name": "Example Co"})
    monkeypatch.setattr(signup, "_get_redis_or_503", lambda: redis)
    monkeypatch.setattr(signup, "acquire_with_tenant", acquire)
    monkeypatch.setattr(profile, "acquire_with_tenant", acquire)
    monkeypatch.setattr(signup, "validate_password_strength", lambda value: None)
    monkeypatch.setattr(signup, "hash_password", lambda value: "synthetic-hash")
    monkeypatch.setattr(signup, "create_session", AsyncMock(return_value=("session", "session-id")))
    monkeypatch.setattr(signup, "issue_cookie_auth", AsyncMock())
    monkeypatch.setattr(signup, "create_jwt", lambda *args: "synthetic-token")
    monkeypatch.setattr(signup, "set_session_cookie", lambda *args: None)
    monkeypatch.setattr(signup, "get_client_ip", lambda request: "192.0.2.1")
    monkeypatch.setattr(signup, "get_user_agent", lambda request: "synthetic-agent")
    monkeypatch.setattr(seed_platform_sip_trunk, "seed_for_tenant", AsyncMock())
    monkeypatch.setattr(tenant_minutes, "compute_tenant_minutes_status", AsyncMock(
        return_value=SimpleNamespace(allowance=lambda: {"minutes_remaining": 0, "minutes_state": "known"})))
    return SimpleNamespace(conn=conn, settings=settings, redis=redis)


def body(**extra):
    return SignupCompleteRequest(email="owner@example.com", code="123456",
                                 password="Synthetic-password-for-tests-42!",
                                 confirm_password="Synthetic-password-for-tests-42!", **extra)


async def complete(payload):
    return await inspect.unwrap(signup.signup_complete)(
        request=SimpleNamespace(), response=Response(), body=payload,
        db_client=SimpleNamespace(pool=object()), audit_logger=_AuditLogger())


async def test_missing_acceptance_is_refused_only_when_required(flow):
    flow.settings.legal_acceptance_required = True
    with pytest.raises(HTTPException) as caught:
        await complete(body())
    assert caught.value.status_code == 422
    assert not flow.conn.statements and not flow.redis.deleted


async def test_required_but_unconfigured_versions_refuse_signup(flow):
    flow.settings.legal_acceptance_required = True
    flow.settings.legal_terms_version = None
    with pytest.raises(HTTPException) as caught:
        await complete(body())
    assert caught.value.status_code == 503
    assert not flow.conn.statements


async def test_feature_flag_preserves_existing_signup(flow):
    result = await complete(body())
    assert result.access_token == "synthetic-token"
    assert flow.conn.legal_writes == []


async def test_signup_stores_explicit_versions_ip_and_agent_atomically(flow):
    await complete(body(terms_version="terms-test-v1", privacy_version="privacy-test-v1"))
    assert flow.conn.committed and len(flow.conn.legal_writes) == 1
    sql, args = flow.conn.legal_writes[0]
    assert "accepted_at" in sql and "NOW()" in sql
    assert {TENANT_ID, "terms-test-v1", "privacy-test-v1", "192.0.2.1", "synthetic-agent"} <= set(map(str, args))


async def test_receipt_failure_rolls_back_signup_without_issuing_cookies(flow):
    flow.conn.fail_receipt = True
    with pytest.raises(RuntimeError, match="synthetic receipt outage"):
        await complete(body(terms_version="terms-test-v1", privacy_version="privacy-test-v1"))
    assert not flow.conn.committed and not flow.conn.statements
    assert not flow.redis.deleted
    signup.issue_cookie_auth.assert_not_awaited()


@pytest.mark.parametrize("versions", [
    {"terms_version": "terms-test-v1"},
    {"terms_version": "old", "privacy_version": "privacy-test-v1"},
])
async def test_partial_or_stale_acceptance_is_not_recorded(flow, versions):
    with pytest.raises(HTTPException) as caught:
        await complete(body(**versions))
    assert caught.value.status_code == 422 and not flow.conn.statements


@pytest.mark.parametrize("receipt,required", [
    (None, True),
    ({"terms_version": "old", "privacy_version": "privacy-test-v1"}, True),
    ({"terms_version": "terms-test-v1", "privacy_version": "privacy-test-v1"}, False),
])
async def test_me_reports_reacceptance_for_current_versions(flow, receipt, required):
    flow.conn.receipt = receipt
    user = SimpleNamespace(id="22222222-2222-4222-8222-222222222222", tenant_id=TENANT_ID,
                           email="owner@example.com", name="Owner", business_name="Example",
                           role="tenant_admin", minutes_remaining=0, minutes_state="known")
    result = await profile.get_me(user, SimpleNamespace(pool=object()))
    assert result.legal_acceptance_required is required
    assert result.terms_version == "terms-test-v1"
    assert result.privacy_version == "privacy-test-v1"


def test_migration_preserves_receipts_and_the_linear_graph(monkeypatch):
    from importlib import import_module
    migration = import_module("Alembic.versions.0085_legal_acceptance")
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration.upgrade()
    sql = " ".join(statements)
    assert "CREATE TABLE IF NOT EXISTS legal_acceptances" in sql
    assert "BEFORE UPDATE OR DELETE OR TRUNCATE" in sql
    assert "FOR EACH STATEMENT" in sql and "RAISE EXCEPTION" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql and "WITH CHECK" in sql
    assert "::regclass" not in sql and "to_regclass(" in sql
    assert migration.down_revision == "0084_call_failure_lead_signals"
    assert len(migration.revision) <= 32
    statements.clear()
    migration.downgrade()
    assert "IF EXISTS (SELECT 1 FROM legal_acceptances" in statements[0]
    assert statements[0].index("RAISE EXCEPTION") < statements[0].index("DROP TABLE")


async def test_authenticated_reacceptance_appends_the_current_users_receipt(flow):
    from fastapi import FastAPI
    import httpx
    from app.api.v1.dependencies import get_current_user, get_db_client
    user = SimpleNamespace(id="22222222-2222-4222-8222-222222222222", tenant_id=TENANT_ID)
    app = FastAPI()
    app.include_router(profile.router, prefix="/auth")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_client] = lambda: SimpleNamespace(pool=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/auth/legal-acceptance", json={
            "terms_version": "terms-test-v1", "privacy_version": "privacy-test-v1",
        })
    assert response.status_code == 200, response.text
    assert len(flow.conn.legal_writes) == 1
    assert tuple(flow.conn.legal_writes[0][1][:2]) == (TENANT_ID, user.id)
