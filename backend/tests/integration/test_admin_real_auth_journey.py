"""Actual configured HTTP/auth/PG journey; no principal/crypto/route mocks.

Opt in only on reviewed local or GitHub CI disposable databases. Application routes,
middleware, Argon2, TOTP, signed JWTs, cookies, current authority, RLS and audit
are real. The fixture supplies only DB infrastructure; production lifespan,
providers, workers, browsers and deployment are outside this test's claim.
"""
import asyncio
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import httpx
import pyotp
import pytest
import pytest_asyncio


pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
DSN = "postgresql://talky@127.0.0.1:55434/admin_premortem_20261021_test"
CI_DSN = "postgresql://talky:testpassword@localhost:5432/talky_alembic_test"
ORIGIN = "http://127.0.0.1:58201"
API_ORIGIN = "http://127.0.0.1:58202"
RUN_EVIDENCE = []


def reviewed_database(configured, github_actions):
    if configured == DSN or (github_actions == "true" and configured == CI_DSN):
        return configured
    raise ValueError("Refuse database outside the exact reviewed disposable targets")


@pytest.mark.parametrize("configured,ci,accepted", [
    (DSN, "", True),
    (DSN, "true", True),
    (CI_DSN, "true", True),
    (CI_DSN, "false", False),
    (CI_DSN, "", False),
    (CI_DSN.replace("localhost", "unreviewed.invalid"), "true", False),
    (CI_DSN.replace("talky_alembic_test", "talky"), "true", False),
    (DSN + "?options=unreviewed", "true", False),
], ids=["local", "local-on-ci", "exact-ci", "ci-false", "ci-unset",
        "unknown-host", "unknown-database", "unknown-options"])
def test_reviewed_database_guard(configured, ci, accepted):
    if accepted:
        assert reviewed_database(configured, ci) == configured
    else:
        with pytest.raises(ValueError, match="exact reviewed disposable"):
            reviewed_database(configured, ci)


def snapshot_helper():
    path = ROOT / "docs/sessions/artifacts/ag06-saved-ack-recovery/pg_probe.py"
    spec = importlib.util.spec_from_file_location("admin_real_http_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.snapshot


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


async def protected_snapshot(conn):
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        return await _protected_snapshot(conn)


async def _protected_snapshot(conn):
    result = await snapshot_helper()(conn)
    # PostgreSQL may materialize NULL/default ACLs on the first GRANT, even
    # after DROP OWNED removes every fixture grant. Compare effective grants,
    # preserving owner, grantor, grantee, privilege and grant option instead.
    # pg_class uses relkind 'S'; acldefault uses 's' for sequences ('S' is a foreign server).
    rows = await conn.fetch("""
        SELECT c.relname,c.relkind::text,c.relrowsecurity,c.relforcerowsecurity,
               pg_get_userbyid(c.relowner) AS owner,
               ARRAY(SELECT jsonb_build_object(
                   'grantor', a.grantor, 'grantee', a.grantee,
                   'privilege', a.privilege_type, 'grant_option', a.is_grantable)::text
                 FROM aclexplode(CASE
                   WHEN c.relkind IN ('r','p','v','m','f','S') THEN
                     coalesce(c.relacl,acldefault(
                       CASE WHEN c.relkind='S' THEN 's' ELSE 'r' END::"char",c.relowner))
                   ELSE c.relacl END) a
                 ORDER BY a.grantor,a.grantee,a.privilege_type,a.is_grantable
               ) AS effective_acl
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' ORDER BY c.relname
    """)
    result["schema"]["relations"] = {
        "count": len(rows), "sha256": digest([dict(row) for row in rows]),
    }
    return result


@pytest_asyncio.fixture
async def journey_db(monkeypatch):
    configured = os.getenv("TEST_ADMIN_DATABASE_URL")
    if not configured:
        pytest.skip("Explicit reviewed disposable TEST_ADMIN_DATABASE_URL required")
    configured = reviewed_database(configured, os.getenv("GITHUB_ACTIONS", ""))
    assert not (ROOT / "backend/.env").exists()
    assert not (ROOT / "backend/app/.env").exists()
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CORS_ORIGINS", json.dumps([ORIGIN]))
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_COOKIE_SAMESITE", "strict")
    monkeypatch.delenv("AUTH_COOKIE_DOMAIN", raising=False)
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", base64.urlsafe_b64encode(hashlib.sha256(b"synthetic-local-TOTP-key").digest()).decode())
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.core import container, db as core_db
    from app.api.v1 import dependencies
    from app.core.postgres_adapter import Client
    from app.core.security.password import hash_password
    from app.core.security.totp import encrypt_totp_secret
    from app.main import app

    assert not app.dependency_overrides, "Authentication must use normal dependencies"
    owner = await asyncpg.connect(configured, command_timeout=30)
    before = await protected_snapshot(owner)
    assert before["head"] == "0077_legacy_tenant_rls_repair"
    role = "admin_http_" + uuid4().hex
    tenants = [uuid4() for _ in range(3)]
    users = [uuid4() for _ in range(3)]
    password = "Synthetic-local-journey-password!27"
    secret = pyotp.random_base32()
    report = {"scope": "actual full configured ASGI application, no production lifespan",
              "steps": [], "protected_before_sha256": digest(before),
              "acl_comparison": "effective sorted grants and owner; NULL/default storage representation canonicalized"}
    RUN_EVIDENCE.append(report)
    pool = None
    role_created = False
    try:
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        role_created = True
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(
            f'GRANT SELECT ON tenants,user_profiles,tenant_users,roles,permissions,role_permissions,'
            f'user_permissions,white_label_partners,suspension_events,audit_logs,calls,call_legs,'
            f'call_events,campaigns,plans TO "{role}"'
        )
        await owner.execute(
            f'GRANT SELECT,INSERT,UPDATE ON security_sessions,refresh_tokens,login_attempts,'
            f'user_mfa,mfa_challenges,webauthn_challenges,security_events TO "{role}"'
        )
        await owner.execute(f'GRANT UPDATE ON tenants,user_profiles,tenant_users TO "{role}"')
        await owner.execute(f'GRANT INSERT,UPDATE ON suspension_events TO "{role}"')
        await owner.execute(f'GRANT INSERT ON audit_logs TO "{role}"')
        encoded = hash_password(password)
        for index, (tenant, user) in enumerate(zip(tenants, users)):
            await owner.execute(
                "INSERT INTO tenants(id,business_name,status,subscription_status) VALUES($1,$2,'active','active')",
                tenant, f"Synthetic Admin HTTP {tenant}",
            )
            await owner.execute(
                """INSERT INTO user_profiles(id,tenant_id,email,name,role,password_hash,is_active,
                   is_verified,email_verified_at,mfa_enabled)
                   VALUES($1,$2,$3,'Synthetic HTTP user',$4,$5,TRUE,TRUE,NOW(),$6)""",
                user, tenant, f"{user}@example.com",
                "platform_admin" if index == 0 else "tenant_admin", encoded, index == 0,
            )
            await owner.execute(
                """INSERT INTO tenant_users(user_id,tenant_id,role_id,status,is_primary)
                   SELECT $1,$2,id,'active',TRUE FROM roles WHERE name='tenant_admin'""", user, tenant,
            )
        await owner.execute(
            "INSERT INTO user_mfa(user_id,totp_secret_enc,enabled,verified_at) VALUES($1,$2,TRUE,NOW())",
            users[0], encrypt_totp_secret(secret),
        )

        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute("SET statement_timeout='15s'")

        pool = await asyncpg.create_pool(configured, min_size=1, max_size=5,
                                       init=core_db._register_jsonb_codecs, setup=setup)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert not flags["rolsuper"] and not flags["rolbypassrls"]
        # Infrastructure only. Every auth/service factory remains production code.
        local_container = container.ServiceContainer()
        local_container._db_pool = pool
        local_container._db_client = Client(pool)
        local_container._initialized = True
        monkeypatch.setattr(container, "_container", local_container)
        monkeypatch.setattr(core_db, "_pool", pool)
        monkeypatch.setattr(core_db, "_read_pool", None)
        monkeypatch.setattr(dependencies, "_audit_logger_service", None)
        monkeypatch.setattr(dependencies, "_suspension_service_instance", None)
        report["database_role"] = {"superuser": False, "bypassrls": False}
        yield SimpleNamespace(app=app, owner=owner, pool=pool, tenants=tenants, users=users,
                              password=password, totp=secret, report=report)
    finally:
        audit = dependencies._audit_logger_service
        if audit:
            await asyncio.wait_for(audit.aclose(), 15)
        if pool:
            await asyncio.wait_for(pool.close(), 15)
        async with owner.transaction():
            await owner.execute("SET LOCAL app.bypass_rls='on'")
            await owner.execute("DELETE FROM suspension_events WHERE target_id=ANY($1::uuid[])", [*tenants, *users])
            await owner.execute("DELETE FROM security_events WHERE user_id=ANY($1::uuid[]) OR tenant_id=ANY($2::uuid[])", users, tenants)
            await owner.execute("DELETE FROM audit_logs WHERE actor_id=ANY($1::uuid[]) OR tenant_id=ANY($2::uuid[])", users, tenants)
            await owner.execute("DELETE FROM login_attempts WHERE user_id=ANY($1::uuid[])", users)
            await owner.execute("DELETE FROM refresh_tokens WHERE user_id=ANY($1::uuid[])", users)
            await owner.execute("DELETE FROM security_sessions WHERE user_id=ANY($1::uuid[])", users)
            await owner.execute("DELETE FROM mfa_challenges WHERE user_id=ANY($1::uuid[])", users)
            await owner.execute("DELETE FROM user_mfa WHERE user_id=ANY($1::uuid[])", users)
            await owner.execute("UPDATE tenants SET suspended_by=NULL WHERE id=ANY($1::uuid[])", tenants)
            await owner.execute("DELETE FROM tenant_users WHERE user_id=ANY($1::uuid[])", users)
            await owner.execute("DELETE FROM user_profiles WHERE id=ANY($1::uuid[])", users)
            await owner.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", tenants)
        if role_created:
            await owner.execute(f'DROP OWNED BY "{role}"')
            await owner.execute(f'DROP ROLE "{role}"')
        after = await protected_snapshot(owner)
        report["protected_after_sha256"] = digest(after)
        report["protected_unchanged"] = before == after
        report["changed_tables"] = [name for name in before["tables"] if before["tables"][name] != after["tables"].get(name)]
        report["fixture_role_removed"] = not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        await owner.close()
        get_settings.cache_clear()
        assert report["protected_unchanged"], "Protected disposable fixture state changed"
        assert report["fixture_role_removed"]


async def test_admin_mfa_cookie_hold_evidence_restore_and_old_credentials(journey_db):
    db = journey_db
    transport = httpx.ASGITransport(app=db.app, client=("127.0.0.1", 59101))
    client_options = {"transport": transport, "base_url": API_ORIGIN,
                      "headers": {"Origin": ORIGIN, "User-Agent": "Synthetic-Admin-HTTP-Acceptance"}}

    async def request(client, method, path, expected=200, **kwargs):
        response = await client.request(method, "/api/v1" + path, **kwargs)
        db.report["steps"].append({"method": method, "path": path, "status": response.status_code})
        assert response.status_code == expected, f"{method} {path}: expected {expected}, received {response.status_code}"
        origin = kwargs.get("headers", {}).get("Origin", ORIGIN)
        if origin == ORIGIN:
            assert response.headers.get("access-control-allow-origin") == ORIGIN
            assert response.headers.get("access-control-allow-credentials") == "true"
        else:
            assert "access-control-allow-origin" not in response.headers
        assert response.headers.get("x-request-id")
        assert response.headers.get("x-content-type-options") == "nosniff"
        return response

    async with httpx.AsyncClient(**client_options) as admin, httpx.AsyncClient(**client_options) as member, httpx.AsyncClient(**client_options) as other:
        await request(admin, "GET", "/admin/tenants", 401)
        await request(admin, "GET", "/admin/tenants", 401, headers={"Authorization": "Bearer malformed"})
        first = await request(admin, "POST", "/auth/login", json={"email": f"{db.users[0]}@example.com", "password": db.password})
        assert first.json()["mfa_required"] is True
        assert not first.json().get("access_token") and not admin.cookies.get("talky_at")
        await request(admin, "GET", "/admin/tenants", 401)
        verified = await request(admin, "POST", "/auth/mfa/verify", json={
            "challenge_token": first.json()["mfa_challenge_token"], "code": pyotp.TOTP(db.totp).now(),
        })
        assert verified.json()["access_token"] and admin.cookies.get("talky_at")
        assert {c.name for c in admin.cookies.jar} >= {"talky_at", "talky_rt", "talky_sid"}
        me = await request(admin, "GET", "/auth/me")
        assert me.json()["role"] == "platform_admin" and me.json()["id"] == str(db.users[0])
        # The real Admin client sends its issued bearer with matching cookies.
        # Keep the cookie-only identity check above, then exercise that transport.
        admin.headers["Authorization"] = "Bearer " + verified.json()["access_token"]
        listed = await request(admin, "GET", "/admin/tenants", params={"limit": 1000})
        assert set(map(str, db.tenants)) <= {row["id"] for row in listed.json()}

        for client, user in ((member, db.users[1]), (other, db.users[2])):
            await request(client, "POST", "/auth/login", json={"email": f"{user}@example.com", "password": db.password})
            await request(client, "GET", "/auth/me")
            await request(client, "GET", "/admin/tenants", 403)
        await request(member, "GET", "/rbac/tenant-users", 403, params={"tenant_id": str(db.tenants[2])})
        tenant_path = f"/admin/suspensions/tenants/{db.tenants[1]}"
        body = {"suspension_type": "ADMIN", "reason_category": "synthetic_acceptance",
                "reason_description": "Synthetic local reviewed hold", "notify_user": False,
                "evidence": {"operator_request_id": str(uuid4())}}
        await request(member, "POST", tenant_path + "/suspend", 403, json=body)
        await request(admin, "POST", tenant_path + "/suspend", 403,
                      headers={"Origin": "https://untrusted.invalid", "Authorization": ""}, json=body)
        old_cookies = httpx.Cookies(member.cookies)
        old_access = member.cookies.get("talky_at")
        held = (await request(admin, "POST", tenant_path + "/suspend?immediate=true", json=body)).json()
        hold_id = held["suspension_id"]
        assert held["audit_event_id"] and held["target_id"] == str(db.tenants[1])
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE event_id=$1", UUID(held["audit_event_id"])) == 1
        status = (await request(admin, "GET", tenant_path + "/status")).json()
        assert status["active_suspension"]["suspension_id"] == hold_id
        proof = (await request(admin, "GET", f"/admin/suspensions/propagation-status/{hold_id}")).json()
        credential = next(d for d in proof["dimensions"] if d["key"] == "credentials")
        # Account holds prove the saved session cohort. Refresh requests must
        # still fail through their binding to those revoked sessions below.
        assert credential["status"] == "confirmed" and credential["counts"]["captured"] == 1
        assert proof["propagation_status"] != "confirmed"
        assert proof["current_authority"]["status"] == "restricted"
        db.report["proof"] = {"credential_status": credential["status"], "credential_counts": credential["counts"],
                              "aggregate": proof["propagation_status"], "fleet_confirmation_claimed": False}
        await request(member, "GET", "/auth/me", 401)
        member.cookies = httpx.Cookies(old_cookies)
        await request(member, "POST", "/auth/refresh", 401)
        await request(other, "GET", "/auth/me")
        await request(member, "POST", "/auth/login", 401, json={"email": f"{db.users[1]}@example.com", "password": db.password})
        await request(admin, "POST", tenant_path + "/restore", 422, json={"reason": "Missing reviewed identity"})
        await request(admin, "POST", tenant_path + "/restore", 409, json={"reason": "Wrong reviewed identity", "expected_suspension_id": str(uuid4())})
        restored = (await request(admin, "POST", tenant_path + "/restore", json={
            "reason": "Synthetic reviewed restore", "expected_suspension_id": hold_id,
        })).json()
        assert restored["restored"] and restored["suspension_id"] == hold_id and restored["audit_event_id"]
        assert await db.owner.fetchval("SELECT count(*) FROM audit_logs WHERE event_id=$1", UUID(restored["audit_event_id"])) == 1
        member.cookies = httpx.Cookies(old_cookies)
        await request(member, "GET", "/auth/me", 401)
        member.cookies = httpx.Cookies(old_cookies)
        await request(member, "POST", "/auth/refresh", 401)
        member.cookies.clear()
        await request(member, "GET", "/auth/me", 401, headers={"Authorization": "Bearer " + old_access})
        await request(member, "POST", "/auth/login", json={"email": f"{db.users[1]}@example.com", "password": db.password})
        await request(member, "GET", "/auth/me")
        await request(other, "GET", "/auth/me")
        final_status = (await request(admin, "GET", tenant_path + "/status")).json()
        assert final_status["active_suspension"] is None and final_status["effective_status"] == "active"
        assert not await db.owner.fetchval("SELECT is_active FROM suspension_events WHERE suspension_id=$1", UUID(hold_id))
        from app.core.db_utils import acquire_with_tenant
        async with db.pool.acquire() as conn:
            assert await conn.fetchval("SELECT current_setting('app.bypass_rls',TRUE)") in (None, "", "off", "false")
            flags = await conn.fetchrow("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='user_profiles'::regclass")
            assert flags["relrowsecurity"] and flags["relforcerowsecurity"]
        async with acquire_with_tenant(db.pool, str(db.tenants[1])) as conn:
            # 0038 protects tenant-owned profiles/memberships; the platform
            # tenants directory itself is not an RLS table.
            visible = await conn.fetch("SELECT id FROM user_profiles WHERE id=ANY($1::uuid[])", db.users)
            assert [row["id"] for row in visible] == [db.users[1]]
        db.report["combined_bearer_cookie_transport"] = True
        db.report["normal_cross_tenant_http_denied"] = True
        db.report["actual_tenant_rls_and_no_bypass_leak"] = True
        db.report["rls_probe_table"] = "user_profiles (ENABLE+FORCE); tenants directory not claimed"
        db.report["error_cors_request_id_security_headers"] = True
        db.report["journey_complete"] = True
