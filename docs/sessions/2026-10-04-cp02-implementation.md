# CP02 — authoritative purchase selection and activation

Status: local implementation and scoped verification complete at `6ca82160e7f1ffc62229f0b5bd09c684db255936` on `codex/production-ready-20261004`, starting from `a56af2fc`. CP02 remains `in_progress` because release and external acceptance are incomplete. Nothing was pushed or deployed. CP01 acceptance remains open. The feature freeze remains active.

## Confirmed causes

- The annual selector calculated `monthly * 12 * 0.83`, advertised a saving, and sent the same `plan_id` as monthly checkout. The backend used one legacy Stripe price. This proved a display/request mismatch; no actual customer charge or live price interval was inspected.
- Checkout created customers before validating the offer, accepted return destinations from the client/Origin, and had no durable purchase identity. A lost response or retry could start another purchase.
- Subscription activation trusted checkout metadata without binding the payment, customer, provider price and tenant to a stored purchase. The SDK's current subscription period shape also differed from the legacy handler.
- The local Next API had an independent Stripe webhook that acknowledged events without applying subscription state. It could claim success while the actual billing service had done nothing.
- Missing configuration could silently become mock billing, and production admission did not reject explicit mock mode or a missing webhook signing secret.

## Bounded changes

Migration `0053_billing_price_options` adds approved price choices and durable checkout attempts. Paid prices are not guessed or backfilled from legacy amounts. Only explicit zero-price legacy plans without a Stripe price receive a free option. Existing plans, subscriptions, balances and customer IDs are not rewritten. Both tables use forced RLS; only platform context can configure the catalog, and checkout attempts are tenant scoped. A database uniqueness rule allows one outstanding checkout per tenant. Purchase records are retained on downgrade.

Both catalog routes use one service. Available paid options must match a locally approved amount, currency, minor-unit convention, monthly/yearly interval, product, test/live mode and active fixed recurring Stripe price. Missing or invalid data disables the offer. The public response omits provider IDs. No new paid offer, discount or currency is configured by this change.

Checkout accepts only a UUID request reference and a UUID price-option reference. It records frozen purchase terms and return URLs before a provider write, uses stable Stripe idempotency keys, and retains uncertainty after transport failure. Old unknown requests are fenced before Stripe's idempotency retention can be mistaken for a permanent receipt. A current paid subscription must be managed through the verified billing portal rather than creating another subscription. Free activation is transactional and requires no Stripe customer. New subscription checkout explicitly accepts cards only; other payment methods remain unavailable until CP03 validates their asynchronous completion and recovery paths.

Signed paid checkout processing binds the session, customer, tenant, subscription and price to the saved attempt before changing access. Early subscription events leave the existing plan and allowance intact. The purchased price is saved with the subscription; retiring a price does not rewrite that receipt. Payment interval does not multiply included minutes: existing calendar-month usage, signed top-up/refund ledger and the unlimited sentinel remain in effect. The subscription endpoint uses the same quota calculation as calling admission.

The UI renders server terms, exposes yearly selection only when an approved available annual option exists, and uses named native interval controls. It saves a frozen request in tenant/user-scoped session storage before sending checkout, preserves it across retries and auth refresh, and treats success/cancel return parameters as informational. Unknown outcomes cannot silently start a replacement purchase. Existing paid accounts use the portal. The unused local Next billing path now returns 503 before generic idempotency handling; its acknowledge-only webhook is removed. Signup navigation is unchanged.

Effective mode is shared by catalog, checkout, config and the production guard. Intentional disabled deployments remain supported. Active production billing rejects mock mode, unsupported keys, missing SDK and missing webhook signing secret.

## Verification

Tests use synthetic identities and provider responses only. No Stripe account, customer, price, subscription or charge has been created or modified.

| Check | Observed result | Evidence |
|---|---|---|
| Scoped backend financial, mode/startup, RLS and route-auth regression suite | 1,464 passed, one pre-existing public-route inventory skip; eight deprecation warnings | [Backend focused run](artifacts/cp02/backend-focused.txt) |
| Latest checkout contract and HTTP envelope after the final card-method restriction | 43 passed; five framework deprecation warnings | [Final checkout run](artifacts/cp02/backend-checkout-final.txt) |
| Fresh PostgreSQL bootstrap through 0053, schema/catalog and checkout service integration | 34 passed on a disposable PostgreSQL 16.1 database, including actual non-bypass application roles | [PostgreSQL evidence](artifacts/cp02/backend-postgres.txt) |
| Frontend purchase/return/error/keyboard/shared-auth-client behavior | 49 passed | [Frontend tests](artifacts/cp02/frontend-focused.txt) |
| Frontend changed-file lint and full TypeScript | Passed | [Lint](artifacts/cp02/frontend-lint.txt), [TypeScript](artifacts/cp02/frontend-typecheck.txt) |
| Changed Python files, Ruff F/E9 | Passed | [Backend lint](artifacts/cp02/backend-lint.txt) |
| Frontend production build | Passed on installed/locked Next 16.3.8; 70 static pages generated; existing custom Cache-Control warning remains | [Build output](artifacts/cp02/frontend-build.txt) |

Independent reviews covered catalog/migration/RLS, frontend recovery, and backend purchase binding/modes. Review and regression testing caught and fixed an early-event access change, same-free-plan counter reset, nested error-envelope recovery fields, an implicit mock fallback, an error-code-only client reset and the delayed-payment-method mismatch. The observed free-plan defect reset the stored usage counter; it did not demonstrate a bypass of the calling quota, which derives usage from calls.

This is scoped candidate verification, not a repeat of the entire 10,000-plus backend suite or a signed provider/browser acceptance run. PostgreSQL tests use synthetic SDK objects; the offline transport test exercises the installed Stripe SDK encoder. The last card-only parameter change was covered by the final 43 checkout/HTTP tests after the broader backend and database batches. The initial frontend build was stopped when review changed the candidate; only the subsequent final build can count. No deployed application or provider account was tested.

The code commit contains only the bounded source, tests and CI changes. The original dirty checkout's application files were not overwritten. The separate evidence checkpoint records commands and artifact digests in [verification.json](artifacts/cp02/verification.json).

## Required before release or package closure

1. Finance/billing owner must provide and verify approved monthly and annual provider price mappings for the designated account. No approved mappings are available in local configuration. Annual containment is not annual completion.
2. Complete CP03 durable webhook recovery and ordering. The existing ordinary-event claim-before-handler failure remains a release blocker; this package does not claim to repair it. CP04 still owns invoice truth, and CP06 owns cancellation and billing communication acceptance.
3. Reconcile every pre-existing open checkout before cutover. A legacy in-flight session without the new saved purchase identity is fenced for explicit reconciliation; existing stored subscriptions keep their identities/access. Do not deploy across outstanding legacy purchases without this inventory.
4. Confirm canonical frontend origin/CORS, explicit FastAPI API base, and signed webhook routing. The old local Next acknowledge-only endpoint must not remain the provider destination. Confirm the portal is configured for supported management actions; arbitrary portal price changes require their own reconciliation and are not claimed as verified by CP02.
5. Run the authorized designated-account monthly/annual lifecycle, renewal/failure and signed webhook acceptance, browser/keyboard/mobile return journeys, invoice checks and billing-owner approval of the existing allowance/reset policy. Synthetic provider tests and a build do not prove these external outcomes.
6. Unknown purchases older than the safe retry window need operator/provider reconciliation; they are intentionally not discarded or retried under a fresh identity. Operational recovery is part of CP03.

## Provider references

The implementation checks fixed recurring [Stripe Price fields](https://docs.stripe.com/api/prices/object), [Checkout Session parameters](https://docs.stripe.com/api/checkout/sessions/create), [currency minor-unit rules](https://docs.stripe.com/currencies), and [idempotency retention](https://docs.stripe.com/api/idempotent_requests). It supports the [subscription item period change](https://docs.stripe.com/changelog/basil/2025-03-31/deprecate-subscription-current-period-start-and-end). These references explain provider contracts; they are not evidence of the deployed account's configuration or successful payment processing.
