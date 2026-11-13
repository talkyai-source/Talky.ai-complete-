"""QA F2-15: ENVIRONMENT is read one way, and a value nobody knows stops boot.

ENVIRONMENT was parsed three ways: "prod" or "productoin" skipped the
production gate entirely (the app booted without its secrets, docs open, no
HSTS, non-Secure cookies), and " production" with padding kept the gate but
opened /docs and dropped HSTS. The gate also accepted a 1-character
JWT_SECRET and never looked at the MFA key.
"""
from __future__ import annotations

import logging

import pytest

from app.core import prod_gate


@pytest.mark.parametrize("raw", ["prod", "productoin", "live", "Prod uction"])
def test_an_unknown_environment_refuses_to_boot(monkeypatch, raw):
    monkeypatch.setenv("ENVIRONMENT", raw)
    with pytest.raises(prod_gate.ProductionGateError, match="unknown_environment"):
        prod_gate.enforce_production_gate()


@pytest.mark.parametrize("raw,expected", [
    (" production ", "production"), ("PRODUCTION", "production"), ("Staging", "staging"),
    ("test", "test"), ("development", "development"),
])
def test_every_reading_is_stripped_and_lower_cased(monkeypatch, raw, expected):
    monkeypatch.setenv("ENVIRONMENT", raw)
    assert prod_gate.normalized_environment() == expected


def test_unset_means_development(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    assert prod_gate.normalized_environment() == "development"


def test_settings_carry_the_same_reading():
    from app.core.config import Settings

    assert Settings(environment=" Production ").environment == "production"


def test_weak_secrets_are_reported_at_boot(monkeypatch, caplog):
    monkeypatch.setenv("JWT_SECRET", "x")
    monkeypatch.setenv("SECRETS_MASTER_KEY", "not-a-fernet-key")
    monkeypatch.delenv("TOTP_ENCRYPTION_KEY", raising=False)
    with caplog.at_level(logging.WARNING, logger=prod_gate.logger.name):
        warnings = prod_gate.secret_strength_warnings()
    assert any("JWT_SECRET" in w for w in warnings)
    assert any("SECRETS_MASTER_KEY" in w for w in warnings)
    assert any("TOTP_ENCRYPTION_KEY" in w for w in warnings)


def test_strong_secrets_raise_no_warning(monkeypatch):
    import secrets

    from cryptography.fernet import Fernet

    monkeypatch.setenv("JWT_SECRET", "s" * 48)
    # kms.py: a 32-byte AES key, 64 hex characters (or base64).
    monkeypatch.setenv("SECRETS_MASTER_KEY", secrets.token_hex(32))
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert prod_gate.secret_strength_warnings() == []
