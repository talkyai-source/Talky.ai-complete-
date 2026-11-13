"""F11-02: actual save/startup boundaries, with credentials/providers stubbed.

Root owns serial execution. No provider, socket or production call is made.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import logging
import re
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.domain.models.ai_config import AIProviderConfig
from app.domain.services.credential_resolver import ResolvedCredential


TENANT = "11111111-2222-4333-8444-555555555555"
ACTOR = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
SECRET = "synthetic-model-key-never-log"
DEFAULTS = {
    "groq": "openai/gpt-oss-20b", "cerebras": "gpt-oss-120b",
    "gemini": "gemini-3.8-flash", "openai": "gpt-6-luna",
    "deepseek": "deepseek-flash",
}


class Connection:
    def __init__(self):
        self.depth = 0
        self.tenant = None
        self.saved = []
        self.receipts = []
        self.audit_locked = False

    def is_in_transaction(self):
        return self.depth > 0

    @asynccontextmanager
    async def transaction(self, **_kwargs):
        old = deepcopy((self.tenant, self.saved, self.receipts, self.audit_locked))
        self.depth += 1
        try:
            yield self
        except BaseException:
            self.tenant, self.saved, self.receipts, self.audit_locked = old
            raise
        finally:
            self.depth -= 1
            if not self.depth:
                self.tenant = None
                self.audit_locked = False

    async def execute(self, statement, *args):
        sql = " ".join(statement.split())
        assert self.is_in_transaction()
        if re.fullmatch(r"SET LOCAL (statement_timeout|lock_timeout) = '[1-9][0-9]*ms'", sql):
            return "SET"
        if sql.startswith("SET LOCAL app.current_tenant_id = '"):
            self.tenant = str(UUID(sql.split("'")[1]))
            return "SET"
        if sql.startswith("INSERT INTO tenant_ai_configs"):
            assert str(args[0]) == self.tenant == TENANT
            self.saved.append(args)
            return "INSERT 0 1"
        raise AssertionError(f"Unexpected SQL in model-selection fixture: {sql}")


class Pool:
    def __init__(self):
        self.conn = Connection()

    @asynccontextmanager
    async def acquire(self, **_kwargs):
        yield self.conn


@pytest.fixture
def save_boundary(monkeypatch):
    from app.api.v1.endpoints.ai_options import config as endpoint
    from app.domain.services import credential_resolver

    for provider in DEFAULTS:
        monkeypatch.setenv(f"{provider.upper()}_API_KEY", SECRET)
    resolver = NS(resolve=AsyncMock(return_value=SECRET))
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: resolver)
    monkeypatch.setattr(endpoint, "_get_deepgram_voices_for_current_key",
                        AsyncMock(return_value=[NS(id="aura-zeus-en")]))
    pool = Pool()
    return endpoint, NS(pool=pool), NS(tenant_id=TENANT, id=ACTOR)


@pytest.mark.parametrize("provider,model", [
    ("groq", "openai/gpt-oss-120b"), ("groq", "llama-3.1-8b-instant"),
    ("cerebras", "gemma-4-31b"), ("cerebras", "zai-glm-4.7"),
    ("gemini", "gemini-2.5-flash"), ("gemini", "gemini-3.1-flash-lite-preview"),
    ("openai", "invented-model"), ("deepseek", "invented-model"),
])
async def test_save_rejects_models_absent_from_the_actual_menu_without_writing(save_boundary, provider, model):
    endpoint, client, user = save_boundary
    body = AIProviderConfig(llm_provider=provider, llm_model=model)
    with pytest.raises(HTTPException) as error:
        await endpoint.save_config(body, current_user=user, db_client=client)
    assert error.value.status_code == 400
    assert client.pool.conn.saved == []


@pytest.mark.parametrize("provider,model", DEFAULTS.items())
async def test_save_accepts_each_current_menu_model_with_same_tenant_and_value(save_boundary, provider, model):
    endpoint, client, user = save_boundary
    body = AIProviderConfig(llm_provider=provider, llm_model=model)
    result = await endpoint.save_config(body, current_user=user, db_client=client)
    assert result.config.llm_model == model
    assert len(client.pool.conn.saved) == 1
    assert client.pool.conn.saved[0][:3] == (TENANT, provider, model)


async def test_get_does_not_rewrite_an_old_saved_choice(save_boundary, monkeypatch):
    endpoint, client, user = save_boundary
    saved = AIProviderConfig(llm_provider="groq", llm_model="llama-3.1-8b-instant")
    monkeypatch.setattr(endpoint, "_fetch_tenant_config", AsyncMock(return_value=saved))
    result = await endpoint.get_config(current_user=user, db_client=client)
    assert result.llm_model == "llama-3.1-8b-instant"
    assert client.pool.conn.saved == []


@pytest.fixture
def runtime(monkeypatch):
    from app.domain.services import audit_logger, credential_resolver
    from app.domain.services.voice_orchestrator import VoiceOrchestrator
    from app.infrastructure.llm.factory import LLMFactory

    monkeypatch.setenv("LLM_FAILOVER_ENABLED", "false")
    credential = ResolvedCredential(SECRET, source="tenant", tenant_id=TENANT,
                                    credential_id="bbbbbbbb-2222-4333-8444-555555555555")
    resolver = NS(resolve_with_metadata=AsyncMock(return_value=credential))
    monkeypatch.setattr(credential_resolver, "get_credential_resolver", lambda: resolver)
    provider = NS(initialize=AsyncMock(), cleanup=AsyncMock())
    factory = Mock(return_value=provider)
    monkeypatch.setattr(LLMFactory, "create", factory)
    pool = Pool()
    audit_failure = []

    class Audit:
        def __init__(self, actual_pool, **_kwargs):
            assert actual_pool is pool

        async def lock_transaction(self, conn):
            assert conn is pool.conn and conn.is_in_transaction()
            assert conn.tenant == TENANT
            conn.audit_locked = True

        async def log_in_transaction(self, conn, **entry):
            assert conn is pool.conn and conn.is_in_transaction() and conn.audit_locked
            assert str(entry["tenant_id"]) == TENANT
            assert entry.get("actor_type") == "system"
            if audit_failure:
                raise audit_failure[0]
            conn.receipts.append(entry)
            return UUID(ACTOR)

    monkeypatch.setattr(audit_logger, "AuditLogger", Audit)
    return NS(orchestrator=VoiceOrchestrator(NS(pool=pool)), provider=provider,
              factory=factory, resolver=resolver, pool=pool, audit_failure=audit_failure,
              credential=credential)


@pytest.mark.parametrize("provider,expected", DEFAULTS.items())
async def test_unknown_saved_model_uses_same_provider_default_and_audits_without_mutating_profile(runtime, provider, expected, caplog):
    from app.domain.services.voice_orchestrator import VoiceSessionConfig

    original = VoiceSessionConfig(session_type="sip", tenant_id=TENANT,
                                 llm_provider_type=provider, llm_model="retired-synthetic-model",
                                 llm_temperature=0.4, llm_max_tokens=123)
    before = deepcopy(vars(original))
    with caplog.at_level(logging.ERROR):
        result = await runtime.orchestrator._create_llm_provider(original)
    assert result is runtime.provider
    runtime.factory.assert_called_once_with(provider, config={})
    init = runtime.provider.initialize.await_args.args[0]
    assert init["model"] == expected
    assert init["api_key"] == SECRET
    assert init["temperature"] == 0.4 and init["max_tokens"] == 123
    assert init["observation_context"]["tenant_id"] == TENANT
    assert runtime.resolver.resolve_with_metadata.await_args.kwargs["tenant_id"] == TENANT
    assert vars(original) == before
    assert len(runtime.pool.conn.receipts) == 1
    assert expected in repr(runtime.pool.conn.receipts)
    assert "model" in caplog.text.lower() and "fallback" in caplog.text.lower()
    assert SECRET not in caplog.text + repr(runtime.pool.conn.receipts)


@pytest.mark.parametrize("provider,model", [
    *DEFAULTS.items(), ("groq", "openai/gpt-oss-120b"), ("gemini", "gemini-2.5-flash"),
])
async def test_known_model_without_retirement_evidence_remains_exact(runtime, provider, model):
    from app.domain.services.voice_orchestrator import VoiceSessionConfig

    config = VoiceSessionConfig(tenant_id=TENANT, llm_provider_type=provider, llm_model=model)
    assert await runtime.orchestrator._create_llm_provider(config) is runtime.provider
    assert runtime.provider.initialize.await_args.args[0]["model"] == model
    assert runtime.pool.conn.receipts == []


@pytest.mark.parametrize("provider", ["unknown", ""])
async def test_unknown_provider_cannot_silently_switch_vendor(runtime, provider):
    from app.domain.services.voice_orchestrator import VoiceSessionConfig

    with pytest.raises(ValueError):
        await runtime.orchestrator._create_llm_provider(
            VoiceSessionConfig(tenant_id=TENANT, llm_provider_type=provider, llm_model="unknown"))
    runtime.factory.assert_not_called()
    runtime.resolver.resolve_with_metadata.assert_not_awaited()


async def test_audit_outage_is_visible_but_does_not_prevent_model_fallback(runtime, caplog):
    from app.domain.services.voice_orchestrator import VoiceSessionConfig

    runtime.audit_failure.append(RuntimeError(f"db transport contained {SECRET}"))
    with caplog.at_level(logging.ERROR):
        await runtime.orchestrator._create_llm_provider(
            VoiceSessionConfig(tenant_id=TENANT, llm_provider_type="groq", llm_model="retired"))
    assert runtime.provider.initialize.await_args.args[0]["model"] == DEFAULTS["groq"]
    assert runtime.pool.conn.receipts == []
    assert "audit" in caplog.text.lower() and SECRET not in caplog.text


async def test_audit_cancellation_is_not_swallowed_as_a_provider_or_catalog_error(runtime):
    from app.domain.services.voice_orchestrator import VoiceSessionConfig

    runtime.audit_failure.append(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await runtime.orchestrator._create_llm_provider(
            VoiceSessionConfig(tenant_id=TENANT, llm_provider_type="groq", llm_model="retired"))
    assert runtime.pool.conn.receipts == []


async def test_secondary_selection_receives_actual_fallback_primary_without_changing_original(runtime, monkeypatch):
    from app.domain.services.voice_orchestrator import VoiceSessionConfig

    monkeypatch.setenv("LLM_FAILOVER_ENABLED", "true")
    build_secondary = AsyncMock(return_value=None)
    monkeypatch.setattr(runtime.orchestrator, "_build_secondary_llm_provider", build_secondary)
    original = VoiceSessionConfig(tenant_id=TENANT, llm_provider_type="groq", llm_model="retired")
    await runtime.orchestrator._create_llm_provider(original)
    actual = build_secondary.await_args.args[0]
    assert actual.llm_model == DEFAULTS["groq"]
    assert build_secondary.await_args.kwargs["primary_provider_type"] == "groq"
    assert original.llm_model == "retired" and actual is not original

