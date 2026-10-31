"""Shared PostgreSQL fixture for the support system's security and integration tests.

Only an explicitly supplied disposable ``*_test`` database on localhost is
accepted. The application runs as a fresh NOSUPERUSER NOBYPASSRLS role, so row
level security is enforced exactly as for a restricted production role; the
code's explicit tenant predicates are tested on top of that. Two tenants and
five people are created, and only those rows are removed afterwards.
"""
from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_current_user, get_db_pool
from app.api.v1.endpoints.auth import limiter
from app.api.v1.endpoints.support import admin_router, router
from app.core.config import get_settings
from app.core.db import _register_jsonb_codecs
from app.core.error_handlers import register_error_handlers
from app.domain.services.audit_logger import AuditLogger

PEOPLE = {
    "a_user": ("user", "a"),
    "a_colleague": ("user", "a"),
    "a_admin": ("tenant_admin", "a"),
    "a_readonly": ("readonly", "a"),
    "b_user": ("user", "b"),
    "staff": ("platform_admin", None),
}


def require_test_dsn() -> str:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit TEST_DATABASE_URL is required")
    target = urlparse(dsn)
    if target.hostname not in {"127.0.0.1", "localhost"} or not target.path.removeprefix("/").endswith("_test"):
        pytest.fail("Support PostgreSQL tests require an explicit disposable local *_test database")
    return dsn


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    register_error_handlers(app)
    app.state.limiter = limiter
    return app


@pytest_asyncio.fixture
async def support_db(monkeypatch):
    dsn = require_test_dsn()
    monkeypatch.setenv("SUPPORT_ENABLED", "true")
    monkeypatch.delenv("SUPPORT_TENANT_ALLOWLIST", raising=False)
    monkeypatch.setenv("SUPPORT_ATTACHMENT_LOCAL_DIR", str(_attachment_dir()))
    monkeypatch.setattr(limiter, "enabled", False)
    if not os.getenv("JWT_SECRET"):
        monkeypatch.setenv("JWT_SECRET", secrets.token_hex(32))  # per-run value, never a literal
    get_settings.cache_clear()
    owner = await asyncpg.connect(dsn, timeout=5)
    role = "support_app_" + uuid4().hex
    tenants = {"a": uuid4(), "b": uuid4()}
    users = {name: uuid4() for name in PEOPLE}
    pool = None
    audit = None
    role_created = False
    try:
        await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        role_created = True
        await owner.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
        await owner.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
        for key, tenant_id in tenants.items():
            await owner.execute(
                "INSERT INTO tenants (id, business_name, plan_id, subscription_status, status) "
                "VALUES ($1, $2, NULL, 'active', 'active')", tenant_id, f"Support test tenant {key.upper()}")
        for name, (profile_role, tenant_key) in PEOPLE.items():
            await owner.execute(
                """INSERT INTO user_profiles (id, email, name, tenant_id, role, is_active, is_verified, email_verified_at)
                   VALUES ($1, $2, $3, $4, $5, TRUE, TRUE, now())""",
                users[name], f"{users[name]}@example.invalid", f"Test {name}",
                tenants[tenant_key] if tenant_key else None, profile_role)

        async def setup(conn):
            await conn.execute(f'SET ROLE "{role}"')
            await conn.execute("SET statement_timeout = '20s'")

        # Same JSONB codecs as the production pool (app/core/db.py).
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=8, timeout=5, command_timeout=20,
                                         init=_register_jsonb_codecs, setup=setup)
        async with pool.acquire() as conn:
            flags = await conn.fetchrow("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            assert tuple(flags.values()) == (False, False)
        audit = AuditLogger(pool)
        current = {"user": None}
        app = build_app()
        app.dependency_overrides[get_db_pool] = lambda: pool
        app.dependency_overrides[get_audit_logger] = lambda: audit
        app.dependency_overrides[get_current_user] = lambda: current["user"]

        def person(name: str) -> CurrentUser:
            profile_role, tenant_key = PEOPLE[name]
            return CurrentUser(id=str(users[name]), email=f"{users[name]}@example.invalid", role=profile_role,
                               tenant_id=str(tenants[tenant_key]) if tenant_key else None, name=f"Test {name}")

        @asynccontextmanager
        async def client(name: str):
            current["user"] = person(name)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                yield http

        def as_user(name: str) -> None:
            current["user"] = person(name)

        async def http(name: str, method: str, path: str, **kwargs) -> httpx.Response:
            current["user"] = person(name)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as session:
                return await session.request(method, f"/api/v1{path}", **kwargs)

        async def seed_campaign(tenant_key: str, name: str = "Synthetic campaign") -> UUID:
            campaign_id = uuid4()
            await owner.execute("SET app.bypass_rls = 'on'")
            await owner.execute("INSERT INTO campaigns (id, tenant_id, name, status) VALUES ($1, $2, $3, 'draft')",
                                campaign_id, tenants[tenant_key], name)
            return campaign_id

        yield SimpleNamespace(pool=pool, owner=owner, tenants=tenants, users=users, app=app, http=http,
                              client=client, as_user=as_user, person=person, audit=audit,
                              seed_campaign=seed_campaign)
    finally:
        if audit is not None:
            try:
                await audit.aclose()
            except Exception:
                pass
        if pool is not None:
            await pool.close()
        async with owner.transaction():
            await owner.execute("SET LOCAL app.bypass_rls = 'on'")
            tenant_ids, user_ids = list(tenants.values()), list(users.values())
            await owner.execute("DELETE FROM audit_logs WHERE actor_id = ANY($1::uuid[]) OR tenant_id = ANY($2::uuid[])",
                                user_ids, tenant_ids)
            await owner.execute("DELETE FROM support_request_receipts WHERE actor_user_id = ANY($1::uuid[])", user_ids)
            await owner.execute("DELETE FROM support_ticket_attachments WHERE uploaded_by = ANY($1::uuid[])", user_ids)
            await owner.execute("DELETE FROM support_kb_articles WHERE updated_by = ANY($1::uuid[])", user_ids)
            await owner.execute("DELETE FROM user_profiles WHERE id = ANY($1::uuid[])", user_ids)
            await owner.execute("DELETE FROM tenants WHERE id = ANY($1::uuid[])", tenant_ids)
        get_settings.cache_clear()
        if role_created:
            await owner.execute(f'DROP OWNED BY "{role}"')
            await owner.execute(f'DROP ROLE "{role}"')
        await owner.close()


def _attachment_dir():
    import tempfile
    from pathlib import Path

    path = Path(tempfile.gettempdir()) / "talky-support-test-attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_request_id() -> str:
    return str(uuid4())
