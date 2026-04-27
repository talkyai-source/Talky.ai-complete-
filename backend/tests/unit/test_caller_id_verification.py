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

class _FakeConn:
    def __init__(self, row: Any):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

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
    assert await svc.is_verified_for_tenant("tenant-a", "+14155551234") is True


@pytest.mark.asyncio
async def test_unknown_number_fails():
    pool = _FakePool(None)
    svc = TenantPhoneNumberService(pool)
    assert await svc.is_verified_for_tenant("tenant-a", "+14155551234") is False


@pytest.mark.asyncio
async def test_pending_number_fails():
    pool = _FakePool({"status": "pending_verification", "stir_shaken_token": None})
    svc = TenantPhoneNumberService(pool)
    assert await svc.is_verified_for_tenant("tenant-a", "+14155551234") is False


@pytest.mark.asyncio
async def test_revoked_number_fails():
    pool = _FakePool({"status": "revoked", "stir_shaken_token": "attest_ABC"})
    svc = TenantPhoneNumberService(pool)
    assert await svc.is_verified_for_tenant("tenant-a", "+14155551234") is False


@pytest.mark.asyncio
async def test_verified_but_no_attestation_fails_in_prod_mode():
    pool = _FakePool({"status": "verified", "stir_shaken_token": None})
    svc = TenantPhoneNumberService(pool)
    # Dev path: no attestation required.
    assert await svc.is_verified_for_tenant("tenant-a", "+14155551234") is True
    # Prod path: attestation required, should fail.
    assert await svc.is_verified_for_tenant(
        "tenant-a", "+14155551234", require_attestation=True
    ) is False


@pytest.mark.asyncio
async def test_db_failure_denies_cleanly():
    """Cold DB / wrong creds → service returns False, not 500."""
    broken_pool = MagicMock()
    broken_pool.acquire.side_effect = RuntimeError("db down")
    svc = TenantPhoneNumberService(broken_pool)
    assert await svc.is_verified_for_tenant("tenant-a", "+14155551234") is False


# ────────────────────────────────────────────────────────────────────────────
# Enforcement-mode resolution (mirrors telephony_bridge.make_call logic)
# ────────────────────────────────────────────────────────────────────────────

def _resolve_mode() -> str:
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    default_mode = "enforce" if environment == "production" else "log"
    mode = os.getenv("CALLER_ID_ENFORCEMENT_MODE", default_mode).strip().lower()
    if mode not in {"enforce", "log", "off"}:
        mode = default_mode
    return mode


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
