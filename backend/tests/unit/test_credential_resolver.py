"""T1.1 — per-tenant AI credential resolver.

Covers the three-layer resolution: tenant row → env var → None, and
the fail-safe behaviours (bad ciphertext, DB down, missing pool).
"""
from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.credential_resolver import (
    CredentialResolver,
    env_var_for_provider,
    resolve_sync_env_only,
)


# ──────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────

class _FakeConn:
    def __init__(self, row: Any = None, raise_on_fetch: Optional[Exception] = None):
        self._row = row
        self._raise = raise_on_fetch

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def fetchrow(self, *args, **kwargs):
        if self._raise:
            raise self._raise
        return self._row

    async def execute(self, *args, **kwargs):
        return "OK"


class _FakePool:
    def __init__(self, row: Any = None, raise_on_fetch: Optional[Exception] = None):
        self._row = row
        self._raise = raise_on_fetch

    def acquire(self):
        return _FakeConn(self._row, self._raise)


class _FakeEncryption:
    def __init__(self, plaintext: Optional[str] = "plain-key-xyz"):
        self._plaintext = plaintext

    def encrypt(self, p):
        return f"ENC:{p}"

    def decrypt(self, c):
        if self._plaintext is None:
            raise RuntimeError("bad ciphertext")
        return self._plaintext


# ──────────────────────────────────────────────────────────────────────────
# Env resolution (the old single-tenant path)
# ──────────────────────────────────────────────────────────────────────────

def test_env_lookup_returns_stripped_value(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "  env-key-123  ")
    assert resolve_sync_env_only("groq") == "env-key-123"


def test_env_lookup_returns_none_when_missing(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert resolve_sync_env_only("groq") is None


def test_env_lookup_honours_explicit_env_var_override(monkeypatch):
    monkeypatch.setenv("CUSTOM_KEY", "custom-xyz")
    assert resolve_sync_env_only("groq", env_var="CUSTOM_KEY") == "custom-xyz"


def test_env_lookup_unknown_provider_returns_none():
    assert resolve_sync_env_only("totally-made-up-provider") is None


def test_env_var_for_provider_covers_known_providers():
    assert env_var_for_provider("groq") == "GROQ_API_KEY"
    assert env_var_for_provider("GEMINI") == "GEMINI_API_KEY"
    assert env_var_for_provider(" deepgram ") == "DEEPGRAM_API_KEY"
    assert env_var_for_provider("cartesia") == "CARTESIA_API_KEY"
    assert env_var_for_provider("elevenlabs") == "ELEVENLABS_API_KEY"
    assert env_var_for_provider("openai") == "OPENAI_API_KEY"
    assert env_var_for_provider("anthropic") == "ANTHROPIC_API_KEY"


# ──────────────────────────────────────────────────────────────────────────
# Tenant resolution takes precedence
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tenant_row_wins_over_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "env-fallback")
    pool = _FakePool({"id": "c1", "encrypted_key": "ENC:tenant-real"})
    resolver = CredentialResolver(db_pool=pool, encryption_service=_FakeEncryption("tenant-real"))
    key = await resolver.resolve("groq", tenant_id="t1")
    assert key == "tenant-real"


@pytest.mark.asyncio
async def test_env_fallback_when_no_tenant_row(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "env-fallback")
    pool = _FakePool(None)  # no row
    resolver = CredentialResolver(db_pool=pool, encryption_service=_FakeEncryption())
    key = await resolver.resolve("groq", tenant_id="t1")
    assert key == "env-fallback"


@pytest.mark.asyncio
async def test_env_fallback_when_no_tenant_id(monkeypatch):
    """Code paths without a tenant context (smoke tests, intent
    classifier, etc.) skip the DB entirely and use env directly."""
    monkeypatch.setenv("GROQ_API_KEY", "env-fallback")
    pool = _FakePool(None)
    resolver = CredentialResolver(db_pool=pool, encryption_service=_FakeEncryption())
    key = await resolver.resolve("groq", tenant_id=None)
    assert key == "env-fallback"


@pytest.mark.asyncio
async def test_returns_none_when_nothing_configured(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    resolver = CredentialResolver(db_pool=None, encryption_service=_FakeEncryption())
    assert await resolver.resolve("groq", tenant_id=None) is None


# ──────────────────────────────────────────────────────────────────────────
# Fail-safe behaviours — a bad per-tenant row must NEVER block the call
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_failure_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "env-fallback")
    pool = _FakePool(raise_on_fetch=RuntimeError("db down"))
    resolver = CredentialResolver(db_pool=pool, encryption_service=_FakeEncryption())
    key = await resolver.resolve("groq", tenant_id="t1")
    assert key == "env-fallback"


@pytest.mark.asyncio
async def test_decrypt_failure_falls_back_to_env(monkeypatch):
    """Bad ciphertext shouldn't make the service unavailable — env is
    still honoured so the tenant keeps dialling while their ops team
    fixes the row."""
    monkeypatch.setenv("GROQ_API_KEY", "env-fallback")
    pool = _FakePool({"id": "c1", "encrypted_key": "corrupted"})
    resolver = CredentialResolver(db_pool=pool, encryption_service=_FakeEncryption(plaintext=None))
    key = await resolver.resolve("groq", tenant_id="t1")
    assert key == "env-fallback"


@pytest.mark.asyncio
async def test_resolver_lowercases_provider(monkeypatch):
    """Tenant SQL uses lower(provider); the resolver must normalise
    before the query."""
    monkeypatch.setenv("GROQ_API_KEY", "env-fallback")
    call_log: dict[str, str] = {}

    class _LoggingConn(_FakeConn):
        async def fetchrow(self, *args, **kwargs):
            # asyncpg's positional args: (query, tenant_id, provider, kind)
            call_log["provider"] = args[2]
            return None

    class _LoggingPool(_FakePool):
        def acquire(self):
            return _LoggingConn()

    resolver = CredentialResolver(db_pool=_LoggingPool(), encryption_service=_FakeEncryption())
    await resolver.resolve("GROQ", tenant_id="t1")
    assert call_log["provider"] == "groq"


@pytest.mark.asyncio
async def test_resolver_trims_plaintext(monkeypatch):
    """Any whitespace that sneaks in through the encryption round-trip
    must be stripped so it doesn't break Bearer-style auth headers."""
    pool = _FakePool({"id": "c1", "encrypted_key": "anything"})
    resolver = CredentialResolver(
        db_pool=pool,
        encryption_service=_FakeEncryption("  tenant-key  \n"),
    )
    key = await resolver.resolve("groq", tenant_id="t1")
    assert key == "tenant-key"
