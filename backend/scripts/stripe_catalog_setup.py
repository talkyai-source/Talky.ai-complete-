"""Create or verify the Stripe side of paid-only billing, then switch it on.

Owner decision 2026-10-27: Basic $99/month and Business $299/month (the public
pricing page), each also yearly at 17% off 12 months ($986 and $2,978), plus
the team-only $1/month payment check (migration 0073).

Two steps, both idempotent. Neither ever prints a secret.

1. ``--configure-env`` reads ``NAME=VALUE`` lines from stdin (so a key never
   reaches argv, ``ps`` or shell history) and upserts them into ``--env-file``.
   ``--remove NAME`` deletes a line, e.g. ``STRIPE_BILLING_DISABLED``.

2. ``--apply`` (a dry run without it) uses ``STRIPE_SECRET_KEY`` from the
   process environment, so run it with the service's EnvironmentFile:

   * one product per plan and one fixed recurring price per interval, found
     again by lookup key ``talky_<plan>_<interval>``;
   * an active, verified ``plan_price_options`` row per price for this mode;
   * the webhook endpoint for ``--webhook-url`` with every event the billing
     code handles. Stripe shows a signing secret only on creation, so a new
     endpoint's secret goes straight into ``--env-file``;
   * a customer-portal configuration offering card updates, invoices and
     cancel-at-period-end, never a plan change (the purchase terms are frozen).

Restart the services afterwards so they read the new environment. Switching
to live keys later is step 1 with the live key, removing
``STRIPE_TEST_MODE_IN_PRODUCTION``, ``STRIPE_WEBHOOK_SECRET`` and
``STRIPE_PORTAL_CONFIGURATION_ID`` (both name test-mode objects: a kept test
secret would fail every live signature), then step 2 again. In live mode step 2
lists accounts still holding a plan bought with a TEST card: no money was
taken for those, so end them (set subscription_status to 'cancelled') before
opening checkout to customers.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CURRENCY = "usd"
# plan_id -> (product name, {interval: amount in cents})
CATALOG = {
    "basic": ("Talk-Lee AI Basic", {"month": 9900, "year": 98600}),
    "business": ("Talk-Lee AI Business", {"month": 29900, "year": 297800}),
    # Team-only real $1 charge that checks the money flow (billing_mode.TEAM_ONLY_PLAN_IDS).
    "payment_test": ("Talk-Lee AI payment test", {"month": 100}),
}
WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "checkout.session.expired",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
    "invoice.finalized",
    "invoice.updated",
    "invoice.voided",
    "invoice.marked_uncollectible",
    "credit_note.created",
    "credit_note.updated",
    "credit_note.voided",
    "charge.refunded",
    "charge.dispute.created",
    "charge.dispute.closed",
    "charge.refund.updated",
    "refund.created",
    "refund.updated",
    "refund.failed",
]


def lookup_key(plan_id: str, interval: str) -> str:
    return f"talky_{plan_id}_{interval}"


def upsert_env(path: Path, values: dict[str, str], remove: set[str] = frozenset()) -> list[str]:
    """Rewrite in place (owner and mode kept) after a same-mode backup copy."""
    for name, value in values.items():
        if not name.isidentifier() or "\n" in value or "\r" in value:
            raise ValueError(f"refusing malformed environment entry {name!r}")
    lines = path.read_text(encoding="utf-8").splitlines()
    backup = path.with_name(f"{path.name}.bak-stripe-{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
    shutil.copy2(path, backup)
    pending = dict(values)
    changed: list[str] = []
    out: list[str] = []
    for line in lines:
        name = line.split("=", 1)[0].strip().removeprefix("export ").strip() if "=" in line else None
        if name in remove:
            changed.append(f"removed {name}")
            continue
        if name in pending:
            out.append(f"{name}={pending.pop(name)}")
            changed.append(f"updated {name}")
            continue
        out.append(line)
    for name, value in pending.items():
        out.append(f"{name}={value}")
        changed.append(f"added {name}")
    with path.open("r+", encoding="utf-8") as handle:
        handle.write("\n".join(out) + "\n")
        handle.truncate()
    return changed


def configure_env(path: Path, remove: set[str]) -> None:
    values = {}
    for raw in sys.stdin.read().splitlines():
        if raw.strip():
            name, _, value = raw.partition("=")
            values[name.strip()] = value.strip()
    for change in upsert_env(path, values, remove):
        print(change)


def _get(obj, key, default=None):
    return obj.get(key, default) if hasattr(obj, "get") else getattr(obj, key, default)


def _product_id(price):
    product = _get(price, "product")
    return product if isinstance(product, str) else _get(product, "id")


def ensure_prices(client, mode: str, apply: bool) -> list[dict]:
    """Return one approved option per plan and interval, creating what is missing."""
    from app.domain.services.billing_catalog import validate_provider_price

    keys = [lookup_key(plan, interval) for plan, (_, prices) in CATALOG.items() for interval in prices]
    found = {
        _get(price, "lookup_key"): price
        for price in client.v1.prices.list({"lookup_keys": keys, "active": True, "limit": 100,
                                            "expand": ["data.product"]}).data
    }
    products = {
        _get(_get(product, "metadata") or {}, "talky_plan_id"): product
        for product in client.v1.products.list({"active": True, "limit": 100}).auto_paging_iter()
        if _get(_get(product, "metadata") or {}, "talky_plan_id")
    }
    options = []
    for plan_id, (name, prices) in CATALOG.items():
        product = products.get(plan_id)
        if product is None:
            print(f"{'creating' if apply else 'would create'} product for {plan_id}")
            if not apply:
                continue
            product = client.v1.products.create(
                {"name": name, "metadata": {"talky_plan_id": plan_id}},
                {"idempotency_key": f"talky-catalog-product-{plan_id}-{mode}"},
            )
        product_id = _get(product, "id")
        for interval, amount in prices.items():
            key = lookup_key(plan_id, interval)
            price = found.get(key)
            matches = (
                price is not None and _get(price, "unit_amount") == amount and _get(price, "currency") == CURRENCY
                and _get(_get(price, "recurring") or {}, "interval") == interval and _product_id(price) == product_id
            )
            if not matches:
                print(f"{'creating' if apply else 'would create'} {interval} price for {plan_id} ({amount} {CURRENCY})")
                if not apply:
                    continue
                price = client.v1.prices.create(
                    {"product": product_id, "currency": CURRENCY, "unit_amount": amount,
                     "recurring": {"interval": interval, "interval_count": 1, "usage_type": "licensed"},
                     "lookup_key": key, "transfer_lookup_key": True,
                     "metadata": {"talky_plan_id": plan_id, "interval": interval}},
                    {"idempotency_key": f"talky-catalog-price-{key}-{amount}-{mode}"},
                )
            option = {
                "plan_id": plan_id, "interval": interval, "interval_count": 1, "amount_minor": amount,
                "currency": CURRENCY, "currency_exponent": 2, "provider_mode": mode,
                "stripe_price_id": _get(price, "id"), "stripe_product_id": product_id,
            }
            validate_provider_price(option, price)
            options.append(option)
            print(f"verified {plan_id} {interval}: {amount / 100:.2f} {CURRENCY.upper()}")
    return options


async def save_options(options: list[dict], apply: bool) -> None:
    import asyncpg

    from app.core.config import get_settings

    if not options:
        return
    conn = await asyncpg.connect(get_settings().database_url.replace("+asyncpg", ""))
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.bypass_rls','true',true)")
            plans = {row["id"] for row in await conn.fetch("SELECT id FROM plans WHERE id = ANY($1::text[])", list(CATALOG))}
            missing = set(CATALOG) - plans
            if missing:
                raise SystemExit(f"plans missing (run the migrations first): {sorted(missing)}")
            for option in options:
                if not apply:
                    print(f"would save price option {option['plan_id']} {option['interval']}")
                    continue
                await conn.execute(
                    """INSERT INTO plan_price_options
                       (plan_id, stripe_price_id, stripe_product_id, kind, interval, interval_count,
                        amount_minor, currency, currency_exponent, provider_mode, active, verified_at)
                       VALUES ($1, $2, $3, 'stripe', $4, 1, $5, $6, 2, $7, TRUE, NOW())
                       ON CONFLICT (plan_id, interval, provider_mode) DO UPDATE SET
                         stripe_price_id = EXCLUDED.stripe_price_id,
                         stripe_product_id = EXCLUDED.stripe_product_id,
                         amount_minor = EXCLUDED.amount_minor, currency = EXCLUDED.currency,
                         active = TRUE, verified_at = NOW(), updated_at = NOW()""",
                    option["plan_id"], option["stripe_price_id"], option["stripe_product_id"],
                    option["interval"], option["amount_minor"], option["currency"], option["provider_mode"],
                )
                print(f"saved price option {option['plan_id']} {option['interval']}")
    finally:
        await conn.close()


async def report_test_mode_plans() -> None:
    """Accounts whose active plan was bought with a test card (no money)."""
    import asyncpg

    from app.core.config import get_settings

    conn = await asyncpg.connect(get_settings().database_url.replace("+asyncpg", ""))
    try:
        async with conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('app.bypass_rls','true',true)")
            rows = await conn.fetch(
                """SELECT t.id FROM tenants t JOIN subscriptions s
                     ON s.stripe_subscription_id = t.stripe_subscription_id AND s.tenant_id = t.id
                    WHERE s.metadata->'purchase'->>'stripe_price_id' IN
                          (SELECT stripe_price_id FROM plan_price_options WHERE provider_mode = 'test')"""
            )
    finally:
        await conn.close()
    if rows:
        print(f"WARNING: {len(rows)} account(s) hold a plan bought with a TEST card: "
              + ", ".join(str(r["id"])[:8] for r in rows) + ". End them before opening checkout.")
    else:
        print("no account holds a test-mode plan")


def ensure_webhook(client, url: str, env_file: Path, apply: bool) -> None:
    import stripe

    endpoints = [e for e in client.v1.webhook_endpoints.list({"limit": 100}).auto_paging_iter() if _get(e, "url") == url]
    have_secret = bool(os.getenv("STRIPE_WEBHOOK_SECRET", "").strip())
    if endpoints and have_secret:
        endpoint = endpoints[0]
        print(f"{'updating' if apply else 'would update'} webhook endpoint events ({len(WEBHOOK_EVENTS)})")
        if apply:
            client.v1.webhook_endpoints.update(_get(endpoint, "id"), {"enabled_events": WEBHOOK_EVENTS, "disabled": False})
        return
    if endpoints:
        # Its signing secret cannot be read back, so it can never be verified.
        print(f"{'replacing' if apply else 'would replace'} webhook endpoint whose secret is not configured")
        if apply:
            for endpoint in endpoints:
                client.v1.webhook_endpoints.delete(_get(endpoint, "id"))
    else:
        print(f"{'creating' if apply else 'would create'} webhook endpoint")
    if not apply:
        return
    endpoint = client.v1.webhook_endpoints.create({
        "url": url, "enabled_events": WEBHOOK_EVENTS, "api_version": stripe.api_version,
        "description": "Talk-Lee billing (subscriptions, invoices, top-ups)",
    })
    for change in upsert_env(env_file, {"STRIPE_WEBHOOK_SECRET": _get(endpoint, "secret")}):
        print(change)


def ensure_portal(client, return_url: str, env_file: Path, apply: bool) -> None:
    origin = return_url.split("/billing", 1)[0]
    params = {
        "business_profile": {"privacy_policy_url": f"{origin}/privacy", "terms_of_service_url": f"{origin}/terms"},
        "default_return_url": return_url,
        "features": {
            "customer_update": {"enabled": True, "allowed_updates": ["email", "address", "name"]},
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
            "subscription_cancel": {"enabled": True, "mode": "at_period_end", "proration_behavior": "none"},
            "subscription_update": {"enabled": False},
        },
    }
    existing = os.getenv("STRIPE_PORTAL_CONFIGURATION_ID", "").strip()
    if existing:
        print(f"{'updating' if apply else 'would update'} customer portal configuration")
        if apply:
            client.v1.billing_portal.configurations.update(existing, params)
        return
    print(f"{'creating' if apply else 'would create'} customer portal configuration")
    if apply:
        configuration = client.v1.billing_portal.configurations.create(params)
        for change in upsert_env(env_file, {"STRIPE_PORTAL_CONFIGURATION_ID": _get(configuration, "id")}):
            print(change)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--configure-env", action="store_true")
    parser.add_argument("--remove", action="append", default=[])
    parser.add_argument("--webhook-url")
    parser.add_argument("--return-url")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.configure_env:
        configure_env(args.env_file, set(args.remove))
        return

    import stripe

    from app.domain.services.billing_mode import get_billing_mode

    mode = get_billing_mode()
    if mode not in {"test", "live"}:
        raise SystemExit(f"billing mode is {mode!r}; set STRIPE_SECRET_KEY and unset STRIPE_BILLING_DISABLED first")
    if not args.webhook_url or not args.webhook_url.startswith("https://"):
        raise SystemExit("--webhook-url must be the public https URL of /api/v1/billing/webhooks")
    if not args.return_url or not args.return_url.startswith("https://"):
        raise SystemExit("--return-url must be the public https URL of /billing")
    print(f"billing mode: {mode}{'' if args.apply else ' (dry run)'}")
    client = stripe.StripeClient(os.environ["STRIPE_SECRET_KEY"])
    options = ensure_prices(client, mode, args.apply)
    asyncio.run(save_options(options, args.apply))
    ensure_webhook(client, args.webhook_url, args.env_file, args.apply)
    ensure_portal(client, args.return_url, args.env_file, args.apply)
    if mode == "live":
        asyncio.run(report_test_mode_plans())
    print("done" if args.apply else "dry run complete; nothing changed")


if __name__ == "__main__":
    main()
