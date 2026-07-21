# Stripe SDK Readiness — 2026-07-21

## What changed

Added `stripe>=12.0.0,<15.0.0` to `requirements.txt`.

### Why this version range

- **v12+**: Minimum that supports the current Stripe API version (2025-09-30.clover)
  and all billing APIs used: `checkout.Session`, `billing_portal.Session`,
  `Customer`, `Subscription`, `Webhook.construct_event`
- **<v15**: v15.0.0 dropped `dict` inheritance from `StripeObject`. The billing
  service uses `event.get("id")` (L346 of `billing_service.py`) which requires
  dict methods. Bracket notation (`event["type"]`) still works in v15, but
  `.get()` does not.

### Version installed locally

`stripe==14.3.0` — resolved by pip within the pinned range.

## Current state after this change

| Component | Status | Notes |
|-----------|--------|-------|
| `stripe` SDK in requirements | ✅ | `>=12.0.0,<15.0.0` |
| `STRIPE_AVAILABLE` flag | ✅ True | `billing_service.py` import succeeds |
| Prod gate: SDK check | ✅ | `STRIPE_SDK_MISSING` no longer fires |
| Prod gate: live key check | ✅ | Blocks `sk_test_` in prod |
| Prod gate: billing disabled | ✅ | `STRIPE_BILLING_DISABLED=1` bypass |
| Mock mode (no key set) | ✅ | Still works — all endpoints return mock data |
| Webhook endpoint | ✅ | `POST /billing/webhooks` — raw body via `await request.body()` |
| Webhook idempotency | ✅ | `processed_webhook_events` table dedup |

## What's still needed to go live

1. **Stripe live key** (`sk_live_...`) — set in `.env` on server
2. **Stripe webhook signing secret** — create webhook endpoint in Stripe Dashboard → capture secret → set `STRIPE_WEBHOOK_SECRET`
3. **Live Products/Prices** — create in Stripe Dashboard, update `plans` DB table with `stripe_price_id` and `stripe_product_id`
4. **Install on prod server** — `venv/bin/pip install stripe` (or pull + `pip install -r requirements.txt`)
5. **Test checkout** — one real checkout on cheapest plan → verify webhook → refund

## v15 upgrade path (future)

When upgrading past v15, audit all `.get()` calls on Stripe objects:
- `billing_service.py:346` — `event.get("id")` → change to `event["id"]` or `getattr(event, "id", None)`
- All other accesses use bracket notation or attribute access (already v15-safe)

## Audit of all Stripe API calls (all in `billing_service.py`)

| Line | Call | v14 Safe | v15 Safe |
|------|------|----------|----------|
| 51 | `stripe.api_key = ...` | ✅ | ✅ |
| 99 | `stripe.Customer.create(...)` | ✅ | ✅ |
| 176 | `stripe.checkout.Session.create(...)` | ✅ | ✅ |
| 234 | `stripe.billing_portal.Session.create(...)` | ✅ | ✅ |
| 303 | `stripe.Subscription.modify(...)` | ✅ | ✅ |
| 337 | `stripe.Webhook.construct_event(...)` | ✅ | ✅ |
| 340 | `stripe.error.SignatureVerificationError` | ✅ | ✅ |
| 344 | `event["type"]` | ✅ | ✅ |
| 345 | `event["data"]["object"]` | ✅ | ✅ |
| 346 | `event.get("id")` | ✅ | ❌ |
