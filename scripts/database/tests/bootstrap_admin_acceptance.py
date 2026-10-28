"""Real first-admin acceptance, called only by the owned TLS fixture runner.

No CLI accepting external targets. The caller creates/removes the isolated
containers and supplies their services. Uses synthetic credentials only, real
database transactions and the configured ASGI auth routes without a lifespan.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import quote, urlencode
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))


def service_url(service):
    value = service.values
    return (f"postgresql://{quote(value['user'], safe='')}@{value['host']}:{value['port']}/"
            f"{quote(value['dbname'], safe='')}?" + urlencode({key: value[key] for key in
            ("sslmode", "sslrootcert", "passfile")}))


def run_checks(owner_service, runtime_service):
    """Return nonsecret passed check names; exceptions intentionally redact SQL."""
    stage = []
    try:
        assert owner_service.values["dbname"] == runtime_service.values["dbname"] == "talky_synthetic_source"
        assert owner_service.values["user"] == "fixture_migration"
        assert runtime_service.values["user"] == "fixture_runtime"
        assert owner_service.values["host"] == runtime_service.values["host"]
        assert owner_service.values["sslmode"] == runtime_service.values["sslmode"] == "verify-full"
        assert not (ROOT / "backend/.env").exists() and not (ROOT / "backend/app/.env").exists()
        return asyncio.run(_checks(owner_service, runtime_service, stage))
    except Exception:
        completed = stage[-1] if stage else "fixture-guard"
        raise RuntimeError(f"Synthetic first-admin acceptance failed after {completed}; sensitive details withheld") from None


async def _checks(owner_service, runtime_service, checks):
    import asyncpg
    import pyotp
    from cryptography.fernet import Fernet

    settings = {
        "ENVIRONMENT": "test", "PYTHON_DOTENV_DISABLED": "1",
        "TOTP_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "AUDIT_LOG_SIGNING_KEY": secrets.token_urlsafe(48),
        "CORS_ORIGINS": json.dumps(["http://127.0.0.1:58201"]),
        "SESSION_COOKIE_SECURE": "false", "AUTH_COOKIE_SAMESITE": "strict",
    }
    previous = {key: os.environ.get(key) for key in settings}
    os.environ.update(settings)
    spec = importlib.util.spec_from_file_location("synthetic_first_admin_cli", ROOT / "backend/scripts/bootstrap_first_admin.py")
    bootstrap = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = bootstrap
    spec.loader.exec_module(bootstrap)
    from app.core.config import get_settings
    get_settings.cache_clear()
    from app.core.security.password import verify_password
    from app.core.security.totp import decrypt_totp_secret, verify_totp_step
    from app.domain.services.audit_logger import AuditLogger

    owner_url, runtime_url = service_url(owner_service), service_url(runtime_service)
    bootstrap.validate_database_url(owner_url)
    owner = await asyncpg.connect(owner_url, timeout=10, command_timeout=20)
    await owner.execute("SET app.bypass_rls='on'")
    audit = AuditLogger(None)
    target = {"database": owner_service.values["dbname"], "owner": owner_service.values["user"], "audit_logger": audit}
    secret = pyotp.random_base32()
    original = bootstrap.Credentials("first-operator@example.com", "Synthetic First Operator",
        secrets.token_urlsafe(32), secret, pyotp.TOTP(secret).now(), True)

    def fresh():
        return replace(original, totp_code=pyotp.TOTP(secret).now())

    async def denied(connection, credentials, **overrides):
        try:
            await bootstrap.provision(connection, credentials, **{**target, **overrides})
        except bootstrap.BootstrapError:
            return
        raise AssertionError("Expected provisioning refusal")

    async def counts():
        return tuple([await owner.fetchval(f"SELECT count(*) FROM public.{table}") for table in
            ("user_profiles", "user_mfa", "audit_logs", "security_sessions", "recovery_codes")])

    try:
        assert not await owner.fetchval("SELECT EXISTS(SELECT 1 FROM user_profiles WHERE role='platform_admin')")
        baseline = await counts()
        await denied(owner, replace(fresh(), totp_code="invalid"))
        await denied(owner, replace(fresh(), owned_email_confirmed=False))
        await denied(owner, fresh(), database="wrong_fixture_database")
        assert await counts() == baseline
        checks.append("invalid-proof-attestation-target-no-writes")

        runtime = await asyncpg.connect(runtime_url, timeout=10, command_timeout=20)
        try:
            await denied(runtime, fresh(), owner=runtime_service.values["user"])
        finally:
            await runtime.close(timeout=5)
        assert await counts() == baseline
        checks.append("runtime-role-cannot-bootstrap")

        for role, active, email in (("user", True, original.email.upper()),
                                    ("platform_admin", False, "inactive-operator@example.com")):
            marker = uuid4()
            await owner.execute("INSERT INTO user_profiles(id,email,role,is_active) VALUES($1,$2,$3,$4)", marker, email, role, active)
            before = await counts()
            await denied(owner, fresh())
            assert await counts() == before
            row = await owner.fetchrow("SELECT role,is_active,password_hash,mfa_enabled FROM user_profiles WHERE id=$1", marker)
            assert row["role"] == role and row["is_active"] == active
            assert row["password_hash"] is None and not row["mfa_enabled"]
            await owner.execute("DELETE FROM user_profiles WHERE id=$1", marker)
        checks.append("existing-email-and-inactive-admin-preserved")

        # Real PostgreSQL failure after profile INSERT must roll back the whole
        # account. Constraint exists only within this disposable fixture.
        await owner.execute("ALTER TABLE user_mfa ADD CONSTRAINT synthetic_bootstrap_fault CHECK (FALSE) NOT VALID")
        try:
            try:
                await bootstrap.provision(owner, fresh(), **target)
            except asyncpg.CheckViolationError:
                pass
            else:
                raise AssertionError("Expected PostgreSQL constraint failure")
        finally:
            await owner.execute("ALTER TABLE user_mfa DROP CONSTRAINT synthetic_bootstrap_fault")
        assert await counts() == baseline
        checks.append("database-write-failure-rolls-back-profile-factor-audit")

        async def contender():
            connection = await asyncpg.connect(owner_url, timeout=10, command_timeout=20)
            try:
                try:
                    return await bootstrap.provision(connection, fresh(), **target)
                except bootstrap.BootstrapError:
                    return None
            finally:
                await connection.close(timeout=5)
        outcomes = await asyncio.gather(contender(), contender())
        assert sum(result is not None for result in outcomes) == 1
        created = next(result for result in outcomes if result is not None)
        row = await owner.fetchrow("SELECT * FROM user_profiles WHERE id=$1::uuid", created["user_id"])
        factor = await owner.fetchrow("SELECT * FROM user_mfa WHERE user_id=$1::uuid", created["user_id"])
        assert row["role"] == "platform_admin" and row["tenant_id"] is None
        assert row["is_verified"] and row["email_verified_at"] and row["is_active"] and row["mfa_enabled"]
        assert verify_password(original.password, row["password_hash"])
        assert factor["enabled"] and factor["verified_at"] and factor["last_used_at"]
        assert decrypt_totp_secret(factor["totp_secret_enc"]) == secret
        used_code = pyotp.TOTP(secret).at(factor["last_used_at"])
        assert verify_totp_step(secret, used_code, last_used_at=factor["last_used_at"]) is None
        assert await owner.fetchval("SELECT count(*) FROM audit_logs WHERE event_id=$1::uuid AND action='first_platform_admin_bootstrapped'", created["audit_event_id"]) == 1
        assert await owner.fetchval("SELECT count(*) FROM security_sessions WHERE user_id=$1::uuid", created["user_id"]) == 0
        assert await owner.fetchval("SELECT count(*) FROM recovery_codes WHERE user_id=$1::uuid", created["user_id"]) == 0
        before = await counts()
        await denied(owner, fresh())
        assert await counts() == before
        checks.append("concurrent-bootstrap-one-mfa-admin-one-durable-audit-no-session")
        await normal_login(runtime_url, owner, original, created["user_id"], factor["last_used_at"], checks)
        return checks
    finally:
        await owner.close(timeout=5)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


async def normal_login(runtime_url, owner, credentials, user_id, consumed_step, checks):
    import asyncpg
    import httpx
    import pyotp
    from app.core import container, db as core_db
    from app.api.v1 import dependencies
    from app.core.postgres_adapter import Client
    from app.main import app
    assert not app.dependency_overrides
    pool = await asyncpg.create_pool(runtime_url, min_size=1, max_size=3,
        init=core_db._register_jsonb_codecs, command_timeout=15)
    saved = (container._container, core_db._pool, core_db._read_pool, dependencies._audit_logger_service)
    local = container.ServiceContainer()
    local._db_pool, local._db_client, local._initialized = pool, Client(pool), True
    container._container, core_db._pool, core_db._read_pool = local, pool, None
    dependencies._audit_logger_service = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:58202",
                headers={"Origin": "http://127.0.0.1:58201", "User-Agent": "Synthetic-First-Admin-Acceptance"}) as client:
            assert (await client.get("/api/v1/admin/tenants")).status_code == 401
            response = await client.post("/api/v1/auth/login", json={"email": credentials.email, "password": credentials.password})
            assert response.status_code == 200
            first = response.json()
            assert first["mfa_required"] and not first.get("access_token") and not client.cookies.get("talky_at")
            assert (await client.get("/api/v1/admin/tenants")).status_code == 401
            replay = await client.post("/api/v1/auth/mfa/verify", json={"challenge_token": first["mfa_challenge_token"],
                "code": pyotp.TOTP(credentials.totp_secret).at(consumed_step)})
            assert replay.status_code == 401 and not client.cookies.get("talky_at")
            # Existing verifier permits ±1 clock step. Use a fresh allowed step;
            # no clock patch, SQL counter reset or manufactured session.
            next_step = max(int(time.time()) // 30, int(consumed_step.timestamp()) // 30 + 1)
            response = await client.post("/api/v1/auth/mfa/verify", json={"challenge_token": first["mfa_challenge_token"],
                "code": pyotp.TOTP(credentials.totp_secret).at(next_step * 30)})
            assert response.status_code == 200 and response.json()["mfa_verified"]
            assert {cookie.name for cookie in client.cookies.jar} >= {"talky_at", "talky_rt", "talky_sid"}
            me = await client.get("/api/v1/auth/me")
            assert me.status_code == 200 and me.json()["id"] == user_id and me.json()["role"] == "platform_admin"
            assert (await client.get("/api/v1/admin/tenants")).status_code == 200
            assert await owner.fetchval("SELECT bool_and(mfa_verified) FROM security_sessions WHERE user_id=$1::uuid", user_id)
        checks.append("normal-password-mfa-replay-denial-session-admin-journey")
    finally:
        audit = dependencies._audit_logger_service
        if audit:
            await asyncio.wait_for(audit.aclose(), 15)
        container._container, core_db._pool, core_db._read_pool, dependencies._audit_logger_service = saved
        await asyncio.wait_for(pool.close(), 15)
