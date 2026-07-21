"""Production-gate coverage for Stripe readiness (Worker E).

Two new rules added to `_check_required_secrets()`:
  - STRIPE_LIVE_KEY: STRIPE_SECRET_KEY is set but is not a live key
    (does not start with 'sk_live_'). The operator believes billing is
    live when it may be running against Stripe's test mode.
  - STRIPE_SDK_MISSING: STRIPE_SECRET_KEY is set but the `stripe` SDK
    isn't importable — billing_service would silently fall back to mock
    mode despite the key being configured.

Neither rule fires when no key is set at all — mock mode with no key is
an accepted, deliberate state (see the pre-existing `missing_secret` /
STRIPE_BILLING_DISABLED path, untouched by this change).
"""
from __future__ import annotations

import importlib.util

import pytest

from app.core import prod_gate


def _rules(violations):
    return {v.rule for v in violations}


@pytest.fixture(autouse=True)
def _clear_stripe_env(monkeypatch):
    # Isolate from whatever the real environment has set.
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_BILLING_DISABLED", raising=False)
    monkeypatch.setenv("JWT_SECRET", "a-sufficiently-random-secret-value")
    monkeypatch.setenv("TELEPHONY_METRICS_TOKEN", "a-sufficiently-random-token")


def test_non_live_stripe_key_is_flagged(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_abc123")

    violations = prod_gate._check_required_secrets()

    assert "STRIPE_LIVE_KEY" in _rules(violations)


def test_live_stripe_key_with_sdk_present_is_not_flagged(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_abc123")
    # Only assert this branch if the SDK is actually importable in this env;
    # otherwise STRIPE_SDK_MISSING is the correct (and separately tested)
    # outcome and would make this assertion meaningless.
    if importlib.util.find_spec("stripe") is None:
        pytest.skip("stripe SDK not installed in this environment")

    violations = prod_gate._check_required_secrets()

    assert "STRIPE_LIVE_KEY" not in _rules(violations)
    assert "STRIPE_SDK_MISSING" not in _rules(violations)


def test_stripe_key_set_but_sdk_absent_is_flagged(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_abc123")

    real_find_spec = importlib.util.find_spec

    def _fake_find_spec(name, *a, **k):
        if name == "stripe":
            return None
        return real_find_spec(name, *a, **k)

    monkeypatch.setattr(prod_gate.importlib.util, "find_spec", _fake_find_spec)

    violations = prod_gate._check_required_secrets()

    assert "STRIPE_SDK_MISSING" in _rules(violations)
    detail = next(v.detail for v in violations if v.rule == "STRIPE_SDK_MISSING")
    assert "mock mode" in detail.lower()


def test_no_stripe_key_set_raises_no_stripe_readiness_violation(monkeypatch):
    # No STRIPE_SECRET_KEY and no STRIPE_BILLING_DISABLED: the pre-existing
    # `missing_secret` violation still fires (unchanged behavior), but
    # neither of the new readiness rules should — there's no key to assess.
    violations = prod_gate._check_required_secrets()

    rules = _rules(violations)
    assert "STRIPE_LIVE_KEY" not in rules
    assert "STRIPE_SDK_MISSING" not in rules
    assert "missing_secret" in rules  # pre-existing behavior, untouched


def test_no_stripe_key_but_billing_disabled_raises_nothing_stripe_related(monkeypatch):
    monkeypatch.setenv("STRIPE_BILLING_DISABLED", "1")

    violations = prod_gate._check_required_secrets()

    stripe_rules = {r for r in _rules(violations) if "STRIPE" in r or r == "missing_secret"}
    # missing_secret for STRIPE is suppressed by STRIPE_BILLING_DISABLED,
    # and the two new rules never fire without a key.
    assert not any(
        v.rule == "missing_secret" and "STRIPE" in v.detail
        for v in violations
    )
    assert "STRIPE_LIVE_KEY" not in stripe_rules
    assert "STRIPE_SDK_MISSING" not in stripe_rules
