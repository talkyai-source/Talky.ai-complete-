"""bughunt billing-money-credentials-1 / -2 / -11 - resolver cache contract.

* A transient lookup/decrypt failure must never pin a tenant to the platform
  key (billing-1).
* 'No BYOK row' is cached only briefly, and plaintext only for a bounded TTL,
  so a key added/rotated/revoked in another process takes effect (billing-2).
* The normal 'tenant never configured a key' case must not log the
  'row_not_readable' WARNING (billing-11).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pytest

import app.domain.services.credential_resolver as cr
from app.domain.services.credential_resolver import CredentialResolver

TENANT = "11111111-1111-4111-8111-111111111111"


class _Txn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Conn:
    def __init__(self, pool: "_Pool"):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def transaction(self):
        return _Txn()

    async def fetchrow(self, *a, **k):
        self._pool.fetches += 1
        step = self._pool.script.pop(0) if len(self._pool.script) > 1 else self._pool.script[0]
        if isinstance(step, Exception):
            raise step
        return step

    async def execute(self, *a, **k):
        return "OK"


class _Pool:
    """Each fetchrow consumes the next scripted step (last one repeats)."""

    def __init__(self, *script: Any):
        self.script = list(script)
        self.fetches = 0

    def acquire(self):
        return _Conn(self)


class _Enc:
    def __init__(self, plaintext: Optional[str] = "tenant-key"):
        self.plaintext = plaintext

    def decrypt(self, _c):
        if self.plaintext is None:
            raise RuntimeError("bad ciphertext")
        return self.plaintext


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(cr.time, "monotonic", c)
    return c


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "platform-key")
    CredentialResolver.invalidate_cache()
    yield
    CredentialResolver.invalidate_cache()


ROW = {"id": "c1", "encrypted_key": "ENC"}


@pytest.mark.asyncio
async def test_transient_db_failure_is_not_cached_as_platform_key():
    pool = _Pool(RuntimeError("db blip"), ROW)
    r = CredentialResolver(db_pool=pool, encryption_service=_Enc("tenant-key"))
    # First call: DB blip -> platform fallback for THIS call only.
    assert await r.resolve("groq", tenant_id=TENANT) == "platform-key"
    # Second call must retry the DB and find the tenant's BYOK key.
    assert await r.resolve("groq", tenant_id=TENANT) == "tenant-key"


@pytest.mark.asyncio
async def test_transient_decrypt_failure_is_not_cached_as_platform_key():
    pool = _Pool(ROW)
    enc = _Enc(plaintext=None)
    r = CredentialResolver(db_pool=pool, encryption_service=enc)
    assert await r.resolve("groq", tenant_id=TENANT) == "platform-key"
    enc.plaintext = "tenant-key"  # KMS / key material recovered
    assert await r.resolve("groq", tenant_id=TENANT) == "tenant-key"


@pytest.mark.asyncio
async def test_absent_is_cached_without_db_roundtrip_then_expires(clock):
    pool = _Pool(None, ROW)
    r = CredentialResolver(db_pool=pool, encryption_service=_Enc("tenant-key"))
    assert await r.resolve("groq", tenant_id=TENANT) == "platform-key"
    assert await r.resolve("groq", tenant_id=TENANT) == "platform-key"
    assert pool.fetches == 1  # second call was an in-memory hit
    clock.now += 61  # past the short 'absent' TTL
    assert await r.resolve("groq", tenant_id=TENANT) == "tenant-key"


@pytest.mark.asyncio
async def test_plaintext_cache_expires_so_a_revoked_key_stops_being_used(clock):
    pool = _Pool(ROW, None)
    r = CredentialResolver(db_pool=pool, encryption_service=_Enc("tenant-key"))
    assert await r.resolve("groq", tenant_id=TENANT) == "tenant-key"
    assert await r.resolve("groq", tenant_id=TENANT) == "tenant-key"
    assert pool.fetches == 1
    clock.now += 301  # beyond any sane plaintext TTL
    # Key was revoked meanwhile -> platform fallback, not the stale plaintext.
    assert await r.resolve("groq", tenant_id=TENANT) == "platform-key"


@pytest.mark.asyncio
async def test_invalidate_cache_still_works_with_tenant_and_provider(clock):
    pool = _Pool(None, ROW)
    r = CredentialResolver(db_pool=pool, encryption_service=_Enc("tenant-key"))
    assert await r.resolve("groq", tenant_id=TENANT) == "platform-key"
    CredentialResolver.invalidate_cache(tenant_id=TENANT, provider="GROQ")
    assert await r.resolve("groq", tenant_id=TENANT) == "tenant-key"


@pytest.mark.asyncio
async def test_no_row_does_not_log_row_not_readable_warning(caplog):
    r = CredentialResolver(db_pool=_Pool(None), encryption_service=_Enc())
    with caplog.at_level(logging.DEBUG, logger=cr.__name__):
        await r.resolve("groq", tenant_id=TENANT)
    warnings = [x for x in caplog.records if x.levelno >= logging.WARNING]
    assert warnings == []
    assert any("tenant_credential_none_configured" in x.getMessage() for x in caplog.records)


@pytest.mark.asyncio
async def test_lookup_failure_still_warns(caplog):
    r = CredentialResolver(db_pool=_Pool(RuntimeError("boom")), encryption_service=_Enc())
    with caplog.at_level(logging.DEBUG, logger=cr.__name__):
        await r.resolve("groq", tenant_id=TENANT)
    assert any(
        x.levelno >= logging.WARNING and "tenant_credential_lookup_failed" in x.getMessage()
        for x in caplog.records
    )
