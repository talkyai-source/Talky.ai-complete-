"""PayPal settings: which environment, which credentials, who may buy now.

Sibling of ``billing_mode``. Everything is read with ``os.getenv`` at call
time so a settings change needs no import-time state. Nothing here logs or
returns a credential except ``paypal_credentials`` (for the client only).
"""
from __future__ import annotations

import os
from typing import Literal

from app.domain.services.billing_mode import is_billing_team

PAYPAL_LIVE_API = "https://api-m.paypal.com"
PAYPAL_SANDBOX_API = "https://api-m.sandbox.paypal.com"
PAYPAL_UNAVAILABLE = "PayPal is not available yet."

# The events the product subscribes to. One list, used by the setup tool and the webhook handler.
PAYPAL_WEBHOOK_EVENTS: tuple[str, ...] = (
    "CHECKOUT.ORDER.APPROVED",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.DECLINED",
    "PAYMENT.CAPTURE.DENIED",
    "PAYMENT.CAPTURE.PENDING",
    "PAYMENT.CAPTURE.REFUNDED",
    "PAYMENT.CAPTURE.REVERSED",
    "CUSTOMER.DISPUTE.CREATED",
    "CUSTOMER.DISPUTE.UPDATED",
    "CUSTOMER.DISPUTE.RESOLVED",
)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})



def paypal_env() -> Literal["live", "sandbox"] | None:
    value = os.getenv("PAYPAL_ENV", "").strip().lower()
    if value == "live":
        return "live"
    if value == "sandbox":
        return "sandbox"
    return None


def paypal_api_base() -> str | None:
    env = paypal_env()
    if env == "live":
        return PAYPAL_LIVE_API
    if env == "sandbox":
        return PAYPAL_SANDBOX_API
    return None


def paypal_provider_mode() -> Literal["live", "test"] | None:
    env = paypal_env()
    if env == "live":
        return "live"
    if env == "sandbox":
        return "test"
    return None


def paypal_credentials() -> tuple[str, str] | None:
    env = paypal_env()
    if env is None:
        return None
    if env == "sandbox":
        client_id = os.getenv("PAYPAL_SANDBOX_CLIENT_ID", "").strip()
        secret = os.getenv("PAYPAL_SANDBOX_CLIENT_SECRET", "").strip()
        if client_id and secret:
            return client_id, secret
    client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if client_id and secret:
        return client_id, secret
    return None


def paypal_webhook_id() -> str | None:
    return os.getenv("PAYPAL_WEBHOOK_ID", "").strip() or None


def paypal_configured() -> bool:
    return (
        paypal_env() is not None
        and paypal_credentials() is not None
        and paypal_webhook_id() is not None
    )


def paypal_topups_switch() -> Literal["off", "team", "on"]:
    value = os.getenv("PAYPAL_TOPUPS_ENABLED", "").strip().lower()
    if value == "team":
        return "team"
    if value in _TRUE_VALUES:
        return "on"
    return "off"


def paypal_topup_availability(
    *, is_platform_admin: bool, email: str | None
) -> tuple[bool, str | None]:
    """Whether this caller may start a PayPal top-up now, and why not."""
    if not paypal_configured():
        return False, PAYPAL_UNAVAILABLE
    switch = paypal_topups_switch()
    if switch == "off":
        return False, PAYPAL_UNAVAILABLE
    sandbox_in_production = (
        paypal_env() == "sandbox"
        and os.getenv("ENVIRONMENT", "").strip().lower() == "production"
    )
    if switch == "team" or sandbox_in_production:
        if is_billing_team(is_platform_admin=is_platform_admin, email=email):
            return True, None
        return False, PAYPAL_UNAVAILABLE
    return True, None
