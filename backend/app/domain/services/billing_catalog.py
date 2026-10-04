"""Server-owned billing catalog. Never derive prices from UI discounts."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.domain.services.billing_mode import get_billing_mode

logger = logging.getLogger(__name__)
# Explicit supported Stripe charge conventions; unknown currencies fail closed.
_EXPONENTS = {
    code: 2
    for code in (
        "usd",
        "cad",
        "gbp",
        "eur",
        "aud",
        "nzd",
        "chf",
        "aed",
        "sgd",
        "hkd",
        "inr",
        "pkr",
        "brl",
        "mxn",
        "zar",
        "huf",
        "twd",
        "isk",
        "ugx",
    )
}
_EXPONENTS.update(
    {
        code: 0
        for code in (
            "jpy",
            "krw",
            "vnd",
            "clp",
            "bif",
            "djf",
            "gnf",
            "kmf",
            "mga",
            "pyg",
            "rwf",
            "vuv",
            "xaf",
            "xof",
            "xpf",
        )
    }
)
_EXPONENTS.update({code: 3 for code in ("bhd", "jod", "kwd", "omr", "tnd")})
_PUBLIC_FIELDS = (
    "id",
    "kind",
    "interval",
    "interval_count",
    "amount_minor",
    "currency",
    "currency_exponent",
    "active",
)


class BillingCatalogUnavailable(ValueError):
    pass


def validate_option(option: dict, mode: str) -> None:
    """Require locally approved, complete and compatible purchase terms."""
    if not option.get("active") or not option.get("verified_at"):
        raise BillingCatalogUnavailable("This price option is not approved for checkout.")
    amount = option.get("amount_minor")
    minutes = option.get("minutes")
    if type(amount) is not int or amount < 0 or type(minutes) is not int or minutes < 0:
        raise BillingCatalogUnavailable("This price option has incomplete purchase terms.")
    currency = option.get("currency")
    exponent = option.get("currency_exponent")
    if currency not in _EXPONENTS or type(exponent) is not int or exponent != _EXPONENTS[currency]:
        raise BillingCatalogUnavailable("This currency configuration is not supported.")
    if currency in ("isk", "ugx") and amount % 100:
        raise BillingCatalogUnavailable("This currency cannot contain fractional amounts.")
    if (
        option.get("interval") not in ("month", "year")
        or type(option.get("interval_count")) is not int
        or option["interval_count"] != 1
    ):
        raise BillingCatalogUnavailable("This billing interval is not supported.")
    if option.get("kind") == "free":
        if (
            amount != 0
            or option.get("stripe_price_id")
            or option.get("stripe_product_id")
            or option.get("provider_mode") != "free"
            or option.get("interval") != "month"
        ):
            raise BillingCatalogUnavailable("This free price option is invalid.")
        return
    if (
        option.get("kind") != "stripe"
        or amount <= 0
        or not str(option.get("stripe_price_id") or "").startswith("price_")
        or not str(option.get("stripe_product_id") or "").startswith("prod_")
    ):
        raise BillingCatalogUnavailable("This paid price option is incomplete.")
    if mode not in ("live", "test") or option.get("provider_mode") != mode:
        raise BillingCatalogUnavailable("Checkout is unavailable in the current billing mode.")
    if not os.getenv("STRIPE_WEBHOOK_SECRET", "").strip():
        raise BillingCatalogUnavailable("Payment confirmation is not configured.")


def validate_provider_price(option: dict, price, *, require_active: bool = True) -> None:
    """Verify exact fixed recurring terms against the provider response."""

    def get(obj, key, default=None):
        return obj.get(key, default) if hasattr(obj, "get") else getattr(obj, key, default)

    recurring = get(price, "recurring") or {}
    product = get(price, "product")
    product_id = product if isinstance(product, str) else get(product, "id")
    amount = get(price, "unit_amount")
    if (
        get(price, "id") != option["stripe_price_id"]
        or (require_active and get(price, "active") is not True)
        or get(price, "type") != "recurring"
        or get(price, "billing_scheme") != "per_unit"
        or get(price, "livemode") is not (option["provider_mode"] == "live")
        or type(amount) is not int
        or amount != option["amount_minor"]
        or get(price, "currency") != option["currency"]
        or product_id != option["stripe_product_id"]
        or get(recurring, "interval") != option["interval"]
        or get(recurring, "interval_count") != option["interval_count"]
        or get(recurring, "usage_type") != "licensed"
        or get(price, "custom_unit_amount")
        or get(price, "transform_quantity")
        or get(price, "tiers_mode")
    ):
        raise BillingCatalogUnavailable(
            "The configured price no longer matches its approved terms."
        )


async def resolve_checkout_option(pool, option_id) -> dict:
    try:
        option_id = UUID(str(option_id))
    except (ValueError, TypeError) as exc:
        raise BillingCatalogUnavailable("Unknown price option.") from exc
    async with acquire_with_tenant(pool, None) as conn:
        row = await conn.fetchrow(
            """SELECT o.*, p.minutes, p.name AS plan_name
            FROM plan_price_options o JOIN plans p ON p.id=o.plan_id WHERE o.id=$1""",
            option_id,
        )
    if not row:
        raise BillingCatalogUnavailable("Unknown price option.")
    option = dict(row)
    option["id"] = str(option["id"])
    option["free"] = option["kind"] == "free"
    validate_option(option, get_billing_mode())
    return option


async def list_plan_catalog(pool) -> list[dict]:
    mode = get_billing_mode()
    async with acquire_with_tenant(pool, None) as conn:
        plans = await conn.fetch(
            "SELECT id,name,price,description,minutes,agents,concurrent_calls,features,not_included,popular FROM plans ORDER BY price,id"
        )
        rows = await conn.fetch(
            """SELECT o.*, p.minutes, p.name AS plan_name FROM plan_price_options o
            JOIN plans p ON p.id=o.plan_id ORDER BY o.plan_id,o.interval,o.created_at"""
        )
    semaphore = asyncio.Semaphore(4)

    async def present(row):
        option = dict(row)
        option["id"] = str(option["id"])
        public = {key: option[key] for key in _PUBLIC_FIELDS}
        reason = None
        try:
            validate_option(option, mode)
            if option["kind"] == "stripe":
                import stripe

                async with semaphore:
                    http_client = stripe.HTTPXClient(timeout=5)
                    try:
                        client = stripe.StripeClient(
                            os.environ["STRIPE_SECRET_KEY"],
                            max_network_retries=0,
                            http_client=http_client,
                        )
                        price = await asyncio.wait_for(
                            client.v1.prices.retrieve_async(option["stripe_price_id"]), timeout=6
                        )
                    finally:
                        await http_client.close_async()
                        http_client.close()
                validate_provider_price(option, price)
        except BillingCatalogUnavailable as exc:
            reason = str(exc)
        except Exception:
            reason = "The payment provider could not verify this price. Please try again."
            logger.warning("Billing catalog provider verification unavailable")
        public.update(checkout_available=reason is None, unavailable_reason=reason)
        return option["plan_id"], public

    presented = await asyncio.gather(
        *(
            present(row)
            for row in rows
            if row["provider_mode"] == "free" or row["provider_mode"] == mode
        )
    )
    result = []
    for row in plans:
        plan = dict(row)
        for field in ("features", "not_included"):
            value = plan.get(field)
            if isinstance(value, str):
                value = json.loads(value)
            if value is not None and (
                not isinstance(value, list) or not all(isinstance(item, str) for item in value)
            ):
                raise BillingCatalogUnavailable("Plan descriptions could not be verified.")
            plan[field] = value or []
        # Legacy amount remains for older read-only consumers, never checkout.
        plan["price"] = float(plan["price"]) if plan["price"] is not None else None
        plan["billing_mode"] = mode
        plan["price_options"] = [option for plan_id, option in presented if plan_id == plan["id"]]
        result.append(plan)
    return result
