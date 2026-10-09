"""AssemblyAI's migration and tenant persistence on disposable PostgreSQL.

The new DDL runs through SQLAlchemy/asyncpg, matching Alembic's prepared
statement boundary. Only its table schema is redirected to a random fixture
schema; shared application tables and the running PostgreSQL service are never
changed. API reads/writes use a NOSUPERUSER NOBYPASSRLS role and the real
acquire_with_tenant helper, not a transport double.
"""
from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.v1.endpoints.ai_options import config as endpoint
from app.api.v1.endpoints.ai_options._shared import _fetch_tenant_config, _upsert_tenant_config
from app.core.db import _register_jsonb_codecs
from app.core.db_utils import acquire_with_tenant
from app.domain.models.ai_config import AIProviderConfig
from app.domain.services import credential_resolver
from app.domain.services.tenant_ai_config_resolver import TenantAIConfigResolver

MIGRATION = importlib.import_module("Alembic.versions.0065_assemblyai_settings")
RLS_MIGRATION = importlib.import_module("Alembic.versions.0038_tenant_table_rls_backfill")
BACKEND = Path(__file__).resolve().parents[2]


def _dsn_or_skip() -> str:
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL is required")
    parsed = urlparse(dsn)
    if (
        parsed.scheme not in {"postgresql", "postgres"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or not re.search(r"(?:^|[_-])(?:test|ci|tmp|ephemeral)(?:[_-]|$)", parsed.path[1:])
    ):
        pytest.fail("Only an explicitly named disposable localhost test database is allowed")
    return dsn


async def _apply_migration(db, direction, monkeypatch):
    statements = []
    with monkeypatch.context() as patch:
        patch.setattr(MIGRATION, "op", SimpleNamespace(execute=lambda sql: statements.append(str(sql))))
        getattr(MIGRATION, direction)()
    async with db.engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement.replace(
                "public.tenant_ai_configs", f"{db.schema}.tenant_ai_configs",
            )))


@pytest_asyncio.fixture(params=[False, True], ids=["raw-json", "production-json-codec"])
async def assembly_db(request, monkeypatch):
    dsn = _dsn_or_skip()
    suffix = uuid4().hex
    schema, role = f"assembly_{suffix}", f"assembly_role_{suffix}"
    admin = await asyncpg.connect(dsn, timeout=5, command_timeout=10)
    engine = create_async_engine(re.sub(r"^postgres(?:ql)?://", "postgresql+asyncpg://", dsn))
    pool = None
    db = SimpleNamespace(admin=admin, engine=engine, schema=schema, role=role, pool=None)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
        await admin.execute(f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS")
        # Preserve the actual baseline types/nullability/defaults. Later STT /
        # Realtime columns are the compatibility additions from migration 0022.
        baseline = (BACKEND / "database/schema/baseline_2026-06-02.sql").read_text()
        match = re.search(r"CREATE TABLE public\.tenant_ai_configs \(.*?\n\);", baseline, re.S)
        assert match is not None
        await admin.execute(match.group().replace("public.tenant_ai_configs", f"{schema}.tenant_ai_configs"))
        await admin.execute(f"""ALTER TABLE {schema}.tenant_ai_configs
            ADD UNIQUE (tenant_id),
            ADD COLUMN stt_engine TEXT NOT NULL DEFAULT 'deepgram_flux',
            ADD COLUMN pipeline_mode TEXT NOT NULL DEFAULT 'cascaded',
            ADD COLUMN realtime_model TEXT,
            ADD COLUMN realtime_voice TEXT,
            ADD COLUMN realtime_settings JSONB""")
        # Seed a real legacy row before upgrade to prove additive migration.
        db.legacy_tenant = str(uuid4())
        await admin.execute(f"INSERT INTO {schema}.tenant_ai_configs (tenant_id) VALUES ($1)", db.legacy_tenant)
        await admin.execute(f"ALTER TABLE {schema}.tenant_ai_configs ENABLE ROW LEVEL SECURITY")
        await admin.execute(f"ALTER TABLE {schema}.tenant_ai_configs FORCE ROW LEVEL SECURITY")
        policy = RLS_MIGRATION._TENANT_POLICY
        await admin.execute(f"CREATE POLICY tenant_scope ON {schema}.tenant_ai_configs USING ({policy}) WITH CHECK ({policy})")
        await _apply_migration(db, "upgrade", monkeypatch)
        await admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO {role}")
        await admin.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON {schema}.tenant_ai_configs TO {role}")

        async def setup(conn):
            # asyncpg resets SET ROLE when a connection returns to the pool.
            # setup, not init, proves every checkout uses the application role.
            await conn.execute(f"SET ROLE {role}")

        pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=2, timeout=5, command_timeout=10,
            setup=setup, init=_register_jsonb_codecs if request.param else None,
            server_settings={"search_path": schema},
        )
        db.pool = pool
        yield db
    finally:
        if pool is not None:
            await pool.close()
        await engine.dispose()
        await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.execute(f"DROP ROLE IF EXISTS {role}")
        await admin.close()


async def test_real_database_api_roundtrip_all_modes_and_next_call_resolution(assembly_db, monkeypatch):
    db = assembly_db
    resolve = AsyncMock(return_value="synthetic-test-key")
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: SimpleNamespace(resolve=resolve))
    monkeypatch.setattr(endpoint, "_get_deepgram_voices_for_current_key", AsyncMock(return_value=[SimpleNamespace(id="aura-2-zeus-en")]))
    user = SimpleNamespace(tenant_id=str(uuid4()))
    resolver = TenantAIConfigResolver()

    async def lookup(tenant_id):
        async with acquire_with_tenant(db.pool, tenant_id) as conn:
            return await _fetch_tenant_config(conn, tenant_id)

    resolver.set_db_lookup(lookup)
    for mode in ("min_latency", "balanced", "max_accuracy"):
        selected = AIProviderConfig(stt_engine="assemblyai", tts_voice_id="aura-2-zeus-en", assemblyai_settings={
            "mode": mode, "region": "eu", "min_turn_silence": 700, "max_turn_silence": 1900,
            "interruption_delay": 200, "vad_threshold": .4, "include_partial_turns": False,
            "prompt": "English booking call.", "keyterms_prompt": ["Talky", "Alyssa"],
            "auto_agent_context": False, "previous_context_n_turns": 7, "language_detection": True,
            "voice_focus": "near-field", "voice_focus_threshold": .6, "domain": "medical-v1",
            "speaker_labels": True, "max_speakers": 2, "speaker_labels_revision_interval_ms": 300000,
            "redact_pii": True, "redact_pii_policies": ["credit_card_number"], "redact_pii_sub": "entity_name",
            "filter_profanity": True, "session_heartbeat": True, "inactivity_timeout": 30,
        })
        saved = await endpoint.save_config(selected, user, db)
        reloaded = await endpoint.get_config(user, db)
        next_call = await resolver.for_tenant_async(user.tenant_id, require_available=True)
        assert saved.config.model_dump() == reloaded.model_dump() == next_call.model_dump() == selected.model_dump()
        assert next_call.stt_provider == "assemblyai"
        assert next_call.stt_model == "universal-3-6-pro"
        assert next_call.stt_language == "en"

    # Keep advanced choices while Flux is active; switching back restores them.
    flux = AIProviderConfig(tts_voice_id="aura-2-zeus-en", assemblyai_settings=selected.assemblyai_settings)
    await endpoint.save_config(flux, user, db)
    restored = await endpoint.get_config(user, db)
    assert restored.model_dump() == flux.model_dump()
    back = AIProviderConfig(**{**restored.model_dump(), "stt_engine": "assemblyai"})
    assert back.assemblyai_settings == selected.assemblyai_settings

    # Migration did not rewrite the old tenant's choices or enable AssemblyAI.
    old = await endpoint.get_config(SimpleNamespace(tenant_id=db.legacy_tenant), db)
    assert old.stt_engine == "deepgram_flux" and old.assemblyai_settings is None


async def test_real_non_superuser_rls_separates_settings_and_rejects_cross_tenant_writes(assembly_db):
    db = assembly_db
    tenant_a, tenant_b = str(uuid4()), str(uuid4())
    config_a = AIProviderConfig(stt_engine="assemblyai", assemblyai_settings={"mode": "max_accuracy"})
    async with acquire_with_tenant(db.pool, tenant_a) as conn:
        role = await conn.fetchrow("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        assert role["rolsuper"] is False and role["rolbypassrls"] is False
        await _upsert_tenant_config(conn, tenant_a, config_a)
    async with acquire_with_tenant(db.pool, tenant_b) as conn:
        await _upsert_tenant_config(conn, tenant_b, AIProviderConfig())
        assert await _fetch_tenant_config(conn, tenant_a) is None
        own = await _fetch_tenant_config(conn, tenant_b)
        assert own.stt_engine == "deepgram_flux" and own.assemblyai_settings is None
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with acquire_with_tenant(db.pool, tenant_b) as conn:
            await _upsert_tenant_config(conn, tenant_a, AIProviderConfig())
    async with acquire_with_tenant(db.pool, tenant_a) as conn:
        assert (await _fetch_tenant_config(conn, tenant_a)).model_dump() == config_a.model_dump()
    async with db.pool.acquire() as conn:
        # Tenant context cannot survive pool release into a subsequent request.
        assert await conn.fetchval("SELECT COUNT(*) FROM tenant_ai_configs") == 0


async def test_actual_constraints_and_rollback_preserve_saved_assemblyai_preferences(assembly_db, monkeypatch):
    db = assembly_db
    tenant = str(uuid4())
    async with acquire_with_tenant(db.pool, tenant) as conn:
        await _upsert_tenant_config(conn, tenant, AIProviderConfig(stt_engine="assemblyai"))
    for update in (
        "stt_language='es'", "stt_model='u3-rt-pro'", "stt_provider='deepgram'",
        "assemblyai_settings='[]'::jsonb", "assemblyai_settings='null'::jsonb",
    ):
        with pytest.raises(asyncpg.CheckViolationError):
            async with acquire_with_tenant(db.pool, tenant) as conn:
                await conn.execute(f"UPDATE tenant_ai_configs SET {update} WHERE tenant_id=$1", tenant)
    with pytest.raises(DBAPIError, match="Export and clear AssemblyAI"):
        await _apply_migration(db, "downgrade", monkeypatch)
    async with acquire_with_tenant(db.pool, tenant) as conn:
        # Even dormant controls must not disappear during rollback.
        await _upsert_tenant_config(conn, tenant, AIProviderConfig(assemblyai_settings={"mode": "max_accuracy"}))
    with pytest.raises(DBAPIError, match="Export and clear AssemblyAI"):
        await _apply_migration(db, "downgrade", monkeypatch)
    async with acquire_with_tenant(db.pool, tenant) as conn:
        await _upsert_tenant_config(conn, tenant, AIProviderConfig())
    await _apply_migration(db, "downgrade", monkeypatch)
    assert not await db.admin.fetchval("""SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema=$1 AND table_name='tenant_ai_configs' AND column_name='assemblyai_settings'
    )""", db.schema)
    await _apply_migration(db, "upgrade", monkeypatch)
    async with acquire_with_tenant(db.pool, tenant) as conn:
        assert (await _fetch_tenant_config(conn, tenant)).stt_engine == "deepgram_flux"
