# Billing record reconciliation — CP04

This procedure covers existing invoice and minute top-up records. It does not authorize a refund, change an allowance, introduce overage pricing, or prove that funds reached a customer's bank. Finance acceptance and a designated provider-account trace remain required before CP04 closes.

## What the customer screen means

| Record | Meaning and limits |
|---|---|
| Invoice subtotal, total, amount due, paid and remaining | Separate captured provider fields in integer minor units. They are not interchangeable. Historical details come from the recorded provider invoice, never the current plan catalog. |
| Taxes, discounts, credit notes and line items | Only captured source objects. `null` means unavailable; an empty collection means the source collection was captured empty. Partial capture is explicitly identified. Display totals are not reconstructed by adding rounded rows. |
| Currency and exponent | Both must be known before displaying a monetary amount. JPY, GBP and KWD do not have the same number of minor-unit decimal places. Unknown configuration is unavailable, never assumed USD/GBP. |
| Provider refund status | An observation of a particular refund reference at the displayed capture time. `pending`, `requires_action`, `succeeded`, `failed` and `canceled` remain distinct. Provider success is not confirmation of bank receipt. |
| Top-up accounting movement | The signed, immutable minute/money ledger movement linked to an order and provider event/payment. A negative movement is an accounting reversal, not a refund request or bank settlement receipt. |
| Usage and allowance | The existing calendar-month settled parent-call and finalized transfer-leg meter against the tenant's actual allowance. Top-ups affect that allowance. Zero allowance retains the existing unlimited meaning. Usage is not invoice line-item consumption. Daily whole-minute floors need not sum to the monthly aggregate floor; the API retains seconds. |
| Checkout return URL | A navigation result only. It does not prove payment, cancellation of payment, or credited minutes. The saved order and ledger provide those facts. |

No approved canonical overage price is configured by this package. An exceeded allowance can be shown, but no charge estimate is invented. The metering calendar, quota admission policy and partial-refund allocation policy are unchanged.

## Deployment and retained evidence

1. Back up billing tables and record the deployed application/database revision. Apply the reviewed Alembic chain through `0056_billing_refund_snapshots` before deploying the coupled API/UI contract.
2. `0055_invoice_snapshots` retains append-only invoice observations, broadens saved invoice amounts to BIGINT and distinguishes newly managed invoice receipt history from unknown legacy history. `0056_billing_refund_snapshots` retains append-only top-up refund observations. Composite foreign keys bind each observation to the correct tenant's invoice/order. Forced row-level security allows tenant reads and platform-only inserts.
3. New captures arrive through the existing signed webhook endpoint. Verify that the designated provider endpoint subscribes to the existing financial events plus `invoice.finalized`, `invoice.updated`, `invoice.voided`, `invoice.marked_uncollectible`, `credit_note.created`, `credit_note.updated`, `credit_note.voided`, `refund.created`, `refund.updated`, `refund.failed` and `charge.refund.updated`. Event configuration has not been changed by the local implementation.
4. Existing records are not backfilled from today's plan, tax settings or an assumed price. Old summaries remain available with explicit unavailable detail and verified provider-document links where recorded. Unverifiable old history stays unresolved.
5. Verify the deployed application role can read its own captures and cannot see another tenant's captures or insert/modify observations through tenant context. Also verify the actual webhook service role has platform-context INSERT and BIGSERIAL sequence privileges on both snapshot tables; the migrations do not configure deployment-specific role grants. Verify document links and the public billing support mailbox in the designated environment.

Invoice capture is bounded by ten pages per collection, a maximum of ten linked payments and a 20-second capture deadline. Top-up refund-list capture is bounded by three pages and 12 seconds. Truncation, unsupported relationships, conflicting objects and interrupted reads do not become complete detail. A partial line collection cannot authorize a first subscription payment. Already-proved financial outcomes remain separate from display-detail completeness.

## Investigate or refresh one existing record

Run from `backend/` in the designated environment using the established operator identity, secrets and restricted database access. The existing webhook recovery procedure still owns financial retries and legacy authorization.

```text
python -m scripts.reconcile_billing_webhooks inspect --event-id evt_RECORDED_EVENT
python -m scripts.reconcile_billing_webhooks refresh-details --event-id evt_RECORDED_EVENT --operator "named finance operator" --reason "Provider read recovered; reconcile this recorded invoice or refund"
```

`inspect` is read-only. `refresh-details` verifies the retained event against the current provider event, locks the reference, follows current provider/customer/order or invoice bindings, appends a new local observation and records the operator/reason. It does not call the financial application handler, issue a charge/refund, change minutes/subscription access, replay a notification, or change a legacy receipt to completed. A fresh observation reference permits recovery after a completed webhook whose display capture was partial. Inspect the returned `detail_status`; `handled` alone does not mean every provider detail was available.

Do not refresh from a bare browser-supplied invoice/order ID or customer search. A shared PaymentIntent does not allocate an entire refund to each invoice. Ambiguous invoice allocations and duplicate legacy top-up payment bindings require review. Neither the financial ledger nor the observation history should be edited/deleted to clear an error.

The command requires a retained event that the provider can still retrieve. Stripe documents [event retrieval within 30 days of creation](https://docs.stripe.com/api/events/retrieve); older unavailable events remain unresolved. This is not an arbitrary historical backfill. If incomplete invoice lines prevented first-payment verification, use the existing CP03 financial retry procedure after resolving that evidence. Detail refresh requires an already recorded invoice and cannot create the missing invoice or activate the purchase.

## Finance review and customer communication

For each designated refund, record the order/invoice ID, tenant binding, event ID, payment/charge ID, refund ID, provider status and amount/currency, capture time, original positive ledger entry and any negative entry. Compare provider invoice/PDF totals, actual component records and the API/UI display. If a credit note exists, identify whether it represents account credit, a refund reference or another adjustment; do not assume all credit notes are cash refunds.

Partial refunds retain provider status without assigning unapproved minute amounts. The existing financial event remains in review until finance approves the allocation policy. A support request is only a request; the documented `billing@talkleeai.com` contact is exposed without a delivery, response-time or refund promise. Mailbox ownership/operation and the CP06 recipient improvements remain acceptance requirements.

Administrative reconciliation totals cover only the returned rows, disclose truncation and group money by currency. `reversed_cents` includes accounting reversals and must not be described as money received by customers. For a complete finance period, verify the export covers all applicable rows and reconcile each currency independently.

## Required acceptance evidence and rollback

The local synthetic reconciliation fixture/tests cover taxes, discounts, credits, paid/remaining amounts, historical plan edits, exact currency precision, incomplete data, tenant denial, refund status versus ledger changes and duplicate events. They are not provider-account or bank acceptance.

Before closure, a named finance reviewer must sign off representative designated-account invoices and a test refund from provider through database, API and customer screen; confirm webhook subscriptions, read-role permissions, retention ownership and support operation; and record treatment of legacy/partial/ambiguous cases. A screenshot or green unit suite alone does not satisfy this gate.

If richer detail needs rollback, retain both snapshot tables, provider identities and monetary records. Present truthful stored summaries and provider documents while a compatible API/UI is deployed. Never restore fabricated plan/usage/tax figures. Both migrations refuse destructive downgrade. Record any customer-facing correction and its affected references; do not issue compensating money operations merely to repair display data.

Provider references: [Invoice fields](https://docs.stripe.com/api/invoices/object), [invoice line items](https://docs.stripe.com/api/invoices/line_item), [refund objects](https://docs.stripe.com/api/refunds/object), [credit notes](https://docs.stripe.com/api/credit_notes/object), and [refund tracing](https://docs.stripe.com/refunds#tracing-refunds). These define source semantics; they do not prove this deployment's configuration or settlement.
