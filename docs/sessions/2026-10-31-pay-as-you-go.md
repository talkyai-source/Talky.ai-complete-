# Pay as you go — 31 October 2026

## Owner decision

"make the pay as you go available too please". Answers to the design questions:

| Question | Owner answer | Built as |
|---|---|---|
| How it works | Prepaid credit | Customers buy minute bundles up front; calls stop at a zero balance; minutes carry over until used |
| Price per minute | "we'll decide, keep it dynamic" | Prices live in `topup_packages`; platform admins change them through the Admin API, no release needed. Starting bundles equal today's top-up rate ($0.40/min) until the owner sets one |
| Limits | "however it is used we'll bill accordingly, that is the most premium plan" | Business capabilities (10 concurrent calls, 10 campaigns, CRM connectors, campaign wizard); no smaller-plan restrictions |
| Screens | Backend + minimal screens | Billing top-up card switches to "Pay as you go" for accounts without a plan; pricing page gets a pay-as-you-go card reading live prices |

Recorded in `docs/production-readiness/execution-tracker.json` (`scope_policy`).

## How it works

- **Who buys what** (`app/domain/services/payg.py`, `purchase_audience`):
  a subscriber in good standing (`active`/`trialing` with a Stripe subscription)
  buys `subscriber` top-ups; any other account that the call guard would let
  place calls buys `payg` bundles; an account the guard would refuse
  (suspended, archived/cancelled, past due, unpaid, incomplete-expired) buys
  nothing, so a purchase can never reopen a closed account.
- **First paid bundle** moves an account with no plan and no subscription to
  plan `payg` in the same transaction as the credit
  (`TopupService.credit_paid_order`). Its minute allocation stays 0.
- **Minutes**: the existing ledger. Prepaid minutes are used once and carry
  over (`topup_usage`), exactly like subscriber top-ups. Calls are admitted by
  the same `compute_minutes_status` the dialer already uses.
- **The trap that was fixed**: a plan with zero included minutes was the
  "unlimited" sentinel in four places (minutes balance, monthly usage
  recorder, order creation, credit warning). Pay as you go also has zero
  included minutes, so it would have been unlimited free calling, and order
  creation would have refused every new account. One rule now decides it,
  `minutes_quota.minutes_unlimited`: zero is unlimited only on a
  `subscription` plan, never on a `prepaid` one.
- **Subscribing later** replaces `payg` with the bought plan; unused prepaid
  minutes stay in the ledger and keep counting.

## Database (Alembic `0083_pay_as_you_go`, on `0082_support_system`)

- `plans.billing_model` (`subscription` | `prepaid`, default `subscription`).
- Plan `payg` "Pay as you go": price 0, 0 included minutes, `prepaid`,
  Business capabilities.
- `topup_packages.audience` (`subscriber` | `payg`, default `subscriber`).
- Bundles `payg_125` ($50), `payg_175` ($70), `payg_250` ($100), USD.
- Downgrade refuses while any account is on `payg` or any pay-as-you-go order exists.

## API

| Route | Who | What |
|---|---|---|
| `GET /api/v1/plans/payg` | public | the prepaid plan and its active bundles with price per minute (pricing page) |
| `GET /api/v1/billing/topups/packages` | signed in, billing:read | bundles for this account's audience; none for an account that cannot buy |
| `POST /api/v1/billing/topups/checkout` | billing:update | refuses (409) an account that cannot buy; a bundle of the other audience is refused (400) |
| `GET /api/v1/admin/billing/packages` | platform admin | every bundle, both audiences, active or not |
| `POST /api/v1/admin/billing/packages` | platform admin | add a bundle (`code`, `name`, `minutes`, `price_cents`, `currency`, `audience`, `sort_order`, `is_active`) |
| `PATCH /api/v1/admin/billing/packages/{code}` | platform admin | change `name`, `minutes`, `price_cents`, `is_active`, `sort_order`; audience and currency are fixed |

Admin changes take the audit chain lock, re-check platform-admin authority
inside the transaction, and write an audit row with the before and after
state (`billing.package.create` / `billing.package.update`). Orders snapshot
price and minutes, so a change applies to new orders only. Bundles are never
deleted; `is_active=false` takes one off sale.

Example — set pay-as-you-go to $0.50/min for the 125-minute bundle:

```
PATCH /api/v1/admin/billing/packages/payg_125
{"price_cents": 6250}
```

## Switches that still apply

Pay-as-you-go purchases use the existing top-up checkout, so the existing
switches govern them:

- `BILLING_TOPUPS_LIVE_ENABLED=true` must be set for any real (test or live)
  Stripe top-up or pay-as-you-go sale; otherwise checkout answers 503.
- While Stripe runs in test mode, checkout is limited to the team
  (`_require_checkout_permitted_in_test_mode`), as for every purchase.

## Screens (Talk-Leee)

- `src/components/billing/topup-card.tsx`: when the bundles the server offers
  are pay-as-you-go, the card is titled "Pay as you go" with "No monthly fee"
  wording instead of "Add minutes to your paid plan".
- `src/lib/topup-api.ts`: bundle schema carries `audience`.
- `src/app/pricing/pay-as-you-go.tsx` + `page.tsx`: a "Pay as you go — No
  monthly fee" card that loads live bundles from `/plans/payg` in the browser
  (falls back to "prices are shown in Billing" if unavailable); the sentence
  "AI calling requires a paid Basic or Business plan" now includes prepaid
  pay-as-you-go minutes.

## Tests

| Test | Result |
|---|---|
| `tests/unit/test_payg_rules.py` (purchase rule, unlimited rule) | 24 passed |
| `tests/integration/test_payg_pg.py` (restricted non-bypass role: seed, audience, credit moves plan, exact minutes, calls use minutes, month close records usage, subscriber top-up keeps plan, unlimited plan stays unlimited, reprice affects new orders only + audited, add/disable bundle, non-admin refused, public prices) | 11 passed |
| same, with the old "zero means unlimited" rule put back | 2 failed (proves the tests catch it) |
| `tests/integration/test_payg_migration.py` (down/up round trip, refusal while used) | passed |
| Billing/top-up/minutes/plans/guard unit + security files (54 files) | 2518 passed, 2 skipped |
| CI's migrated-database integration list run locally (43 files) | 520 passed; 3 inbound tests refuse any database but CI's exact target |
| Talk-Leee `npm run typecheck`, `npm run lint`, `npm test` | clean, clean, 1357 tests / 1355 pass / 0 fail / 2 skipped |

Existing tests updated because the owner's rule changed (not loosened):
`test_paid_only_billing.py` (no subscription now buys pay as you go; closed
accounts still refused before any order), test doubles taught the new
queries, migration-head pins moved to `0083`, the credit-path guard now checks
that a credit sets no tenant column but `plan_id`.

## Not done / not verified

- No real Stripe pay-as-you-go purchase has been made (Stripe is in test mode
  and checkout is team-only there).
- `BILLING_TOPUPS_LIVE_ENABLED` on production was not read (the agent may not
  read the production `.env`).
- The Admin panel has no screen for prices yet; changes go through the Admin
  API above.
- Accounts whose subscription ended carry `subscription_status=cancelled`,
  the same value an admin archive writes, so they cannot self-serve pay as
  you go; an admin "resume" makes them eligible.
- The pricing-page card loads prices in the browser, so the prices are not
  in the server-rendered HTML.
