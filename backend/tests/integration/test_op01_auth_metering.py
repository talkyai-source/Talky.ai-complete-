"""Canonical allowance is optional display data, not credential authority.

Actual migrated PostgreSQL with one restricted pool slot; crypto is synthetic.
No mailbox, identity provider or telephone operations are performed.
"""
import asyncio
import os
from contextlib import asynccontextmanager

import asyncpg
import pytest
from fastapi import Response

from app.api.v1.endpoints.auth import _shared as auth_shared
from app.api.v1.endpoints.mfa import verify as mfa
from app.api.v1.endpoints.mfa.schemas import MFAChallengeVerifyRequest
from tests.integration.test_cp08_current_identity import identity_db, login_ep  # noqa: F401
from tests.integration.test_cp08_credential_transactions import ready_mfa, request

pytestmark = pytest.mark.integration


@asynccontextmanager
async def one_slot(db):
    async def init(conn):
        await conn.execute(f'SET ROLE "{db.role}"')

    pool = await asyncpg.create_pool(
        os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, init=init,
    )
    previous = db.client.pool
    db.client.pool = pool
    try:
        assert pool.get_max_size() == 1
        yield pool
    finally:
        db.client.pool = previous
        await pool.close()


async def configure_meter(db, unavailable):
    # A stale historical counter must not become the displayed balance.
    await db.admin.execute(
        "UPDATE tenants SET minutes_allocated=120,minutes_used=119 WHERE id=$1",
        db.tenants[0],
    )
    if unavailable:
        # Allow entitlement/principal reads while making the actual usage SQL
        # fail. PostgreSQL aborts its transaction until a rollback/savepoint.
        await db.admin.execute(f'REVOKE SELECT ON calls FROM "{db.role}"')


def assert_allowance(result, unavailable):
    assert result["minutes_state"] == ("unavailable" if unavailable else "known")
    assert result["minutes_remaining"] == (None if unavailable else 120)


@pytest.mark.parametrize("unavailable", [False, True])
async def test_password_login_one_slot_preserves_atomic_issuance_when_usage_fails(
    identity_db, monkeypatch, unavailable,  # noqa: F811 - imported shared fixture
):
    db = identity_db
    await configure_meter(db, unavailable)
    await db.admin.execute(
        "UPDATE user_profiles SET password_hash='synthetic' WHERE id=$1", db.users[0],
    )
    monkeypatch.setattr(login_ep, "verify_password", lambda *args: True)
    monkeypatch.setattr(login_ep, "rehash_if_needed", lambda *args: None)
    monkeypatch.setattr(login_ep, "create_jwt", lambda *args: "synthetic-access")
    monkeypatch.setattr(auth_shared, "encode_access_token", lambda **kw: "synthetic-access")
    async with one_slot(db):
        result = await asyncio.wait_for(db.http.post(
            "/auth/login",
            json={"email": f"{db.users[0]}@example.com", "password": "synthetic-password"},
        ), timeout=5)
    assert result.status_code == 200
    assert result.json()["tenant_id"] == str(db.tenants[0])
    assert_allowance(result.json(), unavailable)
    assert len(result.headers.get_list("set-cookie")) == 3
    assert await db.admin.fetchval(
        "SELECT COUNT(*) FROM security_sessions WHERE user_id=$1", db.users[0],
    ) == 2  # Existing fixture session plus exactly one new login.
    assert await db.admin.fetchval(
        "SELECT COUNT(*) FROM refresh_tokens WHERE user_id=$1", db.users[0],
    ) == 1
    assert await db.admin.fetchval(
        "SELECT COUNT(*) FROM login_attempts WHERE user_id=$1 AND success", db.users[0],
    ) == 1


@pytest.mark.parametrize("unavailable", [False, True])
async def test_mfa_one_slot_reads_allowance_only_after_factor_and_session_commit(
    identity_db, monkeypatch, unavailable,  # noqa: F811 - imported shared fixture
):
    db = identity_db
    await db.admin.execute(
        f'GRANT SELECT,INSERT,UPDATE ON user_mfa,mfa_challenges TO "{db.role}"',
    )
    await configure_meter(db, unavailable)
    challenge, step = await ready_mfa(db, monkeypatch)
    response = Response()
    async with one_slot(db):
        result = await asyncio.wait_for(mfa.verify_mfa_challenge(
            request(), response,
            MFAChallengeVerifyRequest(challenge_token=challenge, code="123456"),
            db.client,
        ), timeout=5)
    assert result.tenant_id == str(db.tenants[0])
    assert result.mfa_verified is True
    assert_allowance(result.model_dump(), unavailable)
    assert len(response.headers.getlist("set-cookie")) == 3
    assert await db.admin.fetchval(
        "SELECT used FROM mfa_challenges WHERE user_id=$1", db.users[0],
    ) is True
    assert await db.admin.fetchval(
        "SELECT last_used_at FROM user_mfa WHERE user_id=$1", db.users[0],
    ) == step
    assert await db.admin.fetchval(
        "SELECT COUNT(*) FROM security_sessions WHERE user_id=$1", db.users[0],
    ) == 2
    assert await db.admin.fetchval(
        "SELECT COUNT(*) FROM refresh_tokens WHERE user_id=$1", db.users[0],
    ) == 1
