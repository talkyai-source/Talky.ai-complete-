"""One effective mode must govern billing display and purchase admission."""
import importlib

import pytest


@pytest.fixture(autouse=True)
def clean_billing_env(monkeypatch):
    for name in ("STRIPE_SECRET_KEY", "STRIPE_MOCK_MODE", "STRIPE_BILLING_DISABLED"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("key,sdk,expected", [
    (None, True, "unconfigured"),
    ("   ", True, "unconfigured"),
    ("not-a-stripe-key", True, "unconfigured"),
    ("sk_live_", True, "unconfigured"),
    ("rk_live_fixture", True, "unconfigured"),
    ("sk_live_fixture", False, "unconfigured"),
    ("sk_test_fixture", False, "unconfigured"),
    ("sk_live_fixture", True, "live"),
    ("  sk_test_fixture  ", True, "test"),
])
def test_effective_mode_does_not_invent_a_mock_fallback(monkeypatch, key, sdk, expected):
    from app.domain.services.billing_mode import get_billing_mode
    if key is not None:
        monkeypatch.setenv("STRIPE_SECRET_KEY", key)
    assert get_billing_mode(sdk_available=sdk) == expected


@pytest.mark.parametrize("enabled", ["true", "1", " yes ", "ON"])
def test_explicit_disabled_mode_wins_over_stale_mock_and_key(monkeypatch, enabled):
    from app.domain.services.billing_mode import get_billing_mode
    monkeypatch.setenv("STRIPE_BILLING_DISABLED", enabled)
    monkeypatch.setenv("STRIPE_MOCK_MODE", "true")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_fixture")
    assert get_billing_mode(sdk_available=False) == "disabled"


def test_explicit_mock_mode_is_never_reported_as_live(monkeypatch):
    from app.domain.services.billing_mode import get_billing_mode
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_fixture")
    monkeypatch.setenv("STRIPE_MOCK_MODE", " TRUE ")
    assert get_billing_mode(sdk_available=True) == "mock"


def test_invalid_truthy_flag_does_not_disable_billing(monkeypatch):
    from app.domain.services.billing_mode import get_billing_mode
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_fixture")
    monkeypatch.setenv("STRIPE_BILLING_DISABLED", "not-false")
    assert get_billing_mode(sdk_available=True) == "live"


def test_default_mode_checks_sdk_import_not_only_package_discovery(monkeypatch):
    from app.domain.services.billing_mode import get_billing_mode
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_fixture")
    original = importlib.import_module

    def broken_sdk(name, *args, **kwargs):
        if name == "stripe":
            raise ImportError("synthetic broken SDK dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(importlib, "import_module", broken_sdk)
    assert get_billing_mode() == "unconfigured"


def test_disabled_mode_does_not_import_sdk(monkeypatch):
    from app.domain.services.billing_mode import get_billing_mode
    monkeypatch.setenv("STRIPE_BILLING_DISABLED", "1")
    monkeypatch.setattr(importlib, "import_module", lambda *_args: pytest.fail("SDK import in disabled mode"))
    assert get_billing_mode() == "disabled"
