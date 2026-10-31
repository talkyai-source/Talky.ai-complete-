# Billing webhook recovery (CP03)

This procedure covers existing subscription, invoice and minute top-up events. It does not authorize creating charges, changing prices, choosing a partial-refund minute policy, or resending an email whose outcome is unknown.

## Deployment boundary

1. Back up the database and record the currently deployed application/migration revisions. Inventory legacy webhook claims and outstanding checkout sessions before cutover.
2. Pause the old webhook handler with a retryable response. It must not keep writing claim-only rows during cutover. Apply Alembic through `0054_billing_webhook_receipts`, then deploy the compatible backend and reminder worker together.
3. Confirm the provider destination is the FastAPI `POST /api/v1/billing/webhooks` URL, with the correct account/mode and signing secret. An old Next.js acknowledgement-only destination is not compatible. The exact POST path has a CSRF exemption; Stripe raw-body signature verification remains mandatory in test/live mode.
4. Verify database access using the actual application role, receipt persistence, and the reminder worker's billing delivery loop. Notifications use the existing configured email adapter; no new worker service is required.
5. Run designated-account signed-event acceptance and inspect the resulting database, provider and delivery records. Local synthetic tests do not establish the deployed account's settings or email delivery.

Do not roll back to claim-means-completed code after processing begins. If the release fails, return a retryable non-success while deploying a compatible fix. Retain all receipt, ledger, notification and review rows. Migration 0054 intentionally refuses a destructive downgrade.

## What each state means

| Record | State | Meaning and next action |
|---|---|---|
| Event | `pending` | Verified evidence is saved; business completion is not established. It may be processing under a transaction lock. |
| Event | `failed` | The attempt did not complete. Database business effects rolled back, or a lost commit response must be resolved through the same event identity. Retry the saved event. |
| Event | `completed` | Business effects, notification intents and this completion record committed together. A redelivery returns `duplicate`. This does not claim email delivery. |
| Event | `legacy_unverified` | The old code claimed this identity. Historical completion is unknown. Inspect before authorizing recovery. The original `processed_at` is retained. |
| Event | `needs_review` | Conflicting ownership, provider state, refund policy or another explicit unresolved boundary prevents automatic application. Investigate; do not delete the receipt. |
| Notification | `pending` | Intent exists and a worker can claim it. |
| Notification | `failed_before_send` | Configuration/preflight prevented sending. Automatic checks are bounded; an operator can requeue a proved unsent attempt. |
| Notification | `sending` / `unknown` | A send may have occurred. Age or process restart does not authorize another send. Investigate provider acceptance first. |
| Notification | `accepted` | The transport reported acceptance, not delivery to an inbox. Fabricated SMTP/SendGrid IDs are not retained as provider evidence. |
| Notification | `recipient_missing` | A safe recipient could not be established. Resolve the existing recipient policy under CP06; do not guess another tenant's recipient. |
| Notification | `superseded` | An unsent failure notice became obsolete when the invoice was paid. It will not be sent. |

There is no expiring event lease. A transaction-scoped PostgreSQL lock prevents overlapping work and releases on rollback, disconnect or restart. Current provider reads occur after the subscription/payment lock is acquired. Provider event timestamps are not used as a total ordering rule.

## Read-only inspection

Run from `backend` using the intended database and matching Stripe account configuration:

```text
python -m scripts.reconcile_billing_webhooks list --limit 25
python -m scripts.reconcile_billing_webhooks inspect --event-id evt_REPLACE
```

`list` reports at most 100 unresolved events and 100 notification records. `inspect` retrieves the matching provider event and current object, then reports local subscription/access state, available checkout/invoice projection, ledger movements and notification states without printing customer payloads or email addresses. Current provider IDs/mode must agree with the saved event. Provider event retrieval may be unavailable for older history; retain the unresolved record and use provider/account evidence rather than manufacturing a replacement payload.

Before recovery, establish the account/mode, event/object ID, tenant/customer ownership, saved purchase/order, exact amount/currency, current subscription status, and existing positive/negative ledger entries. A metadata tenant ID alone is not sufficient ownership evidence. Preserve evidence of any mismatch and its resolution in the operator reason or linked incident record.

## Retry one established identity

For saved `pending` or `failed` events:

```text
python -m scripts.reconcile_billing_webhooks retry --event-id evt_REPLACE
```

This invokes current-state, idempotent database handlers only. It cannot create a charge or send email. Check the resulting event state, affected billing rows and ledger. A still-pending provider payment returns a retryable result; a financial ambiguity stays unresolved.

For a legacy or review event, inspect first, establish why an idempotent retry is appropriate, then record that decision explicitly:

```text
python -m scripts.reconcile_billing_webhooks authorize-retry --event-id evt_REPLACE --operator OPERATOR_ID --reason "Verified provider/local evidence and incident reference"
python -m scripts.reconcile_billing_webhooks retry --event-id evt_REPLACE
python -m scripts.reconcile_billing_webhooks inspect --event-id evt_REPLACE
```

Authorization re-fetches provider evidence and records the reason plus a restricted observation report. It changes only that event to `pending`; it neither marks it successful nor applies money. All binding and ledger guards still apply during retry. Partial refunds without an approved minute-allocation policy, pre-credit reversals, a finite allowance reaching the unlimited sentinel, and contradictory history remain review cases. The tool does not override those policies.

The retained `legacy_claim` flag also preserves notification uncertainty after financial replay authorization. A new intent for a legacy event, or an already-recorded financial effect without a delivery record, starts `unknown` with `legacy_delivery_unverified`. Financial recovery does not prove the old email was unsent. Existing durable intents keep their own state; replay cannot turn an accepted or uncertain email into a new pending send.

No batch legacy replay, receipt deletion, new event identity, direct ledger mutation, or timestamp-based success backfill is permitted by this procedure. Compare resulting state with the saved observation report and retain any remaining difference.

## Recover a notification without replaying billing

The reminder worker claims each intent durably before contacting the email adapter. Missing configuration is retried at most five times automatically, with five-minute spacing. A crash/timeout after the sending fence leaves `sending`/`unknown`; the worker never takes that as proof that nothing was sent.

After fixing configuration, requeue only a recorded pre-send failure:

```text
python -m scripts.reconcile_billing_webhooks retry-unsent-notification --notification-id UUID_REPLACE --operator OPERATOR_ID --reason "Configuration repaired; previous attempt was proved unsent"
```

This records the decision and queues the notification; it does not send immediately. Unknown, sending, accepted and missing-recipient records cannot use this command. Provider acceptance/delivery investigation and the canonical-recipient decision remain explicit operational requirements. A payment retry must never be used as an email-resend command.

## Release acceptance still required

- Exercise a designated Stripe account's signed checkout, invoice, subscription cancellation, delayed/failed payment, duplicate/redelivery and reversal events against the deployed endpoint.
- Test a deliberate failed handler and process restart, then reconcile the same event identity and financial state.
- Verify finite and unlimited minute policies and approve treatment of partial refunds; unresolved records are not successful refunds.
- Verify the actual email adapter's bounded transport, acceptance evidence, worker liveness and routing. Confirm delivery separately from acceptance.
- Assign finance/recovery ownership and retention for platform-only receipt payloads and review records. No retention policy is invented by this change.

Stripe documents unordered deliveries and automatic live retries for up to three days; track that deadline during an outage and reconcile retained events afterward. See [webhook delivery behavior](https://docs.stripe.com/webhooks#event-delivery-behaviors) and [undelivered-event recovery](https://docs.stripe.com/webhooks/process-undelivered-events). [Charge refund fields](https://docs.stripe.com/api/charges/object) establish monetary evidence; they do not define Talky.ai's minute-proration policy.
