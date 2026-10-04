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
