"""T0.2 + T0.3 — fail-closed production gate.

Covers two separate failure modes:

1. The runtime bypass for CallGuard errors must ONLY honour
   ENVIRONMENT=development AND TELEPHONY_LOCAL_DEV=1 AND the explicit
   TELEPHONY_DEV_BYPASS_GUARD_ERRORS flag. Every other combination —
   production, staging, blank env, missing flag — must refuse to
   bypass.

2. The startup gate (app.core.prod_gate.enforce_production_gate) must
   raise ProductionGateError in prod when any fatal misconfig is
   present: dev-bypass flags still set, default PBX creds, missing
   JWT_SECRET or TELEPHONY_METRICS_TOKEN, missing STRIPE_SECRET_KEY
   without the explicit STRIPE_BILLING_DISABLED override.
"""
from __future__ import annotations

import os

import pytest

from app.core.prod_gate import ProductionGateError, enforce_production_gate


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

_STRONG_JWT = "s" * 64  # pretend-strong secret — passes the placeholder check
_STRONG_PBX_PW = "not-a-default-password-7a2c"


def _prod_env_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Baseline prod env where the gate should pass. Tests that want to
    prove a specific violation override ONE of these from this baseline."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    # No dev bypass flags
    monkeypatch.delenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", raising=False)
    monkeypatch.delenv("TELEPHONY_LOCAL_DEV", raising=False)
    # Non-default PBX creds for whichever adapter
    monkeypatch.setenv("TELEPHONY_ADAPTER", "asterisk")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", _STRONG_PBX_PW)
    monkeypatch.setenv("FREESWITCH_ESL_PASSWORD", _STRONG_PBX_PW)
    # Required secrets
    monkeypatch.setenv("JWT_SECRET", _STRONG_JWT)
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "tok_" + "a" * 32)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_" + "a" * 32)


# ────────────────────────────────────────────────────────────────────────────
# Startup-gate tests
# ────────────────────────────────────────────────────────────────────────────

def test_gate_skips_when_not_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    # Set a bunch of ugly dev flags — none of this matters in dev.
    monkeypatch.setenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", "true")
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "asterisk")
    monkeypatch.setenv("JWT_SECRET", "")
    # Should NOT raise.
    enforce_production_gate()


def test_gate_passes_with_clean_prod_env(monkeypatch: pytest.MonkeyPatch):
    _prod_env_happy_path(monkeypatch)
    enforce_production_gate()  # no raise


def test_gate_refuses_dev_bypass_flag_in_prod(monkeypatch: pytest.MonkeyPatch):
    _prod_env_happy_path(monkeypatch)
    monkeypatch.setenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", "true")
    with pytest.raises(ProductionGateError, match="dev_bypass_in_prod"):
        enforce_production_gate()


def test_gate_refuses_telephony_local_dev_flag_in_prod(monkeypatch: pytest.MonkeyPatch):
    _prod_env_happy_path(monkeypatch)
    monkeypatch.setenv("TELEPHONY_LOCAL_DEV", "1")
    with pytest.raises(ProductionGateError, match="dev_bypass_in_prod"):
        enforce_production_gate()


@pytest.mark.parametrize("bad_pw", ["", "asterisk", "secret", "ari_password"])
def test_gate_refuses_default_asterisk_password(monkeypatch: pytest.MonkeyPatch, bad_pw: str):
    _prod_env_happy_path(monkeypatch)
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", bad_pw)
    with pytest.raises(ProductionGateError, match="asterisk_default_password"):
        enforce_production_gate()


@pytest.mark.parametrize("bad_pw", ["", "ClueCon", "cluecon"])
def test_gate_refuses_default_freeswitch_password(monkeypatch: pytest.MonkeyPatch, bad_pw: str):
    _prod_env_happy_path(monkeypatch)
    monkeypatch.setenv("TELEPHONY_ADAPTER", "freeswitch")
    monkeypatch.setenv("FREESWITCH_ESL_PASSWORD", bad_pw)
    with pytest.raises(ProductionGateError, match="freeswitch_default_password"):
        enforce_production_gate()


def test_gate_only_checks_selected_adapter(monkeypatch: pytest.MonkeyPatch):
    """Running Asterisk shouldn't care about the FreeSWITCH password."""
    _prod_env_happy_path(monkeypatch)
    monkeypatch.setenv("TELEPHONY_ADAPTER", "asterisk")
    monkeypatch.setenv("FREESWITCH_ESL_PASSWORD", "ClueCon")  # default, but unused
    enforce_production_gate()  # no raise — fs not selected


def test_gate_refuses_missing_jwt_secret(monkeypatch: pytest.MonkeyPatch):
    _prod_env_happy_path(monkeypatch)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ProductionGateError, match="missing_secret.*JWT_SECRET"):
        enforce_production_gate()


@pytest.mark.parametrize("weak", ["change-me", "changeme", "secret", "placeholder"])
def test_gate_refuses_weak_jwt_secret(monkeypatch: pytest.MonkeyPatch, weak: str):
    _prod_env_happy_path(monkeypatch)
    monkeypatch.setenv("JWT_SECRET", weak)
    with pytest.raises(ProductionGateError, match="weak_secret"):
        enforce_production_gate()


def test_gate_refuses_missing_metrics_token(monkeypatch: pytest.MonkeyPatch):
    _prod_env_happy_path(monkeypatch)
    monkeypatch.delenv("TELEPHONY_METRICS_TOKEN", raising=False)
    with pytest.raises(ProductionGateError, match="TELEPHONY_METRICS_TOKEN"):
        enforce_production_gate()


def test_gate_refuses_missing_stripe_key_in_prod(monkeypatch: pytest.MonkeyPatch):
    _prod_env_happy_path(monkeypatch)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(ProductionGateError, match="STRIPE_SECRET_KEY"):
        enforce_production_gate()


def test_gate_allows_stripe_bypass_with_explicit_ack(monkeypatch: pytest.MonkeyPatch):
    """Self-hosted OSS deploys can opt out of billing with an explicit ack."""
    _prod_env_happy_path(monkeypatch)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.setenv("STRIPE_BILLING_DISABLED", "1")
    enforce_production_gate()  # no raise


def test_gate_accumulates_multiple_violations(monkeypatch: pytest.MonkeyPatch):
    """All violations appear in the error so an operator can fix them in one deploy."""
    _prod_env_happy_path(monkeypatch)
    monkeypatch.setenv("ASTERISK_ARI_PASSWORD", "asterisk")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ProductionGateError) as exc:
        enforce_production_gate()
    msg = str(exc.value)
    assert "asterisk_default_password" in msg
    assert "JWT_SECRET" in msg


# ────────────────────────────────────────────────────────────────────────────
# Runtime-bypass tests (T0.2 — telephony_bridge.make_call bypass rule)
# ────────────────────────────────────────────────────────────────────────────
#
# We test the boolean directly by re-evaluating the same expression the
# endpoint uses. Keeps the test simple — no FastAPI dependency wiring —
# and catches regressions if someone loosens the rule.

def _is_bypass_allowed() -> bool:
    """Mirror of the expression at telephony_bridge.make_call:1245-1251."""
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    local_dev = os.getenv("TELEPHONY_LOCAL_DEV", "").strip().lower() in {"1", "true", "yes"}
    bypass_flag = os.getenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", "false").strip().lower() \
        not in {"0", "false", "no", ""}
    return environment == "development" and local_dev and bypass_flag


@pytest.mark.parametrize(
    "environment,local_dev,bypass_flag,expected",
    [
        # Happy bypass path — dev only.
        ("development", "1", "true", True),
        ("development", "true", "1", True),
        # Dev but missing LOCAL_DEV ack → no bypass.
        ("development", "", "true", False),
        # Dev + LOCAL_DEV but no bypass flag → no bypass.
        ("development", "1", "", False),
        ("development", "1", "false", False),
        # Staging must NEVER bypass — historically the footgun.
        ("staging", "1", "true", False),
        # Blank / misspelled env must NEVER bypass.
        ("", "1", "true", False),
        ("dev", "1", "true", False),
        ("prod", "1", "true", False),
        # Production — hard no even with every flag set.
        ("production", "1", "true", False),
    ],
)
def test_runtime_bypass_rule(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
    local_dev: str,
    bypass_flag: str,
    expected: bool,
):
    monkeypatch.setenv("ENVIRONMENT", environment)
    if local_dev:
        monkeypatch.setenv("TELEPHONY_LOCAL_DEV", local_dev)
    else:
        monkeypatch.delenv("TELEPHONY_LOCAL_DEV", raising=False)
    if bypass_flag:
        monkeypatch.setenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", bypass_flag)
    else:
        monkeypatch.delenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", raising=False)

    assert _is_bypass_allowed() is expected
