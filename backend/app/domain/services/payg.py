"""Pay as you go: prepaid minutes without a subscription (owner decision 31 Oct 2026).

Subscribers in good standing buy ``subscriber`` top-up bundles. Every other
account that may place calls buys ``payg`` bundles; the first paid bundle moves
an account with no plan onto the prepaid ``payg`` plan. An account the call
guard would refuse (suspended, archived, cancelled, unpaid) cannot buy minutes,
so a purchase can never reopen it.
"""
from __future__ import annotations

from typing import Any, Optional

from app.domain.services.subscription_status import (
    ACTIVE,
    SUBSCRIPTION_BLOCKED_STATUSES,
    TRIALING,
    canonical,
)

PAYG_PLAN_ID = "payg"
SUBSCRIBER = "subscriber"
PAYG = "payg"
AUDIENCES = (SUBSCRIBER, PAYG)


class PurchaseRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def purchase_audience(stripe_subscription_id: Optional[str], subscription_status: Any) -> str:
    """Which minute bundles this account may buy, or why it may buy none."""
    status = canonical(subscription_status)
    if stripe_subscription_id:
        if status in {ACTIVE, TRIALING}:
            return SUBSCRIBER
        raise PurchaseRefused(
            "subscription_not_in_good_standing",
            "Settle your subscription in Manage subscription before buying minutes.",
        )
    if status in SUBSCRIPTION_BLOCKED_STATUSES:
        raise PurchaseRefused(
            "account_cannot_buy_minutes",
            "This account cannot buy minutes. Contact support.",
        )
    return PAYG


async def tenant_purchase_audience(conn, tenant_id: str) -> str:
    row = await conn.fetchrow(
        "SELECT stripe_subscription_id, subscription_status FROM tenants WHERE id = $1::uuid",
        str(tenant_id),
    )
    if row is None:
        raise PurchaseRefused("account_cannot_buy_minutes", "This account cannot buy minutes. Contact support.")
    return purchase_audience(row["stripe_subscription_id"], row["subscription_status"])
