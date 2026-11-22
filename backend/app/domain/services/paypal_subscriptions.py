"""PayPal plans: start a PayPal subscription, learn its truth, grant the plan once.

Sits beside the Stripe plan path (``billing_checkout``) and touches none of its
tables. A purchase is one row in ``paypal_subscriptions`` whose id is the
browser's request id, so a retry is the same purchase.

WHAT IS TRUSTED
---------------
Our own row and PayPal's answer to ``get_subscription``, checked against the
row (subscription id, plan id, our id in ``custom_id``). The minutes granted
are the stored snapshot's, never a value from PayPal or the browser. A plan is
granted only when PayPal reports the subscription ``ACTIVE`` AND a payment of
exactly the price (amount and currency) is shown for it.

WHAT MAKES THE GRANT ONCE-ONLY
------------------------------
``_apply`` is the only place that turns PayPal's truth into our state. It
decides on the row it has locked (``FOR UPDATE``, after the tenant row), and the
grant branch requires ``activated_at IS NULL``. A browser return, a webhook and
the sweep may all arrive at once: the second one finds ``activated_at`` set.

ACCESS AFTER A CANCEL
---------------------
Cancelling at PayPal stops future billing at once and PayPal sends nothing when
the paid period ends. So a cancelled plan keeps access (``cancel_at_period_end``)
until ``current_period_end``; ``sweep`` ends it then.
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg

from app.core.db_utils import acquire_with_tenant
from app.domain.services import paypal_client as _pc
from app.domain.services.billing_catalog import BillingCatalogUnavailable, resolve_checkout_option
from app.domain.services.billing_checkout import (
    CheckoutAttempts,
    CheckoutError,
    apply_plan_allocation,
    billing_return_urls,
    public_option,
)
from app.domain.services.paypal_client import (
    SUPPORTED_CURRENCIES,
    PayPalError,
    PayPalNotConfigured,
    get_paypal_client,
    minor_units,
)
from app.domain.services.paypal_mode import PAYPAL_UNAVAILABLE, paypal_env, paypal_provider_mode
from app.domain.services.payg import PAYG_PLAN_ID
from app.domain.services.plan_end import end_plan
from app.domain.services.subscription_status import (
    ACTIVE,
    CANCELLED,
    INACTIVE,
    PAST_DUE,
    SUSPENDED,
    UNPAID,
    canonical,
)

logger = logging.getLogger(__name__)

_BRAND = "Talk-Lee"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

CREATING = "creating"
UNKNOWN = "unknown"
APPROVAL_PENDING = "approval_pending"
EXPIRED = "expired"
FAILED = "failed"

LIVE = (CREATING, UNKNOWN, APPROVAL_PENDING, ACTIVE, PAST_DUE, SUSPENDED)
OPEN = (CREATING, UNKNOWN, APPROVAL_PENDING)  # no plan granted yet
HELD = (ACTIVE, PAST_DUE, SUSPENDED)  # a plan was granted (activated_at set)

_PAYMENT_STATUSES = frozenset(
    {"completed", "declined", "partially_refunded", "pending", "refunded", "failed"}
)
_PAID_STATES = frozenset({"completed", "refunded", "partially_refunded"})
_INVOICE_STATUS = {
    "completed": "paid", "refunded": "refunded", "partially_refunded": "partially_refunded",
    "reversed": "reversed", "pending": "pending", "declined": "failed", "failed": "failed",
}
_APPROVAL_WINDOW = timedelta(hours=3)
# PayPal takes the first payment at once. This long after PayPal first showed the approval, with
# the subscription ACTIVE and no payment of the price in sight, we stop it at PayPal and tell a
# person (the clock is ``approved_at``, not the creation time: approving can take hours).
_PAYMENT_WAIT = timedelta(hours=2)
_NO_ID_FRESH = timedelta(minutes=2)

MSG_CONFIRMING = "PayPal has not confirmed the first payment yet. This page checks again by itself."
MSG_UNCONFIRMED = "The purchase is unconfirmed. Retry this saved request."
_UNCONFIRMED = "PayPal did not confirm the purchase. Retry the same saved request."
MSG_NOT_USED = ("This PayPal purchase was not used. If PayPal took a payment for it, "
                "it will be refunded.")


# The three status lists, once, as literal text (kept equal to LIVE / OPEN / HELD by a unit test).
_LIVE_SQL = "('creating', 'unknown', 'approval_pending', 'active', 'past_due', 'suspended')"
_OPEN_SQL = "('creating', 'unknown', 'approval_pending')"
_HELD_SQL = "('active', 'past_due', 'suspended')"

# One spelling of every statement: the unit-test fake matches on these.
_Q_TENANT_LOCK = (
    "SELECT id, status, subscription_status, stripe_subscription_id FROM tenants "
    "WHERE id = $1::uuid FOR UPDATE"
)
_Q_ROW_BY_ID = "SELECT * FROM paypal_subscriptions WHERE id = $1::uuid AND tenant_id = $2::uuid"
_Q_ROW_LOCK = (
    "SELECT * FROM paypal_subscriptions WHERE id = $1::uuid AND tenant_id = $2::uuid FOR UPDATE"
)
# Webhook path: PayPal's id is all that is known, the tenant is learned from the row.
_Q_ROW_BY_PP = "SELECT * FROM paypal_subscriptions WHERE paypal_subscription_id = $1"
# Platform table (no tenant column).
_Q_PLAN_OPTION = (
    "SELECT paypal_plan_id, provider_mode, interval, amount_minor, currency, active, verified_at "
    "FROM paypal_plan_options WHERE price_option_id = $1::uuid"
)
_Q_STRIPE_SUB_EXISTS = (
    "SELECT 1 FROM subscriptions WHERE tenant_id = $1::uuid "
    "AND status NOT IN ('canceled','cancelled','incomplete_expired') LIMIT 1"
)
_Q_STRIPE_ATTEMPT = (
    "SELECT * FROM billing_checkout_attempts WHERE tenant_id = $1::uuid "
    "AND status IN ('creating', 'ready', 'unknown') LIMIT 1"
)
_Q_STRIPE_ATTEMPT_EXISTS = (
    "SELECT 1 FROM billing_checkout_attempts WHERE tenant_id = $1::uuid "
    "AND status IN ('creating', 'ready', 'unknown') LIMIT 1"
)
_Q_OTHER_LIVE = (
    "SELECT * FROM paypal_subscriptions WHERE tenant_id = $1::uuid "
    f"AND status IN {_LIVE_SQL} ORDER BY created_at LIMIT 1"
)
_Q_OTHER_LIVE_EXISTS = (
    "SELECT 1 FROM paypal_subscriptions WHERE tenant_id = $1::uuid "
    f"AND id <> $2::uuid AND status IN {_LIVE_SQL} LIMIT 1"
)
_Q_INSERT = (
    "INSERT INTO paypal_subscriptions (id, tenant_id, price_option_id, plan_id, provider_mode, "
    "paypal_plan_id, snapshot, status) "
    "VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7::jsonb, 'creating')"
)
_Q_HELD = (
    "SELECT * FROM paypal_subscriptions WHERE tenant_id = $1::uuid "
    f"AND status IN {_HELD_SQL} ORDER BY created_at DESC LIMIT 1"
)
_Q_OPEN_ROW = (
    "SELECT * FROM paypal_subscriptions WHERE tenant_id = $1::uuid "
    f"AND status IN {_OPEN_SQL} ORDER BY created_at LIMIT 1"
)
_Q_HAS_LIVE = (
    "SELECT 1 FROM paypal_subscriptions WHERE tenant_id = $1::uuid "
    f"AND status IN {_LIVE_SQL} LIMIT 1"
)
_Q_HELD_EXISTS = (
    "SELECT 1 FROM paypal_subscriptions WHERE tenant_id = $1::uuid "
    f"AND status IN {_HELD_SQL} LIMIT 1"
)
# Writes to paypal_subscriptions. The first four are guarded by the row's status; _Q_SAVE is
# used only under the row lock, on values merged over the locked row.
_Q_SAVE_CREATED = (
    "UPDATE paypal_subscriptions SET paypal_subscription_id = $3, approval_url = $4, "
    "paypal_status = $5, status = 'approval_pending', checked_at = NOW(), updated_at = NOW() "
    "WHERE id = $1::uuid AND tenant_id = $2::uuid AND status IN ('creating', 'unknown')"
)
_Q_MARK_UNKNOWN = (
    "UPDATE paypal_subscriptions SET status = 'unknown', updated_at = NOW() "
    "WHERE id = $1::uuid AND tenant_id = $2::uuid AND status IN ('creating', 'unknown')"
)
_Q_FAIL_UNSTARTED = (
    "UPDATE paypal_subscriptions SET status = 'failed', ended_at = NOW(), updated_at = NOW() "
    "WHERE id = $1::uuid AND tenant_id = $2::uuid AND status IN ('creating', 'unknown')"
)
_Q_CLOSE_OPEN = (
    "UPDATE paypal_subscriptions SET status = $3, ended_at = NOW(), updated_at = NOW() "
    f"WHERE id = $1::uuid AND tenant_id = $2::uuid AND status IN {_OPEN_SQL}"
)
_Q_SAVE = (
    "UPDATE paypal_subscriptions SET status = $3, paypal_status = $4, failed_payments = $5, "
    "activated_at = $6, last_payment_at = $7, current_period_start = $8, current_period_end = $9, "
    "cancel_at_period_end = $10, cancelled_at = $11, ended_at = $12, checked_at = $13, "
    "approved_at = $14, updated_at = NOW() WHERE id = $1::uuid AND tenant_id = $2::uuid"
)
_SAVED = (
    "status", "paypal_status", "failed_payments", "activated_at", "last_payment_at",
    "current_period_start", "current_period_end", "cancel_at_period_end", "cancelled_at",
    "ended_at", "checked_at", "approved_at",
)
_Q_TENANT_PROJECT = (
    "UPDATE tenants SET subscription_status = $2 "
    "WHERE id = $1::uuid AND stripe_subscription_id IS NULL AND plan_id = $3 "
    "AND subscription_status IS DISTINCT FROM 'suspended'"
)
# A reversal after the plan already ended (the tenant is pay as you go): block the account again.
_Q_TENANT_REBLOCK = (
    "UPDATE tenants SET subscription_status = $2 "
    "WHERE id = $1::uuid AND stripe_subscription_id IS NULL AND plan_id = $3 "
    "AND subscription_status IS DISTINCT FROM 'suspended'"
)
_Q_TENANT_PLAN_ONLY = (
    "UPDATE tenants SET plan_id = $2 WHERE id = $1::uuid AND stripe_subscription_id IS NULL"
)
_Q_TENANT_GRANT = (
    "UPDATE tenants SET plan_id = $2, "
    "subscription_status = CASE WHEN subscription_status = 'suspended' "
    "THEN subscription_status ELSE $3 END "
    "WHERE id = $1::uuid AND stripe_subscription_id IS NULL"
)
# Webhook path: the payment is found by PayPal's transaction id alone.
_Q_PAYMENT_BY_TXN = (
    "SELECT id, tenant_id, subscription_id FROM paypal_subscription_payments "
    "WHERE paypal_transaction_id = $1"
)
_Q_PAYMENT_REVERSE = (
    "UPDATE paypal_subscription_payments SET status = 'reversed', updated_at = NOW() "
    "WHERE id = $1::uuid AND tenant_id = $2::uuid"
)
_Q_PAYMENT_UPDATE = (
    "UPDATE paypal_subscription_payments SET status = $4, updated_at = NOW() "
    "WHERE paypal_transaction_id = $3 AND tenant_id = $1::uuid AND subscription_id = $2::uuid "
    "AND status <> 'reversed'"
)
_Q_PAYMENT_INSERT = (
    "INSERT INTO paypal_subscription_payments "
    "(tenant_id, subscription_id, paypal_transaction_id, status, amount_minor, currency, paid_at) "
    "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7) "
    "ON CONFLICT (paypal_transaction_id) DO NOTHING"
)
# Platform table (no tenant column).
_Q_REVIEW = (
    "INSERT INTO paypal_webhook_events "
    "(event_id, event_type, resource_type, resource_id, order_id, provider_mode, state, detail, "
    "payload_hash) VALUES ($1, 'LOCAL.PLAN.REVIEW', 'subscription', $2, NULL, $3, "
    "'needs_review', $4, $5) "
    "ON CONFLICT (event_id) DO UPDATE SET attempt_count = paypal_webhook_events.attempt_count + 1, "
    "state = CASE WHEN paypal_webhook_events.state = 'dismissed' AND paypal_webhook_events.detail "
    "IN ('binding', 'not_confirmed_with_paypal') THEN 'dismissed' ELSE 'needs_review' END"
)
# Sweep: service-wide reads by design (platform scope), every write after them is tenant-bound.
_Q_SWEEP_DUE = (
    "SELECT id, tenant_id FROM paypal_subscriptions WHERE status = 'active' "
    "AND cancel_at_period_end AND current_period_end <= NOW() ORDER BY current_period_end LIMIT $1"
)
_Q_SWEEP_STALE = (
    f"SELECT * FROM paypal_subscriptions WHERE status IN {_LIVE_SQL} "
    "AND (checked_at IS NULL OR checked_at < NOW() - INTERVAL '6 hours' "
    f"OR (status IN {_OPEN_SQL} AND checked_at < NOW() - INTERVAL '10 minutes')) "
    "ORDER BY checked_at NULLS FIRST LIMIT $1"
)
# Rows we gave up on that PayPal may still run (customer approved late, notice lost).
_Q_SWEEP_ABANDONED = (
    "SELECT * FROM paypal_subscriptions WHERE status IN ('failed', 'expired') "
    "AND activated_at IS NULL AND paypal_subscription_id IS NOT NULL "
    "AND created_at > NOW() - INTERVAL '3 days' "
    "AND (checked_at IS NULL OR checked_at < NOW() - INTERVAL '1 hour') "
    "ORDER BY checked_at NULLS FIRST LIMIT $1"
)
# Rows we ended that PayPal still shows running: cancel there again.
_Q_SWEEP_STRAY = (
    "SELECT * FROM paypal_subscriptions WHERE status = 'cancelled' "
    "AND paypal_subscription_id IS NOT NULL AND paypal_status IN ('APPROVED', 'ACTIVE', 'SUSPENDED') "
    "AND (checked_at IS NULL OR checked_at < NOW() - INTERVAL '1 hour') "
    "ORDER BY checked_at NULLS FIRST LIMIT $1"
)
# Paying customers we have not been able to confirm with PayPal for days: a person is told.
_Q_SWEEP_UNCONFIRMED = (
    "SELECT * FROM paypal_subscriptions WHERE status IN ('active', 'past_due') "
    "AND NOT cancel_at_period_end AND current_period_end < NOW() - INTERVAL '3 days' "
    "AND (checked_at IS NULL OR checked_at < NOW() - INTERVAL '3 days') LIMIT $1"
)
# PayPal's payment list lagged at activation: let the next sweep read the row again.
_Q_RECHECK_SOON = (
    "UPDATE paypal_subscriptions SET checked_at = NULL, updated_at = NOW() "
    "WHERE id = $1::uuid AND tenant_id = $2::uuid AND activated_at > NOW() - INTERVAL '2 days'"
)
_Q_PAYMENTS = (
    "SELECT p.id, p.tenant_id, p.paypal_transaction_id, p.status, p.amount_minor, p.currency, "
    "p.paid_at, p.created_at, s.provider_mode, s.snapshot "
    "FROM paypal_subscription_payments p "
    "JOIN paypal_subscriptions s ON s.id = p.subscription_id AND s.tenant_id = p.tenant_id "
    "WHERE p.tenant_id = $1::uuid AND ($3::uuid IS NULL OR p.id = $3::uuid) "
    "ORDER BY p.paid_at DESC LIMIT $2"
)


class _BadAnswer(Exception):
    """PayPal's answer to create_subscription cannot be trusted."""


@dataclass(frozen=True)
class SyncOutcome:
    subscription_id: str  # OUR row id (uuid as str)
    tenant_id: str
    status: str  # our row status after the sync
    activated: bool  # row.activated_at is set after the sync
    review: bool = False  # a person must look (a review row was written)


# -- small helpers ---------------------------------------------------------------


def _short(value) -> str:
    return str(value)[:8]


def _rows(tag) -> int:
    try:
        return int(str(tag).strip().rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return -1


def _snap(row) -> dict:
    value = row["snapshot"]
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _count(value) -> int:
    return value if type(value) is int and value >= 0 else 0


def _add_period(start: datetime, interval: str) -> datetime:
    months = 12 if interval == "year" else 1
    carry, month0 = divmod(start.month - 1 + months, 12)
    year, month = start.year + carry, month0 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def _amount_matches(amount, option) -> bool:
    """True when a PayPal money object equals the price, in the price's currency."""
    if not isinstance(amount, dict):
        return False
    currency = str(option["currency"]).upper()
    if str(amount.get("currency_code") or "").upper() != currency:
        return False
    try:
        return minor_units(amount.get("value"), currency) == int(option["amount_minor"])
    except ValueError:
        return False


def _last_payment(last, option) -> tuple[bool, datetime | None]:
    if not isinstance(last, dict):
        return False, None
    return _amount_matches(last.get("amount"), option), _parse_time(last.get("time"))


def _gross(txn):
    breakdown = txn.get("amount_with_breakdown")
    return breakdown.get("gross_amount") if isinstance(breakdown, dict) else None


async def _save(conn, locked: dict, changes: dict) -> None:
    """Write ``changes`` merged over the LOCKED row's values. Only under the row lock."""
    values = [changes[c] if c in changes else locked[c] for c in _SAVED]
    tag = await conn.execute(_Q_SAVE, str(locked["id"]), str(locked["tenant_id"]), *values)
    if _rows(tag) != 1:
        raise CheckoutError("checkout_not_found", "Checkout request was not found.", 404)


async def has_live_subscription(conn, tenant_id) -> bool:
    return await conn.fetchval(_Q_HAS_LIVE, str(tenant_id)) is not None


async def held_subscription_exists(conn, tenant_id) -> bool:
    """A paying PayPal subscriber; an unfinished purchase does not count."""
    return await conn.fetchval(_Q_HELD_EXISTS, str(tenant_id)) is not None


# What a held row means for the tenant: the mapping `_decide` projects (a payment that failed is past due,
# one PayPal suspended is unpaid). Both refuse calls; only a healthy plan is active.
_HELD_TENANT_STATUS = {ACTIVE: ACTIVE, PAST_DUE: PAST_DUE, SUSPENDED: UNPAID}
_Q_HELD_STATUS = (
    "SELECT status FROM paypal_subscriptions WHERE tenant_id = $1::uuid "
    f"AND status IN {_HELD_SQL} ORDER BY created_at DESC LIMIT 1"
)


async def held_tenant_status(conn, tenant_id) -> str | None:
    """The tenant status the paying PayPal plan implies, or None when there is no held plan."""
    status = await conn.fetchval(_Q_HELD_STATUS, str(tenant_id))
    return None if status is None else _HELD_TENANT_STATUS[status]


def _state(row) -> str:
    if row["activated_at"] is not None:
        return "activated"
    status = row["status"]
    if status == EXPIRED:
        return "expired"
    if status in (FAILED, CANCELLED):
        return "failed"
    if (status == APPROVAL_PENDING and row["paypal_status"] in (None, "APPROVAL_PENDING")
            and row["approval_url"]):
        return "open"
    return "pending"


def _response(row) -> dict:
    row = dict(row)
    state = _state(row)
    message = None
    if state == "pending":
        message = MSG_CONFIRMING if row["paypal_status"] in ("APPROVED", "ACTIVE") else MSG_UNCONFIRMED
    elif (state == "failed" and row["status"] == CANCELLED
            and row["paypal_status"] in ("APPROVED", "ACTIVE")):
        message = MSG_NOT_USED
    return {
        "request_id": str(row["id"]),
        "state": state,
        "session_id": row["paypal_subscription_id"],
        "checkout_url": row["approval_url"] if state == "open" else None,
        "price_option": public_option(_snap(row)["option"]),
        "mock_mode": False,
        "message": message,
    }


def _unavailable() -> CheckoutError:
    return CheckoutError("paypal_unavailable", PAYPAL_UNAVAILABLE, 503, request_not_started=True)


def _unconfirmed() -> CheckoutError:
    return CheckoutError("checkout_unconfirmed", _UNCONFIRMED, 503)


class PayPalSubscriptionService:
    def __init__(self, pool, client=None) -> None:
        self._pool = pool
        self._client_override = client

    def _client(self):
        if self._client_override is not None:
            return self._client_override
        try:
            return get_paypal_client()
        except PayPalNotConfigured:
            raise _unavailable() from None

    # -- reads -----------------------------------------------------------------

    async def _get(self, tenant_id, request_id) -> dict | None:
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            row = await conn.fetchrow(_Q_ROW_BY_ID, str(request_id), str(tenant_id))
        return dict(row) if row is not None else None

    async def _held(self, tenant_id) -> dict | None:
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            row = await conn.fetchrow(_Q_HELD, str(tenant_id))
        return dict(row) if row is not None else None

    async def _lock(self, conn, tenant_id: str, row_id: str) -> tuple[dict, dict]:
        """Tenant row first, then ours: the one lock order everywhere."""
        tenant = await conn.fetchrow(_Q_TENANT_LOCK, tenant_id)
        locked = await conn.fetchrow(_Q_ROW_LOCK, row_id, tenant_id)
        if tenant is None or locked is None:
            raise CheckoutError("checkout_not_found", "Checkout request was not found.", 404)
        return dict(tenant), dict(locked)

    # -- start -----------------------------------------------------------------

    async def start_checkout(self, *, tenant_id, request_id, price_option_id,
                             allow_team_only=False) -> dict:
        tenant_id, price_option_id = str(tenant_id), str(price_option_id)
        try:
            request_id = str(UUID(str(request_id)))
        except ValueError:
            raise CheckoutError("checkout_invalid", "Invalid checkout reference.", 400,
                                request_not_started=True) from None
        mode = paypal_provider_mode()
        if mode is None:
            raise _unavailable()
        self._client()  # PayPalNotConfigured: refused before any row exists

        row = await self._get(tenant_id, request_id)
        if row is not None:
            return await self._continue(row, price_option_id)

        try:
            option = await resolve_checkout_option(
                self._pool, price_option_id, allow_team_only=allow_team_only)
        except BillingCatalogUnavailable as exc:
            raise CheckoutError("billing_offer_unavailable", str(exc), 400,
                                request_not_started=True) from None
        plan = await self._plan_option(option, mode)
        base = billing_return_urls()
        snapshot = {
            "option": {
                key: option.get(key) for key in (
                    "plan_id", "plan_name", "kind", "interval", "interval_count",
                    "amount_minor", "currency", "currency_exponent", "provider_mode", "minutes")
            } | {"id": str(option["id"])},
            "paypal_plan_id": plan["paypal_plan_id"],
            "return_url": f"{base}?checkout=success&provider=paypal&request_id={request_id}",
            "cancel_url": f"{base}?checkout=cancelled&provider=paypal&request_id={request_id}",
        }

        kind, row = await self._claim(tenant_id, request_id, price_option_id, option, snapshot, mode)
        if kind == "release":
            await self._release_open(row)
            kind, row = await self._claim(
                tenant_id, request_id, price_option_id, option, snapshot, mode)
            if kind == "release":
                raise CheckoutError(
                    "checkout_outstanding", "A PayPal purchase is already in progress for this account.",
                    request_not_started=True, existing_attempt=_response(row))
        return await self._continue(row, price_option_id)

    async def _plan_option(self, option: dict, mode: str) -> dict:
        async with acquire_with_tenant(self._pool, None) as conn:
            plan = await conn.fetchrow(_Q_PLAN_OPTION, str(option["id"]))
        refused = CheckoutError(
            "paypal_offer_unavailable", "PayPal is not available for this plan yet.", 400,
            request_not_started=True)
        if (plan is None or not plan["active"] or plan["verified_at"] is None
                or plan["provider_mode"] != mode or option.get("provider_mode") != mode
                or plan["interval"] != option.get("interval")
                or int(plan["amount_minor"]) != option.get("amount_minor")
                or str(plan["currency"]).lower() != str(option.get("currency")).lower()
                or str(option.get("currency")).upper() not in SUPPORTED_CURRENCIES):
            raise refused
        return dict(plan)

    async def _claim(self, tenant_id, request_id, price_option_id, option, snapshot, mode):
        """One claim transaction: ``("existing"|"inserted"|"release", row)``."""
        try:
            async with acquire_with_tenant(self._pool, tenant_id) as conn:
                tenant = await conn.fetchrow(_Q_TENANT_LOCK, tenant_id)
                if tenant is None or tenant["status"] != "active":
                    raise CheckoutError("tenant_unavailable", "Billing account is unavailable.", 403,
                                        request_not_started=True)
                if canonical(tenant["subscription_status"]) == SUSPENDED:
                    raise CheckoutError(
                        "tenant_suspended", "This account is suspended and cannot buy a plan. Contact support.",
                        403, request_not_started=True)
                existing = await conn.fetchrow(_Q_ROW_BY_ID, request_id, tenant_id)
                if existing is not None:
                    return "existing", dict(existing)
                exists = CheckoutError(
                    "subscription_exists", "You already have a subscription. Manage it on the billing page.",
                    request_not_started=True)
                if tenant["stripe_subscription_id"] or await conn.fetchval(_Q_STRIPE_SUB_EXISTS, tenant_id):
                    raise exists
                outstanding = await conn.fetchrow(_Q_STRIPE_ATTEMPT, tenant_id)
                if outstanding is not None:
                    attempt = dict(outstanding)
                    attempt["snapshot"] = _snap(attempt)
                    raise CheckoutError(
                        "checkout_outstanding", "Resume or reconcile your existing checkout before starting another.",
                        request_not_started=True, existing_attempt=CheckoutAttempts.response(attempt))
                other = await conn.fetchrow(_Q_OTHER_LIVE, tenant_id)
                if other is not None:
                    if other["status"] in HELD:
                        raise exists
                    return "release", dict(other)
                await conn.execute(
                    _Q_INSERT, request_id, tenant_id, price_option_id, option["plan_id"], mode,
                    snapshot["paypal_plan_id"], json.dumps(snapshot, default=str))
                inserted = await conn.fetchrow(_Q_ROW_BY_ID, request_id, tenant_id)
                return "inserted", dict(inserted)
        except asyncpg.UniqueViolationError:
            raise CheckoutError(
                "checkout_conflict", "This checkout identity or account already has another request.",
            ) from None

    async def _continue(self, row: dict, price_option_id: str) -> dict:
        if str(row["price_option_id"]) != price_option_id:
            raise CheckoutError("checkout_payload_conflict",
                                "This checkout reference belongs to a different offer.")
        if row["status"] not in (CREATING, UNKNOWN):
            return await self.get_attempt(tenant_id=row["tenant_id"], request_id=row["id"])
        return await self._create_at_paypal(row)

    async def _save_created(self, tenant_id, row_id, paypal_id, url, paypal_status) -> int:
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            return _rows(await conn.execute(
                _Q_SAVE_CREATED, str(row_id), str(tenant_id), paypal_id, url, paypal_status))

    async def _mark_unknown(self, tenant_id, row_id) -> None:
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            await conn.execute(_Q_MARK_UNKNOWN, str(row_id), str(tenant_id))

    async def _fail_unstarted(self, tenant_id, row_id) -> None:
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            await conn.execute(_Q_FAIL_UNSTARTED, str(row_id), str(tenant_id))

    async def _close_open(self, tenant_id, row_id, status: str) -> None:
        """Give up an unfinished purchase: ``failed`` (abandoned) or ``expired``."""
        async with acquire_with_tenant(self._pool, str(tenant_id)) as conn:
            await conn.execute(_Q_CLOSE_OPEN, str(row_id), str(tenant_id), status)

    async def _create_at_paypal(self, row: dict) -> dict:
        """Step 5: create at PayPal outside any transaction; the retry repeats the same request id."""
        tenant_id, rid = str(row["tenant_id"]), str(row["id"])
        snap = _snap(row)
        client = self._client()
        try:
            sub = await client.create_subscription(
                request_id=f"plan-sub-{rid}", plan_id=row["paypal_plan_id"], custom_id=rid,
                return_url=snap["return_url"], cancel_url=snap["cancel_url"], brand_name=_BRAND)
            if not (isinstance(sub, dict) and isinstance(sub.get("id"), str)
                    and _ID_RE.fullmatch(sub["id"])
                    and sub.get("status") in ("APPROVAL_PENDING", "APPROVED", "ACTIVE")
                    and sub.get("plan_id") == row["paypal_plan_id"]
                    and sub.get("custom_id") in (None, rid)):
                raise _BadAnswer
            url = _pc.subscription_approval_url(sub, env=paypal_env())
            if sub["status"] == "APPROVAL_PENDING" and url is None:
                raise _BadAnswer
            if await self._save_created(tenant_id, rid, sub["id"], url, sub["status"]) != 1:
                raise _BadAnswer
        except asyncio.CancelledError:
            await asyncio.shield(self._mark_unknown(tenant_id, rid))
            raise
        except PayPalError as exc:
            if exc.retryable:
                await self._mark_unknown(tenant_id, rid)
                raise _unconfirmed() from None
            logger.error(
                "paypal_plan_create_refused row=%s status=%s name=%s issue=%s debug_id=%s",
                _short(rid), exc.status, exc.name, exc.issue, exc.debug_id)
            await self._fail_unstarted(tenant_id, rid)
            raise CheckoutError("paypal_refused",
                                "PayPal could not start this purchase. Please try again.", 502) from None
        except _BadAnswer:
            logger.error("paypal_plan_create_answer_rejected row=%s", _short(rid))
            await self._mark_unknown(tenant_id, rid)
            raise _unconfirmed() from None
        except Exception as exc:
            logger.error("paypal_plan_create_failed row=%s error=%s", _short(rid), type(exc).__name__)
            await self._mark_unknown(tenant_id, rid)
            raise _unconfirmed() from None
        logger.info("paypal_plan_started row=%s paypal_status=%s", _short(rid), sub["status"])
        return _response(await self._get(tenant_id, rid))

    async def _release_open(self, row: dict) -> None:
        """An unfinished PayPal purchase of this tenant stands in the way of a new one."""
        tenant_id, rid = str(row["tenant_id"]), str(row["id"])
        if not row["paypal_subscription_id"]:
            if datetime.now(UTC) - row["created_at"] < _NO_ID_FRESH:
                return
            await self._create_at_paypal(row)  # learns the id; its CheckoutError propagates
            row = await self._get(tenant_id, rid)
        try:
            sub = await self._client().get_subscription(row["paypal_subscription_id"])
        except PayPalError:
            raise _unconfirmed() from None
        if isinstance(sub, dict) and sub.get("status") == "APPROVAL_PENDING":
            await self._close_open(tenant_id, rid, FAILED)
            logger.info("paypal_plan_attempt_abandoned row=%s", _short(rid))
            return
        await self._apply(row, sub)

    async def release_open_for_tenant(self, tenant_id) -> None:
        """Let an unfinished PayPal purchase stop standing in the way of a card checkout."""
        tenant_id = str(tenant_id)
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            row = await conn.fetchrow(_Q_OPEN_ROW, tenant_id)
        if row is None:
            return
        try:
            await self._release_open(dict(row))
        except CheckoutError as exc:
            logger.warning("paypal_plan_release_skipped row=%s code=%s", _short(row["id"]), exc.code)

    async def get_attempt(self, *, tenant_id, request_id) -> dict:
        row = await self._get(tenant_id, request_id)
        if row is None:
            raise CheckoutError("checkout_not_found", "Checkout request was not found.", 404)
        if (row["paypal_subscription_id"] and row["activated_at"] is None
                and row["status"] in OPEN + (FAILED, EXPIRED)):
            try:
                sub = await self._client().get_subscription(row["paypal_subscription_id"])
                await self._apply(row, sub)
            except (PayPalError, CheckoutError) as exc:
                logger.warning("paypal_plan_refresh_failed row=%s error=%s",
                               _short(row["id"]), type(exc).__name__)
            row = await self._get(tenant_id, request_id)
        return _response(row)

    # -- the one place PayPal's truth becomes our state ----------------------------

    async def sync(self, paypal_subscription_id: str) -> SyncOutcome | None:
        if not isinstance(paypal_subscription_id, str) or not _ID_RE.fullmatch(paypal_subscription_id):
            return None
        async with acquire_with_tenant(self._pool, None) as conn:
            row = await conn.fetchrow(_Q_ROW_BY_PP, paypal_subscription_id)
        if row is None:
            return None
        sub = await self._client().get_subscription(paypal_subscription_id)  # PayPalError: caller retries
        return await self._apply(dict(row), sub)

    async def _paid_in_transactions(self, row, option, now):
        try:
            txns = await self._client().list_subscription_transactions(
                row["paypal_subscription_id"], start_time=row["created_at"] - timedelta(days=1),
                end_time=now + timedelta(days=1))
        except PayPalError:
            return False, None
        for txn in txns:
            if (isinstance(txn, dict) and str(txn.get("status")).upper() == "COMPLETED"
                    and _amount_matches(_gross(txn), option)):
                return True, _parse_time(txn.get("time"))
        return False, None

    async def _apply(self, row, sub) -> SyncOutcome:
        row = dict(row)
        rid, tenant_id = str(row["id"]), str(row["tenant_id"])
        option = _snap(row)["option"]
        if (not isinstance(sub, dict) or sub.get("id") != row["paypal_subscription_id"]
                or sub.get("plan_id") != row["paypal_plan_id"]
                or sub.get("custom_id") not in (None, rid)):
            logger.error("paypal_plan_binding_mismatch row=%s", _short(rid))
            await self._record_review(row, "binding")
            return SyncOutcome(rid, tenant_id, row["status"], row["activated_at"] is not None,
                               review=True)
        pp = sub.get("status") if isinstance(sub.get("status"), str) else ""
        info = sub.get("billing_info") if isinstance(sub.get("billing_info"), dict) else {}
        failed = _count(info.get("failed_payments_count"))
        paid, paid_at = _last_payment(info.get("last_payment"), option)
        next_billing = _parse_time(info.get("next_billing_time"))
        now = datetime.now(UTC)
        if pp == "ACTIVE" and row["activated_at"] is None and not paid:
            paid, paid_at = await self._paid_in_transactions(row, option, now)

        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            tenant, locked = await self._lock(conn, tenant_id, rid)
            final, action = await self._decide(
                conn, tenant, locked, pp=pp, failed=failed, paid=paid, paid_at=paid_at,
                next_billing=next_billing, now=now, option=option)

        if action == "superseded":
            await self._cancel_quietly(row["paypal_subscription_id"], "Superseded by another purchase")
            await self._record_review(row, "superseded")
            logger.error("paypal_plan_superseded row=%s", _short(rid))
            return SyncOutcome(rid, tenant_id, final, False, review=True)
        if action == "stray":
            await self._cancel_quietly(row["paypal_subscription_id"], "Plan ended")
            await self._record_review(row, "still_running_at_paypal")
            logger.error("paypal_plan_still_running_at_paypal row=%s paypal_status=%s",
                         _short(rid), pp)
            return SyncOutcome(rid, tenant_id, final, locked["activated_at"] is not None, review=True)
        if action == "unpaid_active":
            # PayPal stops charging, the tenant can buy again, a person refunds if money moved.
            await self._cancel_quietly(row["paypal_subscription_id"], "No payment was confirmed")
            await self._record_review(row, "active_without_payment")
            logger.error("paypal_plan_active_without_payment row=%s", _short(rid))
            return SyncOutcome(rid, tenant_id, final, False, review=True)
        if action == "recancel":
            await self._cancel_quietly(row["paypal_subscription_id"], "Cancelled by the customer")
            charged_after = bool(
                paid and paid_at is not None and locked["cancelled_at"] is not None
                and paid_at > locked["cancelled_at"])
            logger.error("paypal_plan_cancel_repeated row=%s charged_after_cancel=%s",
                         _short(rid), charged_after)
            if charged_after:
                # PayPal took a payment after the customer cancelled: a person refunds it.
                await self._record_review(row, "charged_after_cancel")
            await self._payments_then_recheck(row, sub)
            return SyncOutcome(rid, tenant_id, final, True, review=charged_after)
        activated = locked["activated_at"] is not None or action == "activated"
        if action == "activated":
            logger.info("paypal_plan_activated row=%s paypal_status=%s", _short(rid), pp)
        elif action == "ended":
            logger.info("paypal_plan_ended row=%s paypal_status=%s", _short(rid), pp)
        if activated:
            await self._payments_then_recheck(row, sub)
        return SyncOutcome(rid, tenant_id, final, activated)

    async def _decide(self, conn, tenant, locked, *, pp, failed, paid, paid_at, next_billing, now,
                      option) -> tuple[str, str | None]:
        """Decide from the LOCKED row and write. Returns (row status, action)."""
        st, activated = locked["status"], locked["activated_at"] is not None
        changes: dict = {"paypal_status": pp, "failed_payments": failed, "checked_at": now}
        if st == CANCELLED and pp in ("APPROVED", "ACTIVE", "SUSPENDED"):
            # We ended it, but PayPal may keep charging: store the facts, a person looks.
            await _save(conn, locked, changes)
            return st, "stray"
        project, action = None, None
        if pp in ("APPROVED", "ACTIVE") and not activated:
            changes["approved_at"] = locked["approved_at"] or now  # the first time PayPal showed it
        if pp == "APPROVAL_PENDING":
            if st in OPEN:
                if now - locked["created_at"] >= _APPROVAL_WINDOW:
                    changes.update(status=EXPIRED, ended_at=now)
                else:
                    changes["status"] = APPROVAL_PENDING
        elif pp == "APPROVED":
            if st in OPEN:
                changes["status"] = APPROVAL_PENDING
            elif st in (FAILED, EXPIRED):
                action = await self._revive(conn, tenant, locked, changes, now)
        elif pp == "ACTIVE":
            if not activated:
                if paid and st in OPEN + (FAILED, EXPIRED):
                    action = await self._activate(
                        conn, tenant, locked, changes, paid_at=paid_at, next_billing=next_billing,
                        now=now, option=option)
                elif st in OPEN:
                    if now - changes["approved_at"] >= _PAYMENT_WAIT:
                        changes.update(status=CANCELLED, ended_at=now)
                        action = "unpaid_active"
                    else:
                        changes["status"] = APPROVAL_PENDING
                elif st in (FAILED, EXPIRED):
                    action = await self._revive(conn, tenant, locked, changes, now)
            elif st in HELD:
                if locked["cancel_at_period_end"]:
                    # The customer cancelled, yet PayPal still shows it running. The paid
                    # period is never extended; PayPal is asked again after this transaction.
                    action = "recancel"
                elif failed > 0:
                    changes["status"], project = PAST_DUE, PAST_DUE
                else:
                    changes["status"], project = ACTIVE, ACTIVE
                    last = locked["last_payment_at"]
                    if paid and paid_at is not None and (last is None or paid_at > last):
                        changes.update(last_payment_at=paid_at, current_period_start=paid_at)
                    if next_billing is not None:
                        changes["current_period_end"] = next_billing
        elif pp == "SUSPENDED":
            if not activated:
                if st in OPEN:
                    changes.update(status=FAILED, ended_at=now)
            elif st in HELD:
                changes["status"], project = SUSPENDED, UNPAID
        elif pp in ("CANCELLED", "EXPIRED"):
            if not activated:
                if st in OPEN:
                    changes.update(status=CANCELLED if pp == "CANCELLED" else EXPIRED, ended_at=now)
            elif st in HELD:
                period_end = locked["current_period_end"]
                if st == ACTIVE and period_end is not None and period_end > now:
                    changes.update(cancel_at_period_end=True, cancelled_at=locked["cancelled_at"] or now)
                else:
                    action = "ended"

        if action == "ended":
            await self._end(conn, tenant, locked, changes)
            return CANCELLED, action
        await _save(conn, locked, changes)
        if project is not None:
            await self._project(conn, tenant, locked, project)
        return changes.get("status", st), action

    async def _conflict(self, conn, tenant, locked) -> bool:
        """Another purchase of this tenant stands in the way of granting this plan."""
        tenant_id = str(locked["tenant_id"])
        return bool(
            tenant["stripe_subscription_id"]
            or await conn.fetchval(_Q_STRIPE_SUB_EXISTS, tenant_id) is not None
            or await conn.fetchval(_Q_STRIPE_ATTEMPT_EXISTS, tenant_id) is not None
            or await conn.fetchval(_Q_OTHER_LIVE_EXISTS, tenant_id, str(locked["id"])) is not None)

    async def _revive(self, conn, tenant, locked, changes, now) -> str | None:
        """PayPal says a purchase we gave up on is still going: follow it again, or supersede it."""
        if await self._conflict(conn, tenant, locked):
            changes.update(status=CANCELLED, ended_at=now)
            return "superseded"
        changes.update(status=APPROVAL_PENDING, ended_at=None)
        return None

    async def _activate(self, conn, tenant, locked, changes, *, paid_at, next_billing, now,
                        option) -> str:
        """Grant the plan. Runs only for a locked row with ``activated_at IS NULL``."""
        tenant_id = str(locked["tenant_id"])
        if await self._conflict(conn, tenant, locked):
            changes.update(status=CANCELLED, ended_at=now)
            return "superseded"
        await apply_plan_allocation(conn, tenant_id, int(option["minutes"]))
        if canonical(tenant["subscription_status"]) == SUSPENDED:
            # An admin suspension is never lifted by a purchase.
            await conn.execute(_Q_TENANT_PLAN_ONLY, tenant_id, locked["plan_id"])
        else:
            await conn.execute(_Q_TENANT_GRANT, tenant_id, locked["plan_id"], ACTIVE)
        start = paid_at or now
        changes.update(
            status=ACTIVE, activated_at=now, last_payment_at=paid_at, current_period_start=start,
            current_period_end=next_billing or _add_period(start, option["interval"]),
            cancel_at_period_end=False, ended_at=None)
        return "activated"

    async def _project(self, conn, tenant, locked, status: str) -> None:
        """Mirror our status onto the tenant, never over an admin or dispute suspension."""
        if canonical(tenant["subscription_status"]) == SUSPENDED:
            return
        await conn.execute(_Q_TENANT_PROJECT, str(locked["tenant_id"]), status, locked["plan_id"])

    async def _end(self, conn, tenant, locked, changes: dict | None = None, *, reversal: bool = False) -> None:
        """The plan is over: the account becomes pay as you go; bought minutes stay usable.

        A reversal keeps the account blocked (``cancelled``) and still ends the plan's allowance.
        """
        merged = dict(changes or {})
        merged.update(status=CANCELLED, ended_at=datetime.now(UTC), cancel_at_period_end=False)
        await _save(conn, locked, merged)
        await end_plan(conn, str(locked["tenant_id"]), stripe_subscription_id=None, plan_id=locked["plan_id"],
                       ended_status=CANCELLED if reversal else INACTIVE)

    async def _cancel_quietly(self, paypal_subscription_id, reason: str) -> None:
        try:
            await self._client().cancel_subscription(paypal_subscription_id, reason=reason)
        except (PayPalError, CheckoutError) as exc:
            logger.error("paypal_plan_cancel_failed error=%s", type(exc).__name__)

    async def _record_review(self, row: dict, rule: str) -> None:
        """Best effort: one platform-level row so a payment that needs a person is counted."""
        event_id = f"local:review:plan:{row['id']}:{rule}"[:255]
        try:
            async with acquire_with_tenant(self._pool, None) as conn:
                await conn.execute(
                    _Q_REVIEW, event_id, str(row["paypal_subscription_id"] or "")[:255],
                    row["provider_mode"], rule[:255], hashlib.sha256(event_id.encode()).hexdigest())
        except Exception:
            logger.error("paypal_plan_review_record_failed row=%s", _short(row["id"]))

    async def _payments_then_recheck(self, row: dict, sub: dict) -> None:
        """Record the payments; none seen means PayPal's list may lag, so read the row again soon."""
        if await self._record_payments(row, sub) > 0:
            return
        try:
            async with acquire_with_tenant(self._pool, str(row["tenant_id"])) as conn:
                await conn.execute(_Q_RECHECK_SOON, str(row["id"]), str(row["tenant_id"]))
        except Exception as exc:
            logger.warning("paypal_plan_recheck_not_set row=%s error=%s",
                           _short(row["id"]), type(exc).__name__)

    async def _record_payments(self, row: dict, sub: dict) -> int:
        """A record, never a gate: any failure is logged and swallowed. Returns valid payments seen."""
        seen = 0
        try:
            now = datetime.now(UTC)
            start = max(row["created_at"] - timedelta(days=1), now - timedelta(days=95))
            txns = await self._client().list_subscription_transactions(
                row["paypal_subscription_id"], start_time=start, end_time=now + timedelta(days=1))
            tenant_id = str(row["tenant_id"])
            async with acquire_with_tenant(self._pool, tenant_id) as conn:
                for txn in txns:
                    parsed = self._payment_of(txn)
                    if parsed is None:
                        continue
                    seen += 1
                    tid, status, minor, currency, paid_at = parsed
                    done = _rows(await conn.execute(
                        _Q_PAYMENT_UPDATE, tenant_id, str(row["id"]), tid, status))
                    if done == 0:  # unknown, or reversed (the insert then does nothing)
                        await conn.execute(
                            _Q_PAYMENT_INSERT, tenant_id, str(row["id"]), tid, status, minor,
                            currency, paid_at)
        except Exception as exc:
            logger.warning("paypal_plan_payments_not_recorded row=%s error=%s",
                           _short(row["id"]), type(exc).__name__)
            return 0
        return seen

    @staticmethod
    def _payment_of(txn):
        if not isinstance(txn, dict) or not isinstance(txn.get("id"), str) or not _ID_RE.fullmatch(txn["id"]):
            return None
        status = str(txn.get("status") or "").lower()
        gross = _gross(txn)
        paid_at = _parse_time(txn.get("time"))
        if status not in _PAYMENT_STATUSES or not isinstance(gross, dict) or paid_at is None:
            return None
        currency = str(gross.get("currency_code") or "").upper()
        try:
            minor = minor_units(gross.get("value"), currency)
        except ValueError:
            return None
        return txn["id"], status, minor, currency.lower(), paid_at

    # -- cancel ----------------------------------------------------------------

    async def cancel_for_tenant(self, tenant_id) -> dict | None:
        tenant_id = str(tenant_id)
        row = await self._held(tenant_id)
        if row is None:
            return None
        if row["cancel_at_period_end"]:
            return self._cancel_result(row)
        client = self._client()
        failed = CheckoutError("paypal_unavailable",
                               "PayPal could not cancel the subscription. Please try again.", 503)
        accepted = True
        try:
            await client.cancel_subscription(row["paypal_subscription_id"],
                                             reason="Cancelled by the customer")
        except PayPalError as exc:
            if exc.issue != "SUBSCRIPTION_STATUS_INVALID":  # that one: PayPal already ended it
                raise failed from None
            accepted = False
        try:
            sub = await client.get_subscription(row["paypal_subscription_id"])
        except PayPalError:
            if not accepted:
                raise failed from None
            sub = None  # PayPal accepted the cancel; we mark the row ourselves below
        if sub is not None:
            await self._apply(row, sub)
        row = await self._get(tenant_id, row["id"])
        if row["status"] in HELD and not row["cancel_at_period_end"]:
            if not accepted:
                raise failed  # PayPal did not cancel and still shows it running
            await self._mark_cancelled(tenant_id, str(row["id"]))
            row = await self._get(tenant_id, row["id"])
        return self._cancel_result(row)

    async def _mark_cancelled(self, tenant_id: str, row_id: str) -> None:
        """PayPal accepted our cancel: keep access to the end of the paid period, or end now."""
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            tenant, locked = await self._lock(conn, tenant_id, row_id)
            if locked["status"] not in HELD or locked["cancel_at_period_end"]:
                return
            now = datetime.now(UTC)
            end = locked["current_period_end"]
            if locked["status"] == ACTIVE and end is not None and end > now:
                await _save(conn, locked, {"cancel_at_period_end": True,
                                           "cancelled_at": locked["cancelled_at"] or now})
            else:
                await self._end(conn, tenant, locked)

    @staticmethod
    def _cancel_result(row: dict) -> dict:
        access = row["status"] in HELD
        end = row["current_period_end"]
        return {
            "status": ACTIVE if access else CANCELLED,
            "cancel_at_period_end": bool(row["cancel_at_period_end"]),
            "current_period_end": end.isoformat() if end is not None else None,
            "mock_mode": False,
            "message": ("Your plan stays active until the end of the paid period."
                        if access else "Your plan has ended."),
        }

    # -- reversal ----------------------------------------------------------------

    async def end_for_reversal(self, *, transaction_id: str, kind: str, event_id: str) -> str:
        if kind not in ("dispute", "reversal"):
            raise ValueError("kind must be dispute or reversal")
        async with acquire_with_tenant(self._pool, None) as conn:
            payment = await conn.fetchrow(_Q_PAYMENT_BY_TXN, transaction_id)
        if payment is None:
            return "not_ours"
        return await self._reverse(
            str(payment["tenant_id"]), str(payment["subscription_id"]), str(payment["id"]),
            kind, event_id)

    async def owns_transaction(self, transaction_id: str) -> bool:
        """Read-only: is this PayPal transaction id one of our plan payments?

        Same cross-tenant lookup as ``end_for_reversal`` (platform connection, by the
        PayPal-global transaction id from a signature-verified webhook); only a
        boolean leaves, never the tenant."""
        async with acquire_with_tenant(self._pool, None) as conn:
            return await conn.fetchrow(_Q_PAYMENT_BY_TXN, transaction_id) is not None

    async def end_subscription_for_reversal(self, *, paypal_subscription_id: str, kind: str,
                                            event_id: str) -> str:
        """Same effect as ``end_for_reversal``, found by PayPal's subscription id; no payment row."""
        if kind not in ("dispute", "reversal"):
            raise ValueError("kind must be dispute or reversal")
        if not isinstance(paypal_subscription_id, str) or not _ID_RE.fullmatch(paypal_subscription_id):
            return "not_ours"
        async with acquire_with_tenant(self._pool, None) as conn:
            row = await conn.fetchrow(_Q_ROW_BY_PP, paypal_subscription_id)
        if row is None:
            return "not_ours"
        return await self._reverse(str(row["tenant_id"]), str(row["id"]), None, kind, event_id)

    async def _reverse(self, tenant_id: str, subscription_id: str, payment_id: str | None,
                       kind: str, event_id: str) -> str:
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            tenant, locked = await self._lock(conn, tenant_id, subscription_id)
            if payment_id is not None:
                await conn.execute(_Q_PAYMENT_REVERSE, payment_id, tenant_id)
            ended = locked["status"] in HELD
            if ended:
                await self._end(conn, tenant, locked, reversal=True)
            elif locked["activated_at"] is not None:
                await conn.execute(_Q_TENANT_REBLOCK, tenant_id, CANCELLED, PAYG_PLAN_ID)
        if ended:
            await self._cancel_quietly(locked["paypal_subscription_id"], "Payment reversed")
        await self._record_review(locked, kind)
        logger.error("paypal_plan_reversal row=%s kind=%s ended=%s event=%s",
                     _short(locked["id"]), kind, ended, str(event_id)[:64])
        return "ended" if ended else "already"

    # -- sweep -----------------------------------------------------------------

    async def sweep(self, *, limit: int = 50) -> dict[str, int]:
        counts = {"ended": 0, "checked": 0, "failed": 0, "unconfirmed": 0}
        async with acquire_with_tenant(self._pool, None) as conn:
            due = await conn.fetch(_Q_SWEEP_DUE, limit)
            stale = await conn.fetch(_Q_SWEEP_STALE, limit)
            abandoned = await conn.fetch(_Q_SWEEP_ABANDONED, limit)
            stray = await conn.fetch(_Q_SWEEP_STRAY, limit)
        for item in due:
            try:
                counts["ended"] += await self._end_if_due(str(item["tenant_id"]), str(item["id"]))
            except Exception as exc:
                counts["failed"] += 1
                logger.error("paypal_plan_sweep_failed row=%s error=%s",
                             _short(item["id"]), type(exc).__name__)
        followed: set[str] = set()
        for item in (*stale, *abandoned, *stray):
            row = dict(item)
            if str(row["id"]) in followed:
                continue
            followed.add(str(row["id"]))
            try:
                await self._refresh(row)
                counts["checked"] += 1
            except Exception as exc:
                counts["failed"] += 1
                logger.error("paypal_plan_sweep_failed row=%s error=%s",
                             _short(row["id"]), type(exc).__name__)
        # After the refreshes: rows that could be read now are no longer unconfirmed.
        async with acquire_with_tenant(self._pool, None) as conn:
            unconfirmed = await conn.fetch(_Q_SWEEP_UNCONFIRMED, limit)
        for item in unconfirmed:
            row = dict(item)
            await self._record_review(row, "not_confirmed_with_paypal")
            logger.error("paypal_plan_not_confirmed_with_paypal row=%s", _short(row["id"]))
            counts["unconfirmed"] += 1
        return counts

    async def _end_if_due(self, tenant_id: str, row_id: str) -> int:
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            tenant, locked = await self._lock(conn, tenant_id, row_id)
            end = locked["current_period_end"]
            if not (locked["status"] == ACTIVE and locked["cancel_at_period_end"]
                    and end is not None and end <= datetime.now(UTC)):
                return 0
            await self._end(conn, tenant, locked)
        logger.info("paypal_plan_ended row=%s reason=period_over", _short(row_id))
        return 1

    async def _refresh(self, row: dict) -> None:
        if not row["paypal_subscription_id"]:
            if datetime.now(UTC) - row["created_at"] >= _APPROVAL_WINDOW:
                await self._close_open(row["tenant_id"], row["id"], EXPIRED)
            return
        sub = await self._client().get_subscription(row["paypal_subscription_id"])
        await self._apply(row, sub)

    # -- reads for the billing page ------------------------------------------------

    async def current_for_tenant(self, tenant_id) -> dict | None:
        row = await self._held(str(tenant_id))
        if row is None:
            return None
        return {
            "provider": "paypal",
            "subscription_status": row["status"],
            "current_period_start": row["current_period_start"],
            "current_period_end": row["current_period_end"],
            "cancel_at_period_end": bool(row["cancel_at_period_end"]),
            "purchased_price_option": public_option(_snap(row)["option"]),
        }

    async def payments_for_tenant(self, tenant_id, *, limit: int = 10, payment_id=None) -> list[dict]:
        tenant_id = str(tenant_id)
        limit = max(1, min(int(limit), 100))
        async with acquire_with_tenant(self._pool, tenant_id) as conn:
            rows = await conn.fetch(
                _Q_PAYMENTS, tenant_id, limit, str(payment_id) if payment_id else None)
        return [self._invoice(dict(r)) for r in rows]

    @staticmethod
    def _invoice(row: dict) -> dict:
        status = row["status"]
        amount = int(row["amount_minor"])
        currency = str(row["currency"]).lower()
        option = _snap(row)["option"]
        paid_at = row["paid_at"].isoformat()
        return {
            "id": str(row["id"]),
            "tenant_id": str(row["tenant_id"]),
            "stripe_invoice_id": None,
            "status": _INVOICE_STATUS[status],
            "invoice_pdf": None,
            "hosted_invoice_url": None,
            "paid_at": paid_at,
            "due_date": None,
            "created_at": row["created_at"].isoformat(),
            "detail_status": "complete",
            "detail_source": "stored_summary",
            "captured_at": None,
            "source": "paypal",
            "source_reference": row["paypal_transaction_id"],
            "provider_mode": row["provider_mode"],
            "invoice_number": None,
            "currency": currency,
            "currency_exponent": 2,
            "amount_due": amount,
            "amount_paid": amount if status in _PAID_STATES else 0,
            "amount_remaining": 0,
            "subtotal": amount,
            "total": amount,
            "discounts": None,
            "taxes": None,
            "credits": None,
            "refunds": None,
            "line_items": [{
                "id": row["paypal_transaction_id"],
                "description": f"{option.get('plan_name')} plan",
                "quantity": 1,
                "amount": amount,
                "currency": currency,
                "period_start": None,
                "period_end": None,
            }],
            "period_start": None,
            "period_end": None,
            "errors": [],
        }
