"""Effective billing mode shared by catalog, checkout and production admission.

An absent or broken provider configuration is not permission to simulate a
purchase. Mock mode must be explicit; disabled mode never depends on the SDK.
This classifies local configuration, not account permissions or connectivity.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
from typing import Literal

BillingMode = Literal["live", "test", "mock", "disabled", "unconfigured"]
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def get_billing_mode(*, sdk_available: bool | None = None) -> BillingMode:
    if os.getenv("STRIPE_BILLING_DISABLED", "").strip().lower() in _TRUE_VALUES:
        return "disabled"
    if os.getenv("STRIPE_MOCK_MODE", "").strip().lower() in _TRUE_VALUES:
        return "mock"

    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if key.startswith("sk_live_") and len(key) > len("sk_live_"):
        mode: BillingMode = "live"
    elif key.startswith("sk_test_") and len(key) > len("sk_test_"):
        mode = "test"
    else:
        return "unconfigured"

    if sdk_available is None:
        try:
            sdk_available = importlib.util.find_spec("stripe") is not None
            if sdk_available:
                importlib.import_module("stripe")
        except (ImportError, OSError, ValueError):
            sdk_available = False
    return mode if sdk_available else "unconfigured"


def checkout_permitted_in_test_mode(*, is_platform_admin: bool, email: str | None) -> bool:
    """Production on Stripe TEST mode sells only to the team.

    A test card pays nothing, so in production test mode a public checkout
    would hand real phone minutes to anyone. Until live keys exist, checkout
    and top-ups are open only to platform admins and to the addresses in
    STRIPE_TEST_CHECKOUT_EMAILS (comma-separated; ``name+tag@domain`` matches
    ``name@domain``). Every other mode and environment is unaffected.
    """
    if get_billing_mode() != "test":
        return True
    if os.getenv("ENVIRONMENT", "").strip().lower() != "production":
        return True
    if is_platform_admin:
        return True
    allowed = {item.strip().lower() for item in os.getenv("STRIPE_TEST_CHECKOUT_EMAILS", "").split(",") if item.strip()}
    address = (email or "").strip().lower()
    local, at, domain = address.partition("@")
    if not at or not local or not domain:
        return False
    return address in allowed or f"{local.split('+', 1)[0]}@{domain}" in allowed
