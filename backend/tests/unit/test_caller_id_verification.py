"""T0.1 — caller-ID ownership verification.

Covers the service layer (is_verified_for_tenant) against a fake
asyncpg pool and the enforcement-mode resolution logic used by
telephony_bridge.make_call. The HTTP path is exercised by a smoke test
in the production-gate suite.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.tenant_phone_number import (
    PhoneNumberStatus,
    TenantPhoneNumber,
    VerificationMethod,
)
from app.domain.services.tenant_phone_number_service import (
    TenantPhoneNumberService,
)


# ────────────────────────────────────────────────────────────────────────────
# Fake DB pool helpers
# ────────────────────────────────────────────────────────────────────────────

# A valid UUID — is_verified_for_tenant now routes through
# acquire_with_tenant(), which validates the tenant id is a real UUID
# (SET LOCAL app.current_tenant_id can't be parameter-bound, so it's
# interpolated and must be a safe UUID). Non-UUID ids raise ValueError
# and the service falls through to False before any status logic runs.
_TENANT_ID = "11111111-1111-1111-1111-111111111111"


class _FakeConn:
    def __init__(self, row: Any):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def transaction(self):
        # acquire_with_tenant() opens `async with conn.transaction():` so
        # the SET LOCAL tenant GUC lives for the read. The fake reuses its
        # own async-CM protocol — the yielded value is unused.
        return self

    async def fetchrow(self, *args, **kwargs):
        return self._row

    async def fetch(self, *args, **kwargs):
        return [self._row] if self._row else []

    async def execute(self, *args, **kwargs):
        return "OK"


class _FakePool:
    def __init__(self, row: Any = None):
        self._row = row

    def acquire(self):
        return _FakeConn(self._row)


# ────────────────────────────────────────────────────────────────────────────
# Service-layer tests
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_verified_number_passes():
    pool = _FakePool({"status": "verified", "stir_shaken_token": "attest_ABC"})
    svc = TenantPhoneNumberService(pool)
    assert await svc.is_verified_for_tenant(_TENANT_ID, "+14155551234") is True


@pytest.mark.asyncio
async def test_unknown_number_fails():
    pool = _FakePool(None)
    svc = TenantPhoneNumberService(pool)
    assert await svc.is_verified_for_tenant(_TENANT_ID, "+14155551234") is False


@pytest.mark.asyncio
async def test_pending_number_fails():
    pool = _FakePool({"status": "pending_verification", "stir_shaken_token": None})
    svc = TenantPhoneNumberService(pool)
    assert await svc.is_verified_for_tenant(_TENANT_ID, "+14155551234") is False


@pytest.mark.asyncio
async def test_revoked_number_fails():
    pool = _FakePool({"status": "revoked", "stir_shaken_token": "attest_ABC"})
    svc = TenantPhoneNumberService(pool)
    assert await svc.is_verified_for_tenant(_TENANT_ID, "+14155551234") is False


@pytest.mark.asyncio
async def test_verified_but_no_attestation_fails_in_prod_mode():
    pool = _FakePool({"status": "verified", "stir_shaken_token": None})
    svc = TenantPhoneNumberService(pool)
    # Dev path: no attestation required.
    assert await svc.is_verified_for_tenant(_TENANT_ID, "+14155551234") is True
    # Prod path: attestation required, should fail.
    assert await svc.is_verified_for_tenant(
        _TENANT_ID, "+14155551234", require_attestation=True
    ) is False


@pytest.mark.asyncio
async def test_db_failure_denies_cleanly():
    """Cold DB / wrong creds → service returns False, not 500."""
    broken_pool = MagicMock()
    broken_pool.acquire.side_effect = RuntimeError("db down")
    svc = TenantPhoneNumberService(broken_pool)
    assert await svc.is_verified_for_tenant(_TENANT_ID, "+14155551234") is False


# ────────────────────────────────────────────────────────────────────────────
# Enforcement-mode resolution (mirrors telephony_bridge.make_call logic)
# ────────────────────────────────────────────────────────────────────────────

def _resolve_mode() -> str:
    # Exercise the real resolver (extracted into the telephony package)
    # rather than a copy, so the two can't silently drift apart.
    from app.domain.services.telephony.caller_id_guard import (
        resolve_enforcement_mode,
    )
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    return resolve_enforcement_mode(environment)


def test_enforcement_defaults_to_enforce_in_prod(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("CALLER_ID_ENFORCEMENT_MODE", raising=False)
    assert _resolve_mode() == "enforce"


def test_enforcement_defaults_to_log_in_dev(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("CALLER_ID_ENFORCEMENT_MODE", raising=False)
    assert _resolve_mode() == "log"


def test_invalid_mode_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CALLER_ID_ENFORCEMENT_MODE", "garbage")
    assert _resolve_mode() == "enforce"


def test_mode_can_be_overridden_to_off_in_dev(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("CALLER_ID_ENFORCEMENT_MODE", "off")
    assert _resolve_mode() == "off"


# ────────────────────────────────────────────────────────────────────────────
# check_caller_id_ownership — decision logic (extracted from make_call)
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ownership_off_mode_allows_without_lookup(monkeypatch):
    """off mode short-circuits to allowed and never touches the DB."""
    from app.domain.services.telephony import caller_id_guard
    monkeypatch.setenv("CALLER_ID_ENFORCEMENT_MODE", "off")
    # A pool whose acquire() would explode — proves we never call it.
    exploding = MagicMock()
    exploding.acquire.side_effect = AssertionError("DB must not be touched in off mode")
    decision = await caller_id_guard.check_caller_id_ownership(
        exploding, tenant_id=_TENANT_ID, caller_id="+14155551234",
        environment="development",
    )
    assert decision.allowed is True
    assert decision.enforcement_mode == "off"


@pytest.mark.asyncio
async def test_ownership_enforce_denies_unverified(monkeypatch):
    from app.domain.services.telephony import caller_id_guard
    monkeypatch.setenv("CALLER_ID_ENFORCEMENT_MODE", "enforce")
    pool = _FakePool(None)  # no row → unverified
    decision = await caller_id_guard.check_caller_id_ownership(
        pool, tenant_id=_TENANT_ID, caller_id="+14155551234",
        environment="development",
    )
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_ownership_log_allows_unverified(monkeypatch):
    """log mode warns but still allows an unverified number."""
    from app.domain.services.telephony import caller_id_guard
    monkeypatch.setenv("CALLER_ID_ENFORCEMENT_MODE", "log")
    pool = _FakePool(None)
    decision = await caller_id_guard.check_caller_id_ownership(
        pool, tenant_id=_TENANT_ID, caller_id="+14155551234",
        environment="development",
    )
    assert decision.allowed is True
    assert decision.enforcement_mode == "log"


@pytest.mark.asyncio
async def test_ownership_enforce_allows_verified(monkeypatch):
    from app.domain.services.telephony import caller_id_guard
    monkeypatch.setenv("CALLER_ID_ENFORCEMENT_MODE", "enforce")
    pool = _FakePool({"status": "verified", "stir_shaken_token": "attest_ABC"})
    decision = await caller_id_guard.check_caller_id_ownership(
        pool, tenant_id=_TENANT_ID, caller_id="+14155551234",
        environment="development",
    )
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_ownership_failclosed_on_db_error(monkeypatch):
    """A DB error during lookup denies cleanly under enforce (no 500)."""
    from app.domain.services.telephony import caller_id_guard
    monkeypatch.setenv("CALLER_ID_ENFORCEMENT_MODE", "enforce")
    broken = MagicMock()
    broken.acquire.side_effect = RuntimeError("db down")
    decision = await caller_id_guard.check_caller_id_ownership(
        broken, tenant_id=_TENANT_ID, caller_id="+14155551234",
        environment="production",
    )
    assert decision.allowed is False
    assert decision.require_attestation is True


# ────────────────────────────────────────────────────────────────────────────
# Prod gate — weakened enforcement must be rejected
# ────────────────────────────────────────────────────────────────────────────

def test_gate_rejects_weakened_enforcement_in_prod(monkeypatch: pytest.MonkeyPatch):
    from app.core.prod_gate import ProductionGateError, enforce_production_gate
    # Happy baseline
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", raising=False)
    monkeypatch.delenv("TELEPHONY_LOCAL_DEV", raising=False)
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "strong-password-abc")
    monkeypatch.setenv("FREESWITCH_ESL_PASSWORD", "strong-password-abc")
    monkeypatch.setenv("JWT_SECRET", "s" * 64)
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "tok_" + "a" * 32)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_" + "a" * 32)
    # Violation:
    monkeypatch.setenv("CALLER_ID_ENFORCEMENT_MODE", "log")
    with pytest.raises(ProductionGateError, match="caller_id_enforcement_weakened"):
        enforce_production_gate()


# ────────────────────────────────────────────────────────────────────────────
# Model convenience
# ────────────────────────────────────────────────────────────────────────────

def test_model_is_dialable_in_production():
    ok = TenantPhoneNumber(
        id="1", tenant_id="t",
        e164="+14155551234",
        status=PhoneNumberStatus.VERIFIED,
        stir_shaken_token="attest_ABC",
    )
    assert ok.is_dialable_in_production() is True

    no_token = TenantPhoneNumber(
        id="1", tenant_id="t",
        e164="+14155551234",
        status=PhoneNumberStatus.VERIFIED,
        stir_shaken_token=None,
    )
    assert no_token.is_dialable_in_production() is False

    not_verified = TenantPhoneNumber(
        id="1", tenant_id="t",
        e164="+14155551234",
        status=PhoneNumberStatus.PENDING,
        stir_shaken_token="attest_ABC",
    )
    assert not_verified.is_dialable_in_production() is False
