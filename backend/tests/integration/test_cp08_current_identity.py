"""CP08 current authority on the actual migrated disposable public schema.

The JWT decoder is a deterministic synthetic boundary; session, membership,
permission and tenant queries below are actual PostgreSQL under NOBYPASSRLS.
No external identity provider, mailbox or device is exercised.
"""

import os
import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, WebSocketDisconnect

from app.api.v1 import dependencies as dep
from app.api.v1.endpoints.rbac import tenant_users
from app.core import container
from app.core.security.rbac import Permission, require_permission, get_user_permissions
from app.core.security.refresh_tokens import issue_initial_refresh_token

login_ep = importlib.import_module("app.api.v1.endpoints.auth.login")
refresh_ep = importlib.import_module("app.api.v1.endpoints.auth.refresh")
sessions_ep = importlib.import_module("app.api.v1.endpoints.auth.sessions")
registration_ep = importlib.import_module("app.api.v1.endpoints.auth.registration")
signup_ep = importlib.import_module("app.api.v1.endpoints.auth.signup")
admin_users = importlib.import_module("app.api.v1.endpoints.admin.users")

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def identity_db(monkeypatch):
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    assert parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.endswith("_test")
    admin = await asyncpg.connect(dsn)
    role = "cp08_" + uuid4().hex
    tenants, users, sessions = ([uuid4(), uuid4()] for _ in range(3))
    pool = None
    seeded_free = None
    try:
        seeded_free = await admin.fetchval(
            "INSERT INTO plans(id,name,price,minutes) VALUES('free','Synthetic free fixture',0,100) ON CONFLICT(id) DO NOTHING RETURNING id"
        )
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(
            f'GRANT SELECT ON tenants,user_profiles,tenant_users,roles,permissions,role_permissions,user_permissions,calls,call_legs TO "{role}"'
        )
        await admin.execute(
            f'GRANT SELECT,INSERT,UPDATE ON security_sessions,refresh_tokens,login_attempts TO "{role}"'
        )
        await admin.execute(
            f'GRANT UPDATE,INSERT ON user_profiles,tenants,tenant_users TO "{role}"'
        )
        await admin.execute(f'GRANT SELECT ON plans TO "{role}"')
        for tenant, user, session in zip(tenants, users, sessions):
            await admin.execute(
                "INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic CP08')", tenant
            )
            await admin.execute(
                """INSERT INTO user_profiles(id,email,tenant_id,role,is_verified,email_verified_at,is_active)
                VALUES($1,$2,$3,'tenant_admin',TRUE,NOW(),TRUE)""",
                user,
                f"{user}@example.com",
                tenant,
            )
            await admin.execute(
                """INSERT INTO tenant_users(user_id,tenant_id,role_id,status,is_primary)
                SELECT $1,$2,id,'active',TRUE FROM roles WHERE name='tenant_admin'""",
                user,
                tenant,
            )
            await admin.execute(
                """INSERT INTO security_sessions(id,user_id,session_token_hash,expires_at)
                VALUES($1,$2,$3,$4)""",
                session,
                user,
                str(uuid4()),
                datetime.now(timezone.utc) + timedelta(hours=1),
            )

        async def init(conn):
            await conn.execute(f'SET ROLE "{role}"')

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, init=init)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            assert tuple(flags.values()) == (False, False)
        db = SimpleNamespace(
            admin=admin, pool=pool, tenants=tenants, users=users, sessions=sessions, role=role
        )
        db.client = SimpleNamespace(pool=pool)
        monkeypatch.setattr(dep, "get_db_client", lambda: db.client)
        monkeypatch.setattr(container, "get_db_pool_from_container", lambda: pool)
        monkeypatch.setattr(
            dep,
            "decode_and_validate_token",
            lambda token: {"sub": str(users[int(token)]), "sid": str(sessions[int(token)])},
        )
        app = FastAPI()
        audit = SimpleNamespace(log=AsyncMock(), log_security_event=AsyncMock())
        app.dependency_overrides[login_ep.get_audit_logger] = lambda: audit
        for module in (login_ep, refresh_ep, sessions_ep, registration_ep, signup_ep):
            app.include_router(module.router, prefix="/auth")
        monkeypatch.setattr(refresh_ep, "encode_access_token", lambda **kw: "synthetic-access")
        app.dependency_overrides[tenant_users.get_db_client] = lambda: db.client
        app.include_router(tenant_users.router, prefix="/rbac")
        app.include_router(admin_users.router, prefix="/admin")

        @app.get("/identity")
        async def identity(user=Depends(dep.get_current_user)):
            return {"user_id": user.id, "tenant_id": user.tenant_id, "role": user.role}

        @app.get("/admin")
        async def protected(user=Depends(dep.require_admin)):
            return {"role": user.role}

        @app.get("/permission")
        async def permission(user=Depends(require_permission(Permission.CALLS_CREATE))):
            return {"allowed": True}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://synthetic.example"
        ) as http:
            db.http = http
            yield db
    finally:
        if pool:
            await pool.close()
        await admin.execute("DELETE FROM user_permissions WHERE user_id=ANY($1::uuid[])", users)
        await admin.execute("DELETE FROM refresh_tokens WHERE user_id=ANY($1::uuid[])", users)
        await admin.execute("DELETE FROM login_attempts WHERE user_id=ANY($1::uuid[])", users)
        await admin.execute("DELETE FROM security_sessions WHERE user_id=ANY($1::uuid[])", users)
        await admin.execute("DELETE FROM tenant_users WHERE user_id=ANY($1::uuid[])", users)
        await admin.execute("DELETE FROM user_profiles WHERE id=ANY($1::uuid[])", users)
        await admin.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", tenants)
        if seeded_free:
            await admin.execute("DELETE FROM plans WHERE id='free'")
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


def auth(kind="cookie"):
    return {"cookie": "talky_at=0"} if kind == "cookie" else {"authorization": "Bearer 0"}


@pytest.mark.parametrize("kind", ["cookie", "bearer"])
async def test_revoked_session_is_denied_in_both_transports(identity_db, kind):
    db = identity_db
    assert (await db.http.get("/admin", headers=auth(kind))).status_code == 200
    await db.admin.execute(
        "UPDATE security_sessions SET revoked=TRUE,revoked_at=NOW() WHERE id=$1", db.sessions[0]
    )
    assert (await db.http.get("/admin", headers=auth(kind))).status_code == 401


@pytest.mark.parametrize("status", ["removed", "suspended", "pending"])
async def test_current_inactive_membership_blocks_profile_admin_and_direct_grant(
    identity_db, status
):
    db = identity_db
    await db.admin.execute(
        """INSERT INTO user_permissions(user_id,tenant_id,permission_id)
        SELECT $1,$2,id FROM permissions WHERE name='calls:create'""",
        db.users[0],
        db.tenants[0],
    )
    await db.admin.execute(
        "UPDATE tenant_users SET status=$1 WHERE user_id=$2", status, db.users[0]
    )
    for path in ("/identity", "/admin", "/permission"):
        assert (await db.http.get(path, headers=auth())).status_code in {401, 403}


async def test_membership_demotion_overrides_stale_profile_role(identity_db):
    db = identity_db
    await db.admin.execute(
        """UPDATE tenant_users SET role_id=(SELECT id FROM roles WHERE name='readonly')
        WHERE user_id=$1""",
        db.users[0],
    )
    result = await db.http.get("/identity", headers=auth())
    assert result.status_code == 200 and result.json()["role"] == "readonly"
    assert (await db.http.get("/admin", headers=auth())).status_code == 403


@pytest.mark.parametrize("state", ["inactive", "unverified"])
async def test_current_profile_admission_is_rechecked(identity_db, state):
    db = identity_db
    sql = (
        "UPDATE user_profiles SET is_active=FALSE WHERE id=$1"
        if state == "inactive"
        else "UPDATE user_profiles SET is_verified=FALSE,email_verified_at=NULL WHERE id=$1"
    )
    await db.admin.execute(sql, db.users[0])
    assert (await db.http.get("/identity", headers=auth())).status_code == 401


async def test_member_list_checks_exact_requested_tenant(identity_db):
    db = identity_db
    assert (await db.http.get("/rbac/tenant-users", headers=auth())).status_code == 200
    result = await db.http.get(
        "/rbac/tenant-users", headers=auth(), params={"tenant_id": str(db.tenants[1])}
    )
    assert result.status_code == 403


async def test_explicit_active_verified_platform_admin_preserved(identity_db):
    db = identity_db
    await db.admin.execute(
        "UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[0]
    )
    await db.admin.execute("DELETE FROM tenant_users WHERE user_id=$1", db.users[0])
    result = await db.http.get(
        "/rbac/tenant-users", headers=auth(), params={"tenant_id": str(db.tenants[1])}
    )
    assert result.status_code == 200 and len(result.json()) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"authorization": "Bearer 0", "cookie": "talky_at=1"},
        {"cookie": "talky_at=0", "X-Talky-Expected-User": "foreign-user"},
        {"cookie": "talky_at=0", "X-Talky-Expected-Tenant": "foreign-tenant"},
    ],
)
async def test_conflicting_browser_identity_is_denied_before_effects(identity_db, headers):
    db = identity_db
    result = await db.http.post("/auth/logout", headers=headers)
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "identity_changed"
    assert not result.headers.get_list("set-cookie")
    assert (
        await db.admin.fetchval(
            "SELECT COUNT(*) FROM security_sessions WHERE id=ANY($1::uuid[]) AND revoked",
            db.sessions,
        )
        == 0
    )


async def _refresh(db, index=0):
    raw, _, family = await issue_initial_refresh_token(
        db.admin,
        user_id=str(db.users[index]),
        tenant_id=str(db.tenants[index]),
        session_id=str(db.sessions[index]),
    )
    return raw, family


async def test_logout_revokes_authenticated_session_without_legacy_cookie(identity_db):
    db = identity_db
    raw, family = await _refresh(db)
    result = await db.http.post("/auth/logout", headers=auth("bearer"))
    assert result.status_code == 200
    assert await db.admin.fetchval(
        "SELECT revoked FROM security_sessions WHERE id=$1", db.sessions[0]
    )
    assert await db.admin.fetchval(
        "SELECT bool_and(revoked_at IS NOT NULL) FROM refresh_tokens WHERE family_id=$1", family
    )
    assert not await db.admin.fetchval(
        "SELECT revoked FROM security_sessions WHERE id=$1", db.sessions[1]
    )


async def test_logout_mixed_refresh_owner_cannot_revoke_either_account(identity_db):
    db = identity_db
    raw, family = await _refresh(db, 1)
    result = await db.http.post(
        "/auth/logout", headers={"authorization": "Bearer 0", "cookie": f"talky_rt={raw}"}
    )
    assert result.status_code == 409 and not result.headers.get_list("set-cookie")
    assert (
        await db.admin.fetchval(
            "SELECT COUNT(*) FROM security_sessions WHERE id=ANY($1::uuid[]) AND revoked",
            db.sessions,
        )
        == 0
    )
    assert not await db.admin.fetchval(
        "SELECT bool_or(revoked_at IS NOT NULL) FROM refresh_tokens WHERE family_id=$1", family
    )


async def test_refresh_checks_current_role_and_returns_bound_token(identity_db):
    db = identity_db
    raw, family = await _refresh(db)
    await db.admin.execute(
        "UPDATE tenant_users SET role_id=(SELECT id FROM roles WHERE name='readonly') WHERE user_id=$1",
        db.users[0],
    )
    result = await db.http.post(
        "/auth/refresh",
        headers={
            "cookie": f"talky_rt={raw}",
            "X-Talky-Expected-User": str(db.users[0]),
            "X-Talky-Expected-Tenant": str(db.tenants[0]),
        },
    )
    assert result.status_code == 200
    assert result.json() == {
        "access_token": "synthetic-access",
        "token_type": "bearer",
        "user_id": str(db.users[0]),
        "tenant_id": str(db.tenants[0]),
        "role": "readonly",
    }
    assert len(result.headers.get_list("set-cookie")) == 2
    assert (
        await db.admin.fetchval("SELECT COUNT(*) FROM refresh_tokens WHERE family_id=$1", family)
        == 2
    )


async def test_refresh_expected_mismatch_does_not_rotate_or_clear_cookie(identity_db):
    db = identity_db
    raw, family = await _refresh(db, 1)
    result = await db.http.post(
        "/auth/refresh",
        headers={"cookie": f"talky_rt={raw}", "X-Talky-Expected-User": str(db.users[0])},
    )
    assert result.status_code == 409 and not result.headers.get_list("set-cookie")
    row = await db.admin.fetchrow(
        "SELECT COUNT(*) AS count,bool_or(used_at IS NOT NULL OR revoked_at IS NOT NULL) AS changed FROM refresh_tokens WHERE family_id=$1",
        family,
    )
    assert row["count"] == 1 and not row["changed"]


async def test_removed_membership_refresh_is_denied_and_revocation_commits(identity_db):
    db = identity_db
    raw, family = await _refresh(db)
    await db.admin.execute("UPDATE tenant_users SET status='removed' WHERE user_id=$1", db.users[0])
    result = await db.http.post("/auth/refresh", headers={"cookie": f"talky_rt={raw}"})
    assert result.status_code == 401 and not result.headers.get_list("set-cookie")
    assert await db.admin.fetchval(
        "SELECT bool_and(revoked_at IS NOT NULL) FROM refresh_tokens WHERE family_id=$1", family
    )


async def test_concurrent_refresh_has_one_winner_and_reuse_revocation_commits(identity_db):
    db = identity_db
    raw, family = await _refresh(db)
    results = await asyncio.gather(
        *(db.http.post("/auth/refresh", headers={"cookie": f"talky_rt={raw}"}) for _ in range(2))
    )
    assert sorted(r.status_code for r in results) == [200, 401]
    row = await db.admin.fetchrow(
        "SELECT COUNT(*) AS count,bool_and(revoked_at IS NOT NULL) AS revoked FROM refresh_tokens WHERE family_id=$1",
        family,
    )
    assert row["count"] == 2 and row["revoked"]


async def test_actual_rejected_login_keeps_durable_failure_counter(identity_db, monkeypatch):
    db = identity_db
    await db.admin.execute(
        "UPDATE user_profiles SET password_hash='synthetic' WHERE id=$1", db.users[0]
    )
    monkeypatch.setattr(login_ep, "verify_password", lambda *args: False)
    result = await db.http.post(
        "/auth/login", json={"email": f"{db.users[0]}@example.com", "password": "wrong-password"}
    )
    assert result.status_code == 401
    row = await db.admin.fetchrow(
        "SELECT success,failure_reason FROM login_attempts WHERE user_id=$1", db.users[0]
    )
    assert row and not row["success"] and row["failure_reason"] == "wrong_password"


async def test_legacy_registration_is_retired_without_database_effect(identity_db):
    db = identity_db
    result = await db.http.post(
        "/auth/register", json={"email": "synthetic@example.com", "password": "not-submitted"}
    )
    assert (
        result.status_code == 410 and result.json()["detail"]["code"] == "registration_flow_retired"
    )


async def test_direct_grant_cannot_cross_removed_target_membership(identity_db):
    db = identity_db
    await db.admin.execute(
        "INSERT INTO tenant_users(user_id,tenant_id,role_id,status) SELECT $1,$2,id,'removed' FROM roles WHERE name='readonly'",
        db.users[0],
        db.tenants[1],
    )
    await db.admin.execute(
        "INSERT INTO user_permissions(user_id,tenant_id,permission_id) SELECT $1,$2,id FROM permissions WHERE name='calls:create'",
        db.users[0],
        db.tenants[1],
    )
    assert "calls:create" not in await get_user_permissions(
        db.admin, str(db.users[0]), str(db.tenants[1])
    )


@pytest.mark.parametrize("engine", ["text", "voice"])
@pytest.mark.parametrize("state", ["active", "revoked", "removed"])
async def test_assistant_socket_admission_uses_current_session_and_membership(
    identity_db, monkeypatch, engine, state
):
    from app.api.v1.endpoints import assistant_ws as text, assistant_voice_ws as voice

    db = identity_db
    if state == "revoked":
        await db.admin.execute(
            "UPDATE security_sessions SET revoked=TRUE,revoked_at=NOW() WHERE id=$1", db.sessions[0]
        )
    if state == "removed":
        await db.admin.execute(
            "UPDATE tenant_users SET status='removed' WHERE user_id=$1", db.users[0]
        )

    class Socket:
        headers = {}
        cookies = {"talky_at": "synthetic"}
        messages = []
        closed = None

        async def accept(self):
            pass

        async def send_json(self, data):
            self.messages.append(data)

        async def close(self, code=1000, reason=""):
            self.closed = code

        async def receive_json(self):
            raise WebSocketDisconnect()

    module = text if engine == "text" else voice
    monkeypatch.setattr(module, "get_db_client", lambda: db.client)
    monkeypatch.setattr(
        module,
        "decode_and_validate_token",
        lambda _: {
            "sub": str(db.users[0]),
            "sid": str(db.sessions[0]),
            "tenant_id": str(db.tenants[0]),
        },
    )
    run = AsyncMock()
    monkeypatch.setattr(voice, "_run_voice_session", run)
    socket = Socket()
    if engine == "text":
        await text.assistant_chat(socket, token=None, conversation_id=None)
        admitted = any(row.get("type") == "connected" for row in socket.messages)
    else:
        await voice.assistant_voice(socket, token=None, conversation_id=None)
        admitted = run.await_count == 1
    assert admitted is (state == "active")
    if state != "active":
        assert socket.closed == 1008
    from app.core.security.principal import assistant_session_context

    assert assistant_session_context.get() is None


async def test_open_socket_rechecks_authority_before_accepting_next_message(
    identity_db, monkeypatch
):
    from app.api.v1.endpoints import assistant_ws

    db = identity_db

    class Socket:
        headers = {}
        cookies = {"talky_at": "synthetic"}
        messages = []
        closed = None

        async def accept(self):
            pass

        async def send_json(self, data):
            self.messages.append(data)

        async def close(self, code=1000, reason=""):
            self.closed = code

        async def receive_json(self):
            await db.admin.execute(
                "UPDATE security_sessions SET revoked=TRUE,revoked_at=NOW() WHERE id=$1",
                db.sessions[0],
            )
            return {"type": "apply_proposal", "proposal_id": "never-consumed"}

    monkeypatch.setattr(assistant_ws, "get_db_client", lambda: db.client)
    monkeypatch.setattr(
        assistant_ws,
        "decode_and_validate_token",
        lambda _: {"sub": str(db.users[0]), "sid": str(db.sessions[0])},
    )
    pop = AsyncMock()
    monkeypatch.setattr(assistant_ws, "pop_proposal", pop)
    socket = Socket()
    await assistant_ws.assistant_chat(socket, token=None, conversation_id=None)
    assert socket.closed == 1008
    pop.assert_not_called()
    assert not any(row.get("type") == "proposal_result" for row in socket.messages)


@pytest.mark.parametrize("change", ["none", "revoked", "removed", "demoted"])
async def test_assistant_dispatch_rechecks_bound_session_and_live_grants(
    identity_db, monkeypatch, change
):
    from app.infrastructure.assistant.tools import dispatch
    from app.core.security.principal import assistant_session_context

    db = identity_db
    effect = AsyncMock(return_value={"success": True})
    monkeypatch.setitem(dispatch.ALL_TOOLS, "start_campaign", {"function": effect})
    if change == "revoked":
        await db.admin.execute(
            "UPDATE security_sessions SET revoked=TRUE,revoked_at=NOW() WHERE id=$1", db.sessions[0]
        )
    elif change == "removed":
        await db.admin.execute(
            "UPDATE tenant_users SET status='removed' WHERE user_id=$1", db.users[0]
        )
    elif change == "demoted":
        await db.admin.execute(
            "UPDATE tenant_users SET role_id=(SELECT id FROM roles WHERE name='readonly') WHERE user_id=$1",
            db.users[0],
        )
    context = assistant_session_context.set(
        {"sub": str(db.users[0]), "sid": str(db.sessions[0]), "tenant_id": str(db.tenants[0])}
    )
    try:
        result = await dispatch.dispatch_tool(
            "start_campaign",
            str(db.tenants[0]),
            db.client,
            None,
            {},
            actor_user_id=str(db.users[0]),
        )
    finally:
        assistant_session_context.reset(context)
    assert effect.await_count == int(change == "none")
    assert bool(result.get("error")) is (change != "none")


async def test_signup_cookie_persistence_failure_rolls_back_entire_account(
    identity_db, monkeypatch
):
    import json
    from app.services.scripts import seed_platform_sip_trunk

    db = identity_db
    email = f"{uuid4()}@example.com"
    pending = {
        "name": "Synthetic Owner",
        "business_name": "Synthetic CP08 Signup",
        "code_hash": signup_ep._hash_signup_code("123456"),
    }
    redis = SimpleNamespace(get=AsyncMock(return_value=json.dumps(pending)), delete=AsyncMock())
    monkeypatch.setattr(signup_ep, "_get_redis_or_503", lambda: redis)
    monkeypatch.setattr(signup_ep, "validate_password_strength", lambda _: None)
    monkeypatch.setattr(signup_ep, "hash_password", lambda _: "synthetic")
    monkeypatch.setattr(signup_ep, "create_jwt", lambda *args: "synthetic-access")
    monkeypatch.setattr(seed_platform_sip_trunk, "seed_for_tenant", AsyncMock())

    async def failed_refresh(response, conn, **kwargs):
        assert conn.is_in_transaction()
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM security_sessions WHERE user_id=$1", kwargs["user_id"]
            )
            == 1
        )
        raise RuntimeError("synthetic refresh write failure")

    monkeypatch.setattr(signup_ep, "issue_cookie_auth", failed_refresh)
    with pytest.raises(RuntimeError, match="synthetic refresh write failure"):
        await db.http.post(
            "/auth/signup/complete",
            json={
                "email": email,
                "code": "123456",
                "password": "Long-Example-Password-42!",
                "confirm_password": "Long-Example-Password-42!",
            },
        )
    assert not await db.admin.fetchval("SELECT id FROM user_profiles WHERE email=$1", email)
    assert not await db.admin.fetchval(
        "SELECT id FROM tenants WHERE business_name='Synthetic CP08 Signup'"
    )
    redis.delete.assert_not_called()


async def test_signup_send_failure_never_claims_email_sent(identity_db, monkeypatch):
    db = identity_db
    monkeypatch.setattr(signup_ep, "_get_redis_or_503", lambda: SimpleNamespace(setex=AsyncMock()))
    monkeypatch.setattr(
        signup_ep,
        "get_email_service",
        lambda: SimpleNamespace(send_signup_code_email=AsyncMock(return_value=False)),
    )
    result = await db.http.post(
        "/auth/signup/start",
        json={"email": f"{uuid4()}@example.com", "name": "Synthetic", "business_name": "Synthetic"},
    )
    assert result.status_code == 503 and "could not confirm" in result.json()["detail"]


@pytest.mark.parametrize("method", ["patch", "delete", "post"])
async def test_tenant_admin_cannot_modify_higher_tier_existing_member(identity_db, method):
    db = identity_db
    membership = await db.admin.fetchval(
        "INSERT INTO tenant_users(user_id,tenant_id,role_id,status) SELECT $1,$2,id,'active' FROM roles WHERE name='partner_admin' RETURNING id",
        db.users[1],
        db.tenants[0],
    )
    url = f"/rbac/tenant-users/{membership}"
    if method == "patch":
        result = await db.http.patch(url, headers=auth(), json={"status": "removed"})
    elif method == "delete":
        result = await db.http.delete(url, headers=auth())
    else:
        result = await db.http.post(
            "/rbac/tenant-users",
            headers=auth(),
            json={
                "user_id": str(db.users[1]),
                "tenant_id": str(db.tenants[0]),
                "role_name": "user",
            },
        )
    assert result.status_code == 403
    assert (
        await db.admin.fetchval("SELECT status FROM tenant_users WHERE id=$1", membership)
        == "active"
    )


async def test_session_revocation_during_permission_lookup_stops_effect(identity_db, monkeypatch):
    from app.infrastructure.assistant.tools import dispatch
    from app.core.security.principal import assistant_session_context

    db = identity_db
    effect = AsyncMock(return_value={"success": True})
    monkeypatch.setitem(dispatch.ALL_TOOLS, "start_campaign", {"function": effect})
    original = dispatch.get_effective_permissions

    async def permission_then_revoke(*args):
        grants = await original(*args)
        await db.admin.execute(
            "UPDATE security_sessions SET revoked=TRUE,revoked_at=NOW() WHERE id=$1", db.sessions[0]
        )
        return grants

    monkeypatch.setattr(dispatch, "get_effective_permissions", permission_then_revoke)
    context = assistant_session_context.set(
        {"sub": str(db.users[0]), "sid": str(db.sessions[0]), "tenant_id": str(db.tenants[0])}
    )
    try:
        result = await dispatch.dispatch_tool(
            "start_campaign",
            str(db.tenants[0]),
            db.client,
            None,
            {},
            actor_user_id=str(db.users[0]),
        )
    finally:
        assistant_session_context.reset(context)
    effect.assert_not_awaited()
    assert result["error"] == "session_unavailable" and result["status"] == "failed"


@pytest.mark.parametrize("failure", ["runtime", "http_rejection"])
async def test_login_refresh_insert_failure_does_not_leave_new_session(
    identity_db, monkeypatch, failure
):
    db = identity_db
    await db.admin.execute(
        "UPDATE user_profiles SET password_hash='synthetic' WHERE id=$1", db.users[0]
    )
    monkeypatch.setattr(login_ep, "verify_password", lambda *args: True)
    monkeypatch.setattr(login_ep, "rehash_if_needed", lambda *args: None)
    monkeypatch.setattr(login_ep, "create_jwt", lambda *args: "synthetic-access")

    async def failed_refresh(response, conn, **kwargs):
        assert conn.is_in_transaction()
        if failure == "http_rejection":
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="synthetic issuance refusal")
        raise RuntimeError("synthetic refresh failure")

    monkeypatch.setattr(login_ep, "issue_cookie_auth", failed_refresh)
    if failure == "http_rejection":
        result = await db.http.post(
            "/auth/login",
            json={"email": f"{db.users[0]}@example.com", "password": "valid-password"},
        )
        assert result.status_code == 401
    else:
        with pytest.raises(RuntimeError, match="synthetic refresh failure"):
            await db.http.post(
                "/auth/login",
                json={"email": f"{db.users[0]}@example.com", "password": "valid-password"},
            )
    assert (
        await db.admin.fetchval(
            "SELECT COUNT(*) FROM security_sessions WHERE user_id=$1", db.users[0]
        )
        == 1
    )
    assert not await db.admin.fetchval(
        "SELECT COUNT(*) FROM login_attempts WHERE user_id=$1 AND success", db.users[0]
    )
    assert not await db.admin.fetchval(
        "SELECT last_login_at FROM user_profiles WHERE id=$1", db.users[0]
    )


@pytest.mark.parametrize("state", ["active", "inactive", "unverified", "removed", "tenant_changed"])
async def test_campaign_test_session_gate_uses_same_current_principal(identity_db, state):
    from app.api.v1.endpoints import campaign_test_ws

    db = identity_db
    if state == "inactive":
        await db.admin.execute("UPDATE user_profiles SET is_active=FALSE WHERE id=$1", db.users[0])
    elif state == "unverified":
        await db.admin.execute(
            "UPDATE user_profiles SET is_verified=FALSE,email_verified_at=NULL WHERE id=$1",
            db.users[0],
        )
    elif state == "removed":
        await db.admin.execute(
            "UPDATE tenant_users SET status='removed' WHERE user_id=$1", db.users[0]
        )
    elif state == "tenant_changed":
        await db.admin.execute(
            "UPDATE user_profiles SET tenant_id=$1 WHERE id=$2", db.tenants[1], db.users[0]
        )
    result = await campaign_test_ws._session_is_active(
        db.pool, str(db.users[0]), str(db.sessions[0]), str(db.tenants[0])
    )
    assert result is (state == "active")


async def test_admin_created_tenant_user_can_sign_in_with_current_membership(
    identity_db, monkeypatch
):
    db = identity_db
    await db.admin.execute(
        "UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[0]
    )
    email = f"{uuid4()}@example.com"
    monkeypatch.setattr(admin_users, "hash_password", lambda _: "synthetic")
    try:
        created = await db.http.post(
            "/admin/users",
            headers=auth(),
            json={
                "name": "Synthetic staff",
                "email": email,
                "password": "Synthetic-Password-42!",
                "role": "user",
                "tenant_id": str(db.tenants[0]),
            },
        )
        assert created.status_code == 201, created.text
        new_user = created.json()["id"]
        membership = await db.admin.fetchrow(
            "SELECT tu.status,r.name FROM tenant_users tu JOIN roles r ON r.id=tu.role_id WHERE user_id=$1 AND tenant_id=$2",
            new_user,
            db.tenants[0],
        )
        assert membership and membership["status"] == "active" and membership["name"] == "user"
        monkeypatch.setattr(login_ep, "verify_password", lambda *args: True)
        monkeypatch.setattr(login_ep, "rehash_if_needed", lambda *args: None)
        monkeypatch.setattr(login_ep, "create_jwt", lambda *args: "synthetic-access")
        monkeypatch.setattr(
            "app.api.v1.endpoints.auth._shared.encode_access_token",
            lambda **kwargs: "synthetic-access",
        )
        signed_in = await db.http.post(
            "/auth/login", json={"email": email, "password": "Synthetic-Password-42!"}
        )
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["role"] == "user" and signed_in.json()["tenant_id"] == str(
            db.tenants[0]
        )
    finally:
        user = await db.admin.fetchval("SELECT id FROM user_profiles WHERE email=$1", email)
        if user:
            db.users.append(user)


async def test_admin_role_edit_changes_effective_membership_not_just_profile(identity_db):
    db = identity_db
    await db.admin.execute(
        "UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[0]
    )
    edited = await db.http.patch(
        f"/admin/users/{db.users[1]}", headers=auth(), json={"role": "readonly"}
    )
    assert edited.status_code == 200, edited.text
    actual = await db.http.get("/identity", headers={"authorization": "Bearer 1"})
    assert actual.status_code == 200 and actual.json()["role"] == "readonly"


async def test_admin_create_membership_failure_rolls_back_new_profile(identity_db, monkeypatch):
    db = identity_db
    await db.admin.execute(
        "UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[0]
    )
    await db.admin.execute(f'REVOKE INSERT ON tenant_users FROM "{db.role}"')
    email = f"{uuid4()}@example.com"
    monkeypatch.setattr(admin_users, "hash_password", lambda _: "synthetic")
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await db.http.post(
                "/admin/users",
                headers=auth(),
                json={
                    "name": "Synthetic staff",
                    "email": email,
                    "password": "Synthetic-Password-42!",
                    "role": "user",
                    "tenant_id": str(db.tenants[0]),
                },
            )
        assert not await db.admin.fetchval("SELECT id FROM user_profiles WHERE email=$1", email)
    finally:
        user = await db.admin.fetchval("SELECT id FROM user_profiles WHERE email=$1", email)
        if user:
            db.users.append(user)


async def test_admin_role_edit_does_not_create_missing_legacy_membership(identity_db):
    db = identity_db
    await db.admin.execute(
        "UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[0]
    )
    await db.admin.execute("DELETE FROM tenant_users WHERE user_id=$1", db.users[1])
    result = await db.http.patch(
        f"/admin/users/{db.users[1]}", headers=auth(), json={"role": "readonly"}
    )
    assert result.status_code == 409
    assert not await db.admin.fetchval(
        "SELECT COUNT(*) FROM tenant_users WHERE user_id=$1", db.users[1]
    )
    assert (
        await db.admin.fetchval("SELECT role FROM user_profiles WHERE id=$1", db.users[1])
        == "tenant_admin"
    )


async def test_tenant_admin_cannot_use_platform_user_creation(identity_db):
    db = identity_db
    email = f"{uuid4()}@example.com"
    result = await db.http.post(
        "/admin/users",
        headers=auth(),
        json={
            "name": "Synthetic",
            "email": email,
            "password": "Synthetic-Password-42!",
            "role": "platform_admin",
        },
    )
    assert result.status_code == 403
    assert not await db.admin.fetchval("SELECT id FROM user_profiles WHERE email=$1", email)


async def test_admin_create_requires_tenant_for_nonplatform_role(identity_db):
    db = identity_db
    await db.admin.execute(
        "UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[0]
    )
    email = f"{uuid4()}@example.com"
    result = await db.http.post(
        "/admin/users",
        headers=auth(),
        json={
            "name": "Synthetic",
            "email": email,
            "password": "Synthetic-Password-42!",
            "role": "user",
        },
    )
    assert result.status_code == 400
    assert not await db.admin.fetchval("SELECT id FROM user_profiles WHERE email=$1", email)


async def test_admin_directory_reflects_rbac_role_edit_and_effective_role_filter(identity_db):
    db = identity_db
    await db.admin.execute(
        "UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[0]
    )
    membership_id = await db.admin.fetchval(
        "SELECT id FROM tenant_users WHERE user_id=$1 AND tenant_id=$2",
        db.users[1], db.tenants[1],
    )
    edited = await db.http.patch(
        f"/rbac/tenant-users/{membership_id}", headers=auth(), json={"role_name": "readonly"}
    )
    assert edited.status_code == 200, edited.text
    # The ordinary membership editor deliberately does not rewrite this legacy profile assignment.
    assert await db.admin.fetchval(
        "SELECT role FROM user_profiles WHERE id=$1", db.users[1]
    ) == "tenant_admin"
    detailed = await db.http.get(f"/admin/users/{db.users[1]}", headers=auth())
    assert detailed.status_code == 200, detailed.text
    assert detailed.json()["role"] == "readonly"
    assert detailed.json()["effective_role"] == "readonly"
    assert detailed.json()["membership_status"] == "active"
    for role, expected in (("readonly", 1), ("tenant_admin", 0)):
        result = await db.http.get(
            "/admin/users", headers=auth(),
            params={"search": str(db.users[1]), "role": role},
        )
        assert result.status_code == 200 and len(result.json()) == expected
    # Ordinary tenant roles cannot turn this global directory into a cross-tenant read route.
    denied = await db.http.get("/admin/users", headers={"authorization": "Bearer 1"})
    assert denied.status_code == 403


async def test_admin_directory_distinguishes_historical_assignment_from_current_access(identity_db):
    db = identity_db
    await db.admin.execute(
        "UPDATE user_profiles SET role='platform_admin' WHERE id=$1", db.users[0]
    )
    await db.admin.execute("DELETE FROM tenant_users WHERE user_id=ANY($1::uuid[])", db.users)
    legacy = await db.http.get(f"/admin/users/{db.users[1]}", headers=auth())
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["role"] == "tenant_admin"  # Historical assignment only.
    assert legacy.json()["effective_role"] is None
    assert legacy.json()["membership_status"] == "missing"
    filtered = await db.http.get(
        "/admin/users", headers=auth(),
        params={"search": str(db.users[1]), "role": "tenant_admin"},
    )
    assert filtered.status_code == 200 and filtered.json() == []
    platform = await db.http.get(f"/admin/users/{db.users[0]}", headers=auth())
    assert platform.json()["effective_role"] == "platform_admin"
    assert platform.json()["membership_status"] == "not_required"
