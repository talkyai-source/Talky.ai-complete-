# CP03 implementation and verification — 4 October 2026

The recoverable billing webhook implementation is complete locally. CP03 remains **in progress** until designated-provider, deployment and operational acceptance are evidenced. This is the next bounded package in the saved production-readiness plan, covering existing subscriptions, invoices and minute top-ups/refunds.

Implementation candidate: `9eb2ac8d49442619dbdd26235c67de264a2e8e79` on `codex/production-ready-20261004`, based on `10e45b73ad316afd67df379854156b90b840a227`. Changes were made in the isolated `tmp/production-ready-20261004` worktree. The original dirty application's files were preserved. No push, deployment, real charge, provider-account modification or email was performed.

## Root causes and resulting behavior

| Failure found | Repair |
|---|---|
| The old event claim committed before the subscription/invoice handler. A failed handler's retry was acknowledged as a duplicate. Receipt-store failure deliberately proceeded without durable protection. | Save the verified event first. Apply business writes, notification intents and completion in one PostgreSQL transaction. Only a completed event returns `duplicate`; busy, transient and review cases return a retryable 503. Storage failure prevents business processing. |
| CSRF middleware rejected genuine server-to-server Stripe requests with no browser Origin, before signature verification. | Exempt only the exact webhook POST path. Verify the original request bytes using the real Stripe SDK signature verifier. Keep adjacent billing writes protected; reject oversized bodies before processing. |
| Event snapshots could overwrite newer subscription state; an old cancellation or failed invoice could affect a replacement subscription. Current invoice subscription references also use a newer `parent.subscription_details` shape. | Serialize by subscription before retrieving authoritative provider state. Verify persisted tenant/customer/purchase ownership, support current and retained legacy invoice shapes, and update current access only for the subscription actually bound to it. Timestamps do not determine ordering. |
| A lost checkout response or an invoice arriving first could leave a paid purchase unactivated. A terminal first-payment failure could keep the single pending-purchase slot occupied. | A current paid invoice can complete the exact saved purchase after validating its frozen price, line items, currency, customer, mode and subscription. Later checkout delivery binds its session without resetting usage. A confirmed terminal initial subscription fails only its pending attempt and preserves existing access/allowance. |
| A distinct late success event could re-credit a refunded order. The refund lookup could choose another legacy order sharing the same payment ID. A finite refund balance of zero became the existing unlimited sentinel. | Verify current Session, PaymentIntent, Charge and Dispute evidence. Lock the payment before authoritative reads, preserve immutable ledger uniqueness, bind reversals to the verified session/order and reject contradictory history. Preserve genuine unlimited accounts; send finite-zero conflicts to explicit review. |
| Missing PaymentIntent IDs on earlier snapshots caused false permanent failures. Currency case differed between historical ledger rows and Stripe. | Discover the current payment through Session/Charge, acquire its lock and re-read before mutation. Compare equivalent currency codes consistently while rejecting actual currency differences. |
| Receipt sending happened after money committed, with no durable delivery record. Adapter errors were treated as sent, and a payment retry could not reliably recover the gap. | Commit a separate notification intent with the billing transaction. Reuse the reminder worker process for delivery. Commit a sending fence before external I/O; retain accepted, known-unsent, missing-recipient and unknown outcomes separately. Bound transport calls. Payment retries cannot resend existing intents. |
| Replaying old financial claims or already-recorded money could resend an email whose previous outcome was unknowable. An unsent failed-payment notice could arrive after payment succeeded. | Retain an explicit legacy-claim flag. New intents for historical uncertain delivery remain `unknown`, not pending. Existing durable intents retain their state. A paid invoice supersedes only failure notices that have not begun sending. |

Partial refunds retain exact provider monetary evidence but require an approved minute-allocation policy. Pre-credit reversals and contradictory ledger/order state remain explicit review records. The implementation does not invent rounding, silently report those cases as successful, or delete history to make a retry work.

## Structure and recovery

`billing_service.py` now verifies and dispatches webhooks. The old claim/delete path and inline financial-email handlers were removed. Separate small modules own receipt transactions, current subscription/invoice state, verified top-up events, notification delivery and operator recovery. No new queue service or lease-renewal mechanism was introduced: event and payment/subscription ownership use transaction-scoped PostgreSQL locks.

Migration `0054_billing_webhook_receipts` creates the previously manual-only receipt table on fresh installs, adds completion/recovery evidence and preserves existing claims as `legacy_unverified`. The notification and review tables are platform-only under forced row-level security. No legacy success backfill is performed. Destructive downgrade is refused, and the protected-table regression inventory was updated.

The recovery CLI provides bounded inspection, same-identity retries, explicit legacy/review authorization and known-unsent notification requeueing. Authorization records the operator, reason and restricted provider/local observation report before making one event retryable. It does not create money effects or declare success. Unknown emails cannot be requeued through the known-unsent command.

Deployment, state meanings, exact commands and rollback requirements are in [the billing recovery procedure](../production-readiness/billing-webhook-recovery.md).

## Verification

All financial/provider objects were synthetic. Database checks used PostgreSQL 16.1, production JSON codecs, real transactions and roles with neither SUPERUSER nor BYPASSRLS. The full-stack HTTP cases used the actual middleware, router, Stripe verifier, dispatcher, receipt processor and financial SQL; only external Stripe reads were replaced.

| Check | Result | Evidence |
|---|---|---|
| Broad financial, subscription/status, quota, startup, RLS, endpoint-auth, CSRF and reminder regressions | 1,571 passed, one pre-existing public-route inventory skip; eight existing framework/deprecation warnings | [Broad backend run](artifacts/cp03/backend-focused.txt) |
| Latest financial/checkout/state/top-up/signature contracts after the final legacy-delivery guard | 147 passed | [Final focused run](artifacts/cp03/backend-financial-final.txt) |
| Fresh schema bootstrap and complete Alembic chain through final 0054 | Passed | [Fresh migration proof](artifacts/cp03/fresh-bootstrap.txt) |
| Combined actual PostgreSQL financial, receipt, recovery, migration/RLS and signed HTTP tests | 116 passed on fresh `cp03_final_test`; no skips | [Final database run](artifacts/cp03/backend-postgres-final.txt) |
| Scoped SMTP/SES transport options, receipt identity and worker heartbeat | 21 passed, including billing-only timeout/retry configuration and preserved unrelated adapter policies | [Transport follow-up](artifacts/cp03/notification-transport-final.txt) |
| Changed Python files, Ruff F/E9, and diff whitespace check | Passed | [Lint output](artifacts/cp03/backend-lint.txt) |

The database tests cover pre-claim storage failure, business rollback, transaction cancellation/restart, concurrent delivery, lost HTTP response, duplicate and distinct success events, replacement/cancelled subscriptions, wrong ownership, first-paid-invoice recovery, full/partial/pre-credit reversals, quota preservation, notification uncertainty and operator authorization. Four combined signed HTTP/database cases additionally prove real financial application, duplicate acknowledgement, rollback followed by same-event recovery, wrong-customer refusal and safe legacy recovery.

Independent implementation/review duties were split across root, audio, LLM and realtime agents. Reviews caught the nullable dispute reference, ambiguous refund order lookup, terminal purchase slot, obsolete failure notice and historical delivery uncertainties before the final checkpoint.

The initial broad run exposed eight top-up currency-comparison failures and the new protected tables missing from the schema inventory guard; both were corrected. Parallel agent database runs also produced PostgreSQL test-fixture permission-catalog conflicts. A later isolated run found a pending synthetic notification left by that failed cleanup, causing a drain-count assertion against a different test's record. The final acceptance run uses a freshly bootstrapped database and serial tests; prior synthetic evidence and immutable ledgers were retained. Intermediate failures are recorded separately and are not counted as acceptance proof.

The broad run preceded the last historical-email consistency changes; the final focused and database batches cover those changes. The final adapter-only follow-up verifies bounded SMTP/SES settings apply only to billing; unrelated adapter policies are preserved. The complete backend suite, frontend/browser acceptance and deployed services were not retested by this package. CI now includes the new integration modules, but remote CI was not run.

## Remaining release and closure requirements

1. Verify designated Stripe account/mode, signing secret, endpoint routing and actual current API objects. Run signed account-originated checkout, renewal/failure, cancellation, duplicate/reordered delivery, timeout/restart and refund acceptance against the deployed candidate.
2. Inventory and reconcile legacy claims/open purchases before cutover. A saved claim proves no historical success; an old email's absence from current delivery records proves no historical non-delivery. Provider history that can no longer be retrieved stays unresolved until external evidence establishes its treatment.
3. Finance must approve partial-refund minute treatment and any inconsistent historical balances. Finite-zero and pre-credit reversal cases are explicit unresolved outcomes, not completed refunds.
4. Confirm the actual email provider/SDK/configuration, acceptance evidence, reminder-worker operation and safe recipient. The existing owner-recipient gap remains CP06; broader delivery reconciliation remains CP07. Local SendGrid transport validation was unavailable because its optional SDK was not installed.
5. Record migration backup, compatible rollback, recovery ownership and receipt-payload retention, then perform deployed application-role and operational acceptance. CP02 catalog/provider acceptance and CP04 invoice truth remain dependencies of a sellable billing journey.

The disposable PostgreSQL server was stopped after verification. No application server or task test process was left running. No package or full-plan closure is claimed from local tests alone. The existing feature freeze remains active.

## Provider contracts

Stripe documents [unordered webhook delivery and retries](https://docs.stripe.com/webhooks#event-delivery-behaviors), [undelivered-event recovery](https://docs.stripe.com/webhooks/process-undelivered-events), [Charge refund fields](https://docs.stripe.com/api/charges/object) and [subscription/invoice webhook handling](https://docs.stripe.com/billing/subscriptions/webhooks). These references guide the contracts; they do not prove this deployment's account configuration or real payment/email outcomes.
