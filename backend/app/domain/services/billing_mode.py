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


# Plans only the team can see or buy (owner decision 2026-10-27): a real $1
# charge that checks the live money flow end to end before customers pay.
# Never listed publicly; the checkout endpoint refuses it to everyone else.
TEAM_ONLY_PLAN_IDS = frozenset({"payment_test"})


def is_billing_team(*, is_platform_admin: bool, email: str | None) -> bool:
    """Platform admins and the addresses in STRIPE_TEST_CHECKOUT_EMAILS.

    The list is comma-separated; ``name+tag@domain`` matches ``name@domain``.
    """
    if is_platform_admin:
        return True
    allowed = {item.strip().lower() for item in os.getenv("STRIPE_TEST_CHECKOUT_EMAILS", "").split(",") if item.strip()}
    address = (email or "").strip().lower()
    local, at, domain = address.partition("@")
    if not at or not local or not domain:
        return False
    return address in allowed or f"{local.split('+', 1)[0]}@{domain}" in allowed


def checkout_permitted_in_test_mode(*, is_platform_admin: bool, email: str | None) -> bool:
    """Production on Stripe TEST mode sells only to the team.

    A test card pays nothing, so in production test mode a public checkout
    would hand real phone minutes to anyone. Until live keys exist, checkout
    and top-ups are open only to the team (``is_billing_team``). Every other
    mode and environment is unaffected.
    """
    if get_billing_mode() != "test":
        return True
    if os.getenv("ENVIRONMENT", "").strip().lower() != "production":
        return True
    return is_billing_team(is_platform_admin=is_platform_admin, email=email)


TEST_MODE_TEAM_ONLY_MESSAGE = "Payments are not open yet. Billing is in test mode for the Talk-Lee team."
TOPUPS_OFF_MESSAGE = "Minute top-ups are not available yet."
BILLING_UNAVAILABLE_MESSAGE = "Payments are temporarily unavailable."


def checkout_availability(*, is_platform_admin: bool, email: str | None) -> tuple[bool, str | None]:
    """Whether this caller may start a plan checkout now, and why not (G7).

    The same refusals the checkout endpoints apply, known before the click.
    """
    if get_billing_mode() not in {"live", "test", "mock"}:
        return False, BILLING_UNAVAILABLE_MESSAGE
    if not checkout_permitted_in_test_mode(is_platform_admin=is_platform_admin, email=email):
        return False, TEST_MODE_TEAM_ONLY_MESSAGE
    return True, None


def topup_availability(
    *, is_platform_admin: bool, email: str | None, live_topups_enabled: bool
) -> tuple[bool, str | None]:
    """Whether this caller may buy minutes now, in the top-up checkout's order (G7)."""
    if get_billing_mode() != "mock" and not live_topups_enabled:
        return False, TOPUPS_OFF_MESSAGE
    return checkout_availability(is_platform_admin=is_platform_admin, email=email)
