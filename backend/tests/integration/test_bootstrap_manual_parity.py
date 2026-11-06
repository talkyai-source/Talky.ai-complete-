"""Opt-in real-PostgreSQL behavior for the late bootstrap/manual repair.

Requires an explicitly supplied loopback, test-named TEST_DATABASE_URL with
database-creation privileges. Only generated UUID databases are modified/dropped.
This small contract fixture does not replace the full two-bootstrap dump diff.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


BACKEND = Path(__file__).resolve().parents[2]
REVISION = BACKEND / "Alembic" / "versions" / "0087_bootstrap_manual_parity.py"
pytestmark = pytest.mark.integration

BASE = (
    'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
    "CREATE TABLE tenants (id uuid PRIMARY KEY)",
    "CREATE TABLE user_profiles (id uuid PRIMARY KEY)",
    "CREATE TABLE audit_logs (event_id uuid PRIMARY KEY)",
    "CREATE TABLE suspension_events (suspension_id uuid PRIMARY KEY)",
    "CREATE TABLE leads (id uuid PRIMARY KEY, campaign_id uuid, phone_number text, status varchar(20))",
    """CREATE TABLE refresh_tokens (id uuid PRIMARY KEY, revoked_reason text,
        CONSTRAINT refresh_tokens_revoked_reason_check CHECK (revoked_reason IS NULL OR
            revoked_reason IN ('rotated','reuse_detected','logout','admin','expired')))""",
)
CURRENT_REASONS = (
    "rotated", "reuse_detected", "logout", "admin", "expired", "password_change",
    "password_reset", "mfa_disabled", "expired_with_subsequent_use",
)


def migration():
    assert REVISION.is_file(), "F14-02 is missing the guarded late bootstrap repair"
    spec = importlib.util.spec_from_file_location("pg_bootstrap_manual_parity", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def admin_dsn():
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("not run on PostgreSQL: explicit TEST_DATABASE_URL is required")
    parsed = urlsplit(value.replace("postgresql+asyncpg://", "postgresql://", 1))
    if parsed.scheme != "postgresql" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("bootstrap repair requires a loopback PostgreSQL test DSN", pytrace=False)
    if parsed.query or parsed.fragment:
        pytest.fail("bootstrap fixture DSN must not contain connection-routing overrides", pytrace=False)
    if not re.search(r"(?:^|[_-])(?:test|ci|tmp)(?:[_-]|$)", parsed.path.lstrip("/"), re.I):
        pytest.fail("bootstrap repair requires a test-named provisioning database", pytrace=False)
    return urlunsplit(parsed)


@pytest.fixture
async def database():
    dsn = admin_dsn()
    name = f"qa_bootstrap_parity_{uuid4().hex}_test"
    assert re.fullmatch(r"qa_bootstrap_parity_[a-f0-9]{32}_test", name)
    admin = await asyncpg.connect(dsn, timeout=5, command_timeout=60)
    created = False
    engine = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        created = True
        parsed = urlsplit(dsn)
        target = urlunsplit(parsed._replace(path=f"/{name}"))
        engine = create_async_engine(
            "postgresql+asyncpg://", connect_args={"dsn": target, "timeout": 5, "command_timeout": 120},
            poolclass=NullPool,
        )
        async with engine.begin() as conn:
            for statement in BASE:
                await conn.execute(text(statement))
        yield engine
    finally:
        if engine is not None:
            await engine.dispose()
        try:
            if created:
                # No FORCE and no unrelated connection termination.
                await admin.execute(f'DROP DATABASE "{name}"')
        finally:
            await admin.close()


async def upgrade(engine):
    module = migration()
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL app.bypass_rls='true'"))

        def apply(sync):
            with Operations.context(MigrationContext.configure(sync)):
                module.upgrade()

        await conn.run_sync(apply)


async def test_missing_runtime_tables_support_current_call_and_credential_contracts(database):
    await upgrade(database)
    tenant, user = uuid4(), uuid4()
    async with database.begin() as conn:
        await conn.execute(text("SET LOCAL app.bypass_rls='true'"))
        await conn.execute(text("INSERT INTO tenants(id,active_telephony_provider,assistant_model) VALUES (:id,'sip','synthetic-model')"), {"id": tenant})
        await conn.execute(text("INSERT INTO user_profiles(id) VALUES (:id)"), {"id": user})
        await conn.execute(text("INSERT INTO stream_events(tenant_id,category,title) VALUES (:tenant,'call','synthetic event')"), {"tenant": tenant})
        for state in ("disabled", "disabled", "active"):
            await conn.execute(text("""INSERT INTO tenant_ai_credentials
                (tenant_id,provider,credential_kind,encrypted_key,status)
                VALUES (:tenant,'synthetic','api_key','ciphertext-fixture',:state)"""), {"tenant": tenant, "state": state})
        await conn.execute(text("""INSERT INTO tenant_telephony_credentials
            (tenant_id,provider,credentials_encrypted) VALUES (:tenant,'vonage','ciphertext-fixture')"""), {"tenant": tenant})
        await conn.execute(text("""INSERT INTO emergency_access_requests
            (requestor_id,scenario,justification,requested_access,expires_at)
            VALUES (:user,'synthetic','fixture',ARRAY['read'],NOW()+INTERVAL '1 hour')"""), {"user": user})
        for table in ("worker_status", "audit_chain_state", "audit_event_types", "rate_limit_events", "suspension_propagation_queue"):
            assert (await conn.execute(text("SELECT to_regclass(:table)"), {"table": f"public.{table}"})).scalar() is not None
        assert (await conn.execute(text("SELECT count(*) FROM tenant_ai_credentials WHERE tenant_id=:tenant AND status='disabled'"), {"tenant": tenant})).scalar_one() == 2
        for table in ("stream_events", "tenant_ai_credentials", "tenant_telephony_credentials"):
            flags = (await conn.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)"), {"table": f"public.{table}"})).one()
            assert tuple(flags) == (True, True)
    with pytest.raises(DBAPIError):
        async with database.begin() as conn:
            await conn.execute(text("SET LOCAL app.bypass_rls='true'"))
            await conn.execute(text("""INSERT INTO tenant_ai_credentials
                (tenant_id,provider,credential_kind,encrypted_key,status)
                VALUES (:tenant,'synthetic','api_key','second-active','active')"""), {"tenant": tenant})


async def test_already_current_refresh_constraint_is_adopted_without_replacement(database):
    allowed = ",".join(f"'{value}'" for value in CURRENT_REASONS)
    async with database.begin() as conn:
        await conn.execute(text("ALTER TABLE refresh_tokens DROP CONSTRAINT refresh_tokens_revoked_reason_check"))
        await conn.execute(text(f"""ALTER TABLE refresh_tokens ADD CONSTRAINT refresh_tokens_revoked_reason_check
            CHECK (revoked_reason IS NULL OR revoked_reason IN ({allowed}))"""))
        await conn.execute(text("INSERT INTO refresh_tokens VALUES (:id,'password_change')"), {"id": uuid4()})
        before = (await conn.execute(text("SELECT oid,pg_get_constraintdef(oid),convalidated FROM pg_constraint WHERE conrelid='refresh_tokens'::regclass AND conname='refresh_tokens_revoked_reason_check'"))).one()
    await upgrade(database)
    async with database.connect() as conn:
        after = (await conn.execute(text("SELECT oid,pg_get_constraintdef(oid),convalidated FROM pg_constraint WHERE conrelid='refresh_tokens'::regclass AND conname='refresh_tokens_revoked_reason_check'"))).one()
        assert tuple(after) == tuple(before)
        assert (await conn.execute(text("SELECT revoked_reason FROM refresh_tokens"))).scalars().all() == ["password_change"]


async def test_reapply_preserves_existing_rows_and_object_identities(database):
    await upgrade(database)
    async with database.begin() as conn:
        await conn.execute(text("INSERT INTO worker_status(name,processed_count) VALUES ('synthetic',17)"))
        before = (await conn.execute(text("SELECT oid,relname FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY relname"))).all()
    await upgrade(database)
    async with database.connect() as conn:
        after = (await conn.execute(text("SELECT oid,relname FROM pg_class WHERE relnamespace='public'::regnamespace ORDER BY relname"))).all()
        assert after == before
        assert (await conn.execute(text("SELECT processed_count FROM worker_status WHERE name='synthetic'"))).scalar_one() == 17


async def test_incompatible_existing_table_refuses_atomically(database):
    async with database.begin() as conn:
        await conn.execute(text("CREATE TABLE stream_events(id text PRIMARY KEY, tenant_id uuid)"))
        await conn.execute(text("INSERT INTO stream_events VALUES ('preserve-me',NULL)"))
    with pytest.raises(DBAPIError):
        await upgrade(database)
    async with database.connect() as conn:
        assert (await conn.execute(text("SELECT id FROM stream_events"))).scalars().all() == ["preserve-me"]
        assert (await conn.execute(text("SELECT to_regclass('public.worker_status')"))).scalar() is None
        assert (await conn.execute(text("SELECT to_regclass('public.tenant_ai_credentials')"))).scalar() is None


@pytest.mark.parametrize("parent,delete_action", [
    ("user_profiles", "CASCADE"),  # Same UUID type and action; wrong owner table.
    ("tenants", "RESTRICT"),      # Correct owner table; wrong deletion contract.
])
async def test_existing_foreign_key_must_match_parent_and_delete_action(database, parent, delete_action):
    await upgrade(database)
    tenant = uuid4()
    async with database.begin() as conn:
        await conn.execute(text("SET LOCAL app.bypass_rls='true'"))
        await conn.execute(text("INSERT INTO tenants(id) VALUES (:id)"), {"id": tenant})
        await conn.execute(text("INSERT INTO user_profiles(id) VALUES (:id)"), {"id": tenant})
        await conn.execute(text("""INSERT INTO stream_events(tenant_id,category,title)
            VALUES (:tenant,'call','preserve-fk-fixture')"""), {"tenant": tenant})
        await conn.execute(text("ALTER TABLE stream_events DROP CONSTRAINT stream_events_tenant_id_fkey"))
        # Identifiers/actions come only from the two fixed parametrized cases.
        await conn.execute(text(f"""ALTER TABLE stream_events ADD CONSTRAINT stream_events_tenant_id_fkey
            FOREIGN KEY (tenant_id) REFERENCES public.{parent}(id) ON DELETE {delete_action}"""))
        before = (await conn.execute(text("""SELECT oid,pg_get_constraintdef(oid),convalidated
            FROM pg_constraint WHERE conrelid=to_regclass('public.stream_events')
            AND conname='stream_events_tenant_id_fkey'"""))).one()
    with pytest.raises(DBAPIError, match="0087 refuses incompatible columns or constraints on stream_events"):
        await upgrade(database)
    async with database.begin() as conn:
        await conn.execute(text("SET LOCAL app.bypass_rls='true'"))
        after = (await conn.execute(text("""SELECT oid,pg_get_constraintdef(oid),convalidated
            FROM pg_constraint WHERE conrelid=to_regclass('public.stream_events')
            AND conname='stream_events_tenant_id_fkey'"""))).one()
        assert tuple(after) == tuple(before), "Refusal must not replace an unknown adopted FK"
        assert (await conn.execute(text("SELECT title FROM stream_events WHERE tenant_id=:tenant"),
                                   {"tenant": tenant})).scalars().all() == ["preserve-fk-fixture"]


async def test_serial_column_cannot_silently_adopt_generated_always_identity(database):
    await upgrade(database)
    async with database.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_chain_state ALTER COLUMN id DROP DEFAULT"))
        await conn.execute(text("ALTER SEQUENCE audit_chain_state_id_seq OWNED BY NONE"))
        await conn.execute(text("ALTER TABLE audit_chain_state ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY"))
        inserted_id = (await conn.execute(text("""INSERT INTO audit_chain_state(last_event_hash)
            VALUES ('preserve-identity-fixture') RETURNING id"""))).scalar_one()
        before = (await conn.execute(text("""SELECT attidentity::text AS attidentity,attgenerated::text AS attgenerated
            FROM pg_attribute WHERE attrelid=to_regclass('public.audit_chain_state') AND attname='id'"""))).one()
        assert before.attidentity == "a", "The planted column must reject ordinary explicit-ID inserts"
    with pytest.raises(DBAPIError, match="0087 refuses incompatible columns or constraints on audit_chain_state"):
        await upgrade(database)
    async with database.connect() as conn:
        after = (await conn.execute(text("""SELECT attidentity::text AS attidentity,attgenerated::text AS attgenerated
            FROM pg_attribute WHERE attrelid=to_regclass('public.audit_chain_state') AND attname='id'"""))).one()
        assert tuple(after) == tuple(before)
        assert (await conn.execute(text("SELECT last_event_hash FROM audit_chain_state WHERE id=:id"),
                                   {"id": inserted_id})).scalar_one() == "preserve-identity-fixture"
