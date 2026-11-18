"""Create the PayPal product and one PayPal billing plan per approved price option.

Sibling of ``paypal_setup.py``. Idempotent, a dry run unless ``--apply``. It
never prints a credential, token or settings value: only setting NAMES.

PayPal's settings come from the PROCESS ENVIRONMENT (load the service's
EnvironmentFile first); ``--env-file`` is only written to.

1. Reads the approved Stripe-kind price options for the current billing mode.
2. For each one, checks the PayPal plan recorded in ``paypal_plan_options``
   against PayPal; creates the product and plan when missing (a plan that
   exists at PayPal but has no row is picked up, not created twice).
3. ``--self-check`` (needs ``--apply``) proves the real request and answer
   shapes with an order and a subscription nobody approves, so nothing is
   charged, and checks the webhook subscription.
4. ``--enable`` writes ``PAYPAL_PLANS_ENABLED`` into ``--env-file`` only when
   nothing failed (``off`` is always written).

Restart the services afterwards so they read the new environment.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stripe_catalog_setup import upsert_env  # noqa: E402

WEBHOOK_PATH = "/api/v1/billing/paypal/webhooks"
PRODUCT_NAME = "Talk-Lee plans"
_PAGE_SIZE = 20
_MAX_PAGES = 25


def plan_matches(plan: Any, *, product_id: str, interval: str, value: str, currency: str) -> bool:
    """Whether a PayPal plan has exactly the wanted terms and nothing that changes the charge.

    The product grants a plan only for a payment of exactly the price, so a plan
    with a setup fee or a tax line would take money and never grant anything, and
    one with a fixed number of cycles would stop by itself. All three are refused.
    """
    from decimal import Decimal, InvalidOperation

    from app.domain.services.paypal_client import minor_units

    try:
        code = currency.upper()
        if plan.get("status") != "ACTIVE" or plan.get("product_id") != product_id:
            return False
        cycles = plan.get("billing_cycles")
        if not isinstance(cycles, list) or len(cycles) != 1:
            return False
        cycle = cycles[0]
        if cycle.get("tenure_type") != "REGULAR":
            return False
        frequency = cycle["frequency"]
        if frequency.get("interval_unit") != interval.upper():
            return False
        count = frequency.get("interval_count")
        if isinstance(count, bool) or count != 1:
            return False
        total = cycle.get("total_cycles")
        if total is not None and (isinstance(total, bool) or total != 0):
            return False  # 0 means it never runs out
        fixed = cycle["pricing_scheme"]["fixed_price"]
        if fixed.get("currency_code") != code:
            return False
        if minor_units(fixed.get("value"), code) != minor_units(value, code):
            return False
        preferences = plan.get("payment_preferences")
        fee = preferences.get("setup_fee") if preferences is not None else None
        if fee is not None and minor_units(fee.get("value"), str(fee.get("currency_code")).upper()) != 0:
            return False
        taxes = plan.get("taxes")
        if taxes is not None and Decimal(str(taxes.get("percentage", "0"))) != 0:
            return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation):
        return False


class PlanStore:
    """The database side: approved price options and the PayPal plan rows."""

    async def _connect(self):
        import asyncpg

        from app.core.config import get_settings

        return await asyncpg.connect(get_settings().database_url.replace("+asyncpg", ""))

    async def price_options(self, mode: str) -> list[dict]:
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.bypass_rls','true',true)")
                rows = await conn.fetch(
                    """SELECT o.id, o.plan_id, p.name AS plan_name, o.interval, o.amount_minor,
                              o.currency, o.currency_exponent
                       FROM plan_price_options o JOIN plans p ON p.id = o.plan_id
                       WHERE o.kind = 'stripe' AND o.active AND o.verified_at IS NOT NULL
                         AND o.provider_mode = $1
                       ORDER BY o.amount_minor, o.plan_id, o.interval""",
                    mode,
                )
                return [dict(r) for r in rows]
        finally:
            await conn.close()

    async def plan_rows(self) -> dict[str, dict]:
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.bypass_rls','true',true)")
                rows = await conn.fetch(
                    """SELECT price_option_id, provider_mode, paypal_product_id, paypal_plan_id,
                              interval, amount_minor, currency
                       FROM paypal_plan_options"""
                )
                return {str(r["price_option_id"]): dict(r) for r in rows}
        finally:
            await conn.close()

    async def save(
        self, *, price_option_id, provider_mode, paypal_product_id, paypal_plan_id,
        interval, amount_minor, currency,
    ) -> None:
        conn = await self._connect()
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.bypass_rls','true',true)")
                await conn.execute(
                    """INSERT INTO paypal_plan_options
                       (price_option_id, provider_mode, paypal_product_id, paypal_plan_id,
                        interval, amount_minor, currency, active, verified_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, NOW())
                       ON CONFLICT (price_option_id) DO UPDATE SET
                         provider_mode = EXCLUDED.provider_mode,
                         paypal_product_id = EXCLUDED.paypal_product_id,
                         paypal_plan_id = EXCLUDED.paypal_plan_id,
                         interval = EXCLUDED.interval,
                         amount_minor = EXCLUDED.amount_minor,
                         currency = EXCLUDED.currency,
                         active = TRUE, verified_at = NOW(), updated_at = NOW()""",
                    price_option_id, provider_mode, paypal_product_id, paypal_plan_id,
                    interval, amount_minor, currency,
                )
        finally:
            await conn.close()


def _preflight(args) -> str | None:
    from app.domain.services import billing_mode, paypal_mode

    env = paypal_mode.paypal_env()
    if env is None:
        return "PayPal is not configured; missing setting: PAYPAL_ENV (live or sandbox)"
    if paypal_mode.paypal_credentials() is None:
        names = ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"]
        if env == "sandbox":
            names = ["PAYPAL_SANDBOX_CLIENT_ID", "PAYPAL_SANDBOX_CLIENT_SECRET"]
        return "PayPal is not configured; missing settings: " + ", ".join(names)
    if paypal_mode.paypal_provider_mode() != billing_mode.get_billing_mode():
        return "PayPal and the billing mode differ; nothing changed"
    if args.self_check:
        hook = args.webhook_url or ""
        if not hook.startswith("https://") or not hook.endswith(WEBHOOK_PATH):
            return f"--webhook-url must be the public https URL ending with {WEBHOOK_PATH}"
        site = args.site_url or ""
        if not site.startswith("https://") or "/" in site[len("https://"):] or len(site) == len("https://"):
            return "--site-url must be https with no path"
    return None


async def _find_product(client) -> str | None:
    for page in range(1, _MAX_PAGES + 1):
        items = await client.list_products(page=page, page_size=_PAGE_SIZE)
        for item in items:
            if item.get("name") == PRODUCT_NAME and isinstance(item.get("id"), str):
                return item["id"]
        if len(items) < _PAGE_SIZE:
            return None
    return None


async def _find_plan(client, *, product_id, name, interval, value, currency) -> dict | None:
    for page in range(1, _MAX_PAGES + 1):
        items = await client.list_plans(product_id=product_id, page=page, page_size=_PAGE_SIZE)
        for item in items:
            if item.get("name") == name and plan_matches(
                item, product_id=product_id, interval=interval, value=value, currency=currency
            ):
                return item
        if len(items) < _PAGE_SIZE:
            return None
    return None


def _line(name: str, error: str | None) -> bool:
    print(f"self-check {name}: ok" if error is None else f"self-check {name}: FAILED ({error})")
    return error is None


async def _self_check(args, client, env, mode, options, plan_ids) -> bool:
    from app.domain.services.paypal_client import PayPalError, approval_url, subscription_approval_url
    from app.domain.services.paypal_mode import PAYPAL_WEBHOOK_EVENTS

    site = args.site_url.rstrip("/")
    good = True
    order_id = None
    try:
        order = await client.create_order(
            request_id=f"selfcheck-order-{uuid4()}", reference_id="selfcheck", custom_id="selfcheck",
            description="Talk-Lee self-check (never paid)",
            currency=options[0]["currency"].upper(), value="1.00",
            return_url=f"{site}/billing?selfcheck=1", cancel_url=f"{site}/billing?selfcheck=0",
            brand_name="Talk-Lee",
        )
        order_id = order.get("id")
        problem = None
        if not isinstance(order_id, str):
            problem = "no order id"
        elif order.get("status") not in ("CREATED", "PAYER_ACTION_REQUIRED"):
            problem = "unexpected order status"
        elif approval_url(order, env=env) is None:
            problem = "no approval link"
    except PayPalError as error:
        problem = str(error)
    good &= _line("order", problem)

    try:
        if not isinstance(order_id, str):
            problem = "no order to read"
        else:
            read = await client.get_order(order_id)
            problem = None if read.get("id") == order_id else "order id differs"
    except PayPalError as error:
        problem = str(error)
    good &= _line("order-read", problem)

    cheapest = plan_ids.get(str(options[0]["id"]))
    sub_id = None
    try:
        if cheapest is None:
            problem = "no PayPal plan for the cheapest option"
        else:
            sub = await client.create_subscription(
                request_id=f"selfcheck-sub-{uuid4()}", plan_id=cheapest, custom_id="selfcheck",
                return_url=f"{site}/billing?selfcheck=1", cancel_url=f"{site}/billing?selfcheck=0",
                brand_name="Talk-Lee",
            )
            sub_id = sub.get("id")
            if not isinstance(sub_id, str):
                problem = "no subscription id"
            elif sub.get("status") != "APPROVAL_PENDING":
                problem = "unexpected subscription status"
            elif sub.get("plan_id") != cheapest:
                problem = "plan id differs"
            elif subscription_approval_url(sub, env=env) is None:
                problem = "no approval link"
            else:
                problem = None
    except PayPalError as error:
        problem = str(error)
    good &= _line("subscription", problem)

    try:
        if not isinstance(sub_id, str):
            problem = "no subscription to read"
        else:
            read = await client.get_subscription(sub_id)
            problem = None if read.get("id") == sub_id and read.get("plan_id") == cheapest else "subscription differs"
    except PayPalError as error:
        problem = str(error)
    good &= _line("subscription-read", problem)

    if isinstance(sub_id, str):
        now = datetime.now(timezone.utc)
        try:
            await client.list_subscription_transactions(
                sub_id, start_time=now - timedelta(days=95), end_time=now + timedelta(days=1)
            )
            print("self-check transactions: ok")
        except PayPalError:
            print("self-check transactions: not available for an unapproved subscription (informational)")
    else:
        print("self-check transactions: not available for an unapproved subscription (informational)")

    try:
        hooks = [h for h in await client.list_webhooks() if h.get("url") == args.webhook_url]
        have = {
            e.get("name") for h in hooks for e in (h.get("event_types") or []) if isinstance(e, dict)
        }
        problem = None if hooks and ("*" in have or set(PAYPAL_WEBHOOK_EVENTS) <= have) else "webhook missing or incomplete"
    except PayPalError as error:
        problem = str(error)
    good &= _line("webhook", problem)
    return good


async def run(args, *, client, store) -> int:
    from app.domain.services import paypal_mode
    from app.domain.services.paypal_client import SUPPORTED_CURRENCIES, PayPalError, money_value

    problem = _preflight(args)
    if problem:
        print(problem)
        return 1
    apply = bool(args.apply)
    if args.self_check and not apply:
        print("self-check needs --apply")
        return 1
    if args.enable == "on" and not args.self_check:
        # Opening to customers needs the proof that the real PayPal answers as expected.
        print("--enable on needs --self-check; nothing changed")
        return 1
    env = paypal_mode.paypal_env()
    mode = paypal_mode.paypal_provider_mode()
    print(f"PayPal environment: {env}{'' if apply else ' (dry run)'}")

    failed = 0
    plan_ids: dict[str, str] = {}
    try:
        options = await store.price_options(mode)
        if not options:
            print("no approved price options")
            return 1
        rows = await store.plan_rows()
        product_id: str | None = None
        product_known = False
        for option in options:
            oid = str(option["id"])
            interval = option["interval"]
            amount_minor = option["amount_minor"]
            currency = str(option["currency"]).upper()
            plan_name = option["plan_name"]
            label = f"{plan_name} {interval}"
            if currency not in SUPPORTED_CURRENCIES or option.get("currency_exponent") != 2:
                print("skipped: currency not supported by PayPal here")
                failed += 1
                continue
            value = money_value(amount_minor, currency)
            row = rows.get(oid)
            if (
                row is not None
                and row.get("provider_mode") == mode
                and row.get("interval") == interval
                and row.get("amount_minor") == amount_minor
                and str(row.get("currency")).upper() == currency
            ):
                existing = await client.get_plan(row["paypal_plan_id"])
                if plan_matches(
                    existing, product_id=row["paypal_product_id"], interval=interval,
                    value=value, currency=currency,
                ):
                    print(f"ok: {label}")
                    plan_ids[oid] = row["paypal_plan_id"]
                    continue
            if not apply:
                print(f"would create PayPal plan: {label} {value} {currency}")
                continue
            if not product_known:
                product_id = await _find_product(client)
                if product_id is None:
                    created = await client.create_product(
                        request_id=f"talklee-plans-product-{mode}", name=PRODUCT_NAME,
                        description="Talk-Lee AI voice agent plans",
                    )
                    product_id = created.get("id")
                if not isinstance(product_id, str) or not product_id:
                    print("PayPal did not return a product id; nothing saved")
                    return 1
                product_known = True
            name = f"Talk-Lee {plan_name} ({'monthly' if interval == 'month' else 'yearly'})"
            plan = await _find_plan(
                client, product_id=product_id, name=name, interval=interval, value=value, currency=currency
            )
            if plan is None:
                plan = await client.create_plan(
                    request_id=f"talklee-plan-{oid}-{amount_minor}-{currency}-{interval}",
                    product_id=product_id, name=name,
                    description=f"{plan_name} plan, billed every {interval}",
                    interval=interval, currency=currency, value=value,
                )
                if not plan_matches(
                    plan, product_id=product_id, interval=interval, value=value, currency=currency
                ):
                    print(f"PayPal answered with different terms for {label}; nothing saved")
                    failed += 1
                    continue
            plan_id = plan.get("id")
            if not isinstance(plan_id, str) or not plan_id:
                print(f"PayPal did not return a plan id for {label}; nothing saved")
                failed += 1
                continue
            await store.save(
                price_option_id=option["id"], provider_mode=mode, paypal_product_id=product_id,
                paypal_plan_id=plan_id, interval=interval, amount_minor=amount_minor,
                currency=currency.lower(),
            )
            plan_ids[oid] = plan_id
            print(f"created: {label}")

        if args.self_check:
            if not await _self_check(args, client, env, mode, options, plan_ids):
                failed += 1
    except PayPalError as error:
        print(str(error))
        return 1

    if args.enable:
        value = args.enable
        if failed and value != "off":
            print("not enabling PayPal plans")
            return 1
        if apply:
            for change in upsert_env(args.env_file, {"PAYPAL_PLANS_ENABLED": value}):
                print(change)
        else:
            print(f"would set PAYPAL_PLANS_ENABLED={value}")
    print("done" if apply else "dry run complete; nothing changed")
    return 0 if not failed else 1


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--webhook-url")
    parser.add_argument("--site-url")
    parser.add_argument("--enable", choices=["off", "team", "on"])
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    problem = _preflight(args)
    if problem:
        print(problem)
        return 1
    from app.domain.services.paypal_client import get_paypal_client

    client = get_paypal_client()

    async def _go() -> int:
        try:
            return await run(args, client=client, store=PlanStore())
        finally:
            await client.aclose()

    return asyncio.run(_go())


if __name__ == "__main__":
    sys.exit(main())
