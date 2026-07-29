"""SECRETS_MASTER_KEY must fail CLOSED in production.

SECRETS_MASTER_KEY is the key-encryption key wrapping every DEK in the
secrets store. When it is unset, `LocalKMSBackend` (the DEFAULT provider —
see kms.get_kms_backend) mints a random ephemeral key and logs a warning.
That is harmless in development and catastrophic in production: the
ciphertext in the database outlives the key, so a routine restart orphans
every tenant credential and connector token while looking like a clean
deploy.

Two enforcement points, both covered here:
  1. `prod_gate._check_required_secrets()` — refuses boot for the API
     process, alongside JWT_SECRET / TELEPHONY_METRICS_TOKEN.
  2. `LocalKMSBackend.__init__` — refuses construction in production, for
     worker processes that never run the boot gate.

Development keeps the ephemeral-key path; only the warning got clearer.
"""
from __future__ import annotations

import logging

import pytest

from app.core import kms, prod_gate


_VALID_KEY_HEX = "a" * 64  # 32 bytes of hex — a well-formed master key


def _rules(violations):
    return {v.rule for v in violations}


def _master_key_violations(violations):
    return [v for v in violations if "SECRETS_MASTER_KEY" in v.detail]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate from the ambient environment.

    Every gate rule OTHER than the one under test is satisfied, so a
    ProductionGateError in these tests can only come from
    SECRETS_MASTER_KEY.
    """
    monkeypatch.delenv("SECRETS_MASTER_KEY", raising=False)
    monkeypatch.setenv("JWT_SECRET", "a-sufficiently-random-secret-value")
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "a-sufficiently-random-token")
    monkeypatch.setenv("STRIPE_BILLING_DISABLED", "1")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "a-strong-ari-password")
    monkeypatch.setenv("FREESWITCH_ESL_PASSWORD", "a-strong-esl-password")
    monkeypatch.delenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", raising=False)
    monkeypatch.delenv("TELEPHONY_LOCAL_DEV", raising=False)
    monkeypatch.delenv("CALLER_ID_ENFORCEMENT_MODE", raising=False)
    # kms caches its backend in a module global; keep tests independent.
    monkeypatch.setattr(kms, "_backend", None)


# ---------------------------------------------------------------------------
# Non-vacuity: the premise the whole hazard rests on
# ---------------------------------------------------------------------------

def test_local_is_the_default_kms_provider(monkeypatch):
    """If `local` stopped being the default, this gate would be moot."""
    monkeypatch.delenv("KMS_PROVIDER", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("SECRETS_MASTER_KEY", _VALID_KEY_HEX)

    backend = kms.get_kms_backend()

    assert isinstance(backend, kms.LocalKMSBackend)
    assert backend.provider_name == "local_aes_gcm"


def test_gate_is_prod_only_so_the_fixture_env_is_otherwise_clean(monkeypatch):
    """Guards the fixture: with the key set, prod boot is ALLOWED.

    Without this, a test asserting "boot refused" could pass for the wrong
    reason (some other unsatisfied rule) forever.
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRETS_MASTER_KEY", _VALID_KEY_HEX)

    prod_gate.enforce_production_gate()  # must not raise


# ---------------------------------------------------------------------------
# 1. Production + missing var -> boot refused
# ---------------------------------------------------------------------------

def test_production_without_master_key_refuses_boot(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(prod_gate.ProductionGateError) as exc:
        prod_gate.enforce_production_gate()

    assert "SECRETS_MASTER_KEY" in str(exc.value)


def test_missing_master_key_is_a_missing_secret_violation(monkeypatch):
    violations = prod_gate._check_required_secrets()

    flagged = _master_key_violations(violations)
    assert len(flagged) == 1
    assert flagged[0].rule == "missing_secret"
    # The operator must be told the CONSEQUENCE, not just the fact.
    detail = flagged[0].detail.lower()
    assert "unreadable" in detail


def test_blank_master_key_is_treated_as_missing(monkeypatch):
    """A key set to whitespace is the same hazard as no key at all."""
    monkeypatch.setenv("SECRETS_MASTER_KEY", "   ")

    assert _master_key_violations(prod_gate._check_required_secrets())


# ---------------------------------------------------------------------------
# 2. Var present -> allowed
# ---------------------------------------------------------------------------

def test_master_key_present_raises_no_violation(monkeypatch):
    monkeypatch.setenv("SECRETS_MASTER_KEY", _VALID_KEY_HEX)

    violations = prod_gate._check_required_secrets()

    assert not _master_key_violations(violations)
    assert "missing_secret" not in _rules(violations)


def test_production_with_master_key_builds_the_local_backend(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRETS_MASTER_KEY", _VALID_KEY_HEX)

    backend = kms.LocalKMSBackend()

    assert backend.provider_name == "local_aes_gcm"


# ---------------------------------------------------------------------------
# 3. Development + missing var -> allowed, with a warning
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("environment", ["development", "staging", "test"])
def test_non_production_without_master_key_boots_fine(monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)

    prod_gate.enforce_production_gate()  # gate is prod-only; must not raise

    kms.LocalKMSBackend()  # ephemeral key path must stay usable


def test_dev_ephemeral_key_warning_states_the_consequence(monkeypatch, caplog):
    monkeypatch.setenv("ENVIRONMENT", "development")

    with caplog.at_level(logging.WARNING, logger=kms.__name__):
        kms.LocalKMSBackend()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "dev path must still warn about the ephemeral key"
    message = " ".join(r.getMessage() for r in warnings).lower()
    assert "ephemeral" in message
    # The point of the rewording: say what BREAKS, not just what is unset.
    assert "unreadable" in message


@pytest.mark.asyncio
async def test_dev_ephemeral_key_still_wraps_and_unwraps(monkeypatch):
    """Developer ergonomics: no key, no config, still a working backend."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    backend = kms.LocalKMSBackend()

    dek = b"0123456789abcdef0123456789abcdef"
    assert await backend.unwrap_key(await backend.wrap_key(dek)) == dek


# ---------------------------------------------------------------------------
# 4. Second enforcement point: workers that never run the boot gate
# ---------------------------------------------------------------------------

def test_local_backend_refuses_to_construct_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(RuntimeError) as exc:
        kms.LocalKMSBackend()

    assert "SECRETS_MASTER_KEY" in str(exc.value)


def test_explicit_master_key_argument_still_bypasses_env_lookup(monkeypatch):
    """Callers passing key material directly are unaffected by the gate."""
    monkeypatch.setenv("ENVIRONMENT", "production")

    backend = kms.LocalKMSBackend(master_key=b"\x01" * 32)

    assert backend.provider_name == "local_aes_gcm"
