# CP04 implementation and verification — 4 October 2026

CP04 repairs the existing invoice, usage and top-up/refund displays. Local implementation and the recorded synthetic checks passed; the package remains **in progress** until designated-provider, finance and deployed-candidate acceptance are evidenced. No new billing product, provider, pricing policy, refund-issuance workflow or metering calendar was introduced.

Implementation candidate: `4ec7f51eb05ed00b4ffea68fd77c2ba5649c3c5d`. Base: `5ff021b4` on `codex/production-ready-20261004`, in the isolated `tmp/production-ready-20261004` worktree. The original application's dirty files were preserved. No push, deployment, real payment, refund, email or provider-account configuration change was performed.

## Root causes and repairs

| Observed problem | Root cause and resulting behavior |
|---|---|
| Old invoice details changed with today's plan and claimed allowance was actual usage. Tax, overage and peak concurrency appeared as zero. | The detail endpoint joined the current plan, invented a single line item and used `amount_due` for multiple accounting totals. It now reads captured provider facts. Missing detail is unavailable, never a zero charge or made-up line. Current call usage is presented separately. |
| Amounts could be wrong outside two-decimal currencies, and paid invoices were labeled “Total Due.” | List/detail/overview divided every amount by 100 and conflated total, due, paid and remaining. A shared validated contract retains integer minor units and known currency precision; the UI formats them exactly and labels each source amount separately. |
| A provider invoice could have more lines than the embedded response, or later adjustments could be absent. | Bounded pagination captures actual lines, taxes, discounts, credit notes and invoice-bound refunds. Relevant signed invoice/credit/refund events append new observations. Incomplete collections remain partial/unavailable, and a shared payment is not used to assign an entire refund to several invoices. |
| Saving invoice updates before payment could suppress the first receipt after adding richer capture. | New invoice records explicitly distinguish known managed notification history from unknown legacy history. A newly captured paid invoice can receive its first durable receipt when the financial event arrives; legacy uncertainty remains fenced. Existing notification identity prevents duplicates. |
| Usage failures looked like zero usage; unlimited plans looked over quota. | Billing reads used an intentionally fail-soft authentication helper and treated zero allocation as a finite allowance. Billing now uses the strict canonical tenant-scoped meter, exposes the unlimited sentinel explicitly and returns unavailable errors on read failure. Unsupported non-minute usage does not silently reuse minute allowance. |
| The daily chart and top-up balance disagreed with the canonical meter, and a 59-second call displayed zero duration. | The daily query omitted finalized transfer legs; the top-up read omitted tenant context required by forced RLS. Both now follow the existing settled parent/finalized-transfer rules. The average uses exact daily seconds, and separately floored days are not mistaken for the monthly aggregate. |
| The screen estimated an unapproved overage charge. | The endpoint defaulted missing and explicit-zero rates to 0.10 and ignored purchased allowance. It now uses actual allowance and returns no monetary estimate without an approved canonical price. |
| A checkout return claimed payment succeeded, cancellation meant no charge, and unrelated balance changes could “confirm” a purchase. | Navigation flags and aggregate balance changes were treated as transaction evidence. Return messages are neutral; saved order/ledger status is shown. Return URLs preserve existing query parameters and identify the order. |
| Refunds and adjustments could not be traced to a purchase, and populated adjustments failed frontend validation. | `/billing/adjustments` always returned an empty list and the ledger API omitted stored relationships. Both read endpoints now use one public serializer with string record IDs and known currency precision. The UI shows signed accounting movements with order/payment/event references. Separate captured refund objects retain provider reference/status/amount without implying bank receipt. Partial-refund minute policy is unchanged. |
| One legacy payment binding could attach refund detail to the wrong order. | The payment-ID column is not unique. Observation handling rejects multiple saved matches and verifies current provider metadata, customer, mode, currency and amount against the exact saved order. |
| Billing data was visible to a same-tenant role without billing-read permission. | Existing reads checked authentication and tenant identity but omitted the role permission. Billing reads now require `BILLING_READ`; foreign tenant invoices remain inaccessible. |
| Administrative totals mixed currencies and looked complete despite a row limit. | Money is now grouped by currency, totals explicitly cover returned rows, and truncation is disclosed. Accounting reversals are not labeled as cash received by a customer. |

The invoice list also states its actual recent-record limit. Failed/malformed successful API responses remain visible errors. Provider-document URLs are validated; unavailable links do not make valid historical summaries unreadable. The existing billing support contact is exposed without promising delivery, response time or a completed refund.

## Implementation and recovery

Two compact modules own sanitized invoice and refund observations. Existing signed webhook processing, PostgreSQL transactions/locks, the immutable money ledger and the existing worker are reused. Observation capture does not create a second financial workflow.

Migration `0055_invoice_snapshots` adds append-only invoice observations, BIGINT saved amounts and the legacy notification-history distinction. `0056_billing_refund_snapshots` adds append-only top-up refund observations. Composite foreign keys enforce parent/tenant relationships; forced RLS permits tenant reads and platform-only inserts. No synthetic legacy backfill or destructive downgrade is provided.

The existing reconciliation CLI adds `refresh-details` for one verified recorded event. It retrieves current provider facts and appends a local observation plus operator review record. It cannot issue money, replay financial handlers, change minutes/access, send notifications or convert an unresolved legacy receipt to completed. The response exposes detail completeness separately from processing status.

The [billing record reconciliation procedure](../production-readiness/billing-record-reconciliation.md) documents source meanings, webhook event configuration, bounded capture, operator commands, retained history, finance review and compatible rollback.

## Verification

Commands, artifact hashes and the implementation candidate are recorded in [the verification manifest](artifacts/cp04/verification.json). Provider objects are synthetic throughout. Real PostgreSQL tests use production codecs, transactions and roles without SUPERUSER or BYPASSRLS. No live Stripe, email or bank result is claimed.

| Final check | Observed result and boundary |
|---|---|
| Broad backend regression | 1,652 passed, one existing public-route-inventory skip, eight deprecation warnings. This is the selected regression set, not the entire backend suite. |
| Disposable PostgreSQL acceptance | 142 passed against a fresh database through migration 0056, including actual roles, tenant isolation and signed local HTTP processing. |
| Final ledger API change | One additional actual PostgreSQL contract test passed for both read endpoints and tenant isolation after the shared serializer correction. |
| Focused financial/read tests | 224 passed before the last serializer correction; the final broad run and additional database test cover that last change. |
| Frontend tests | 135 initial tests and 39 overlapping final tests passed: 140 distinct cases, not a single 140-case invocation. Includes actual stored invoice and ledger API fixtures. |
| Static and build checks | Changed Python Ruff F/E9, changed frontend ESLint, TypeScript and the final production build passed. |
| Visual check | Actual invoice React component with production CSS passed desktop/mobile checks and visual inspection. No authenticated deployed journey was exercised. |

The disposable database server and task browser were stopped. The implementation and evidence are committed locally; nothing was pushed or deployed.

The [reconciliation example](artifacts/cp04/reconciliation-example.json) contains the synthetic provider invoice, actual stored snapshot and actual API response. The [ledger example](artifacts/cp04/ledger-contract.json) records both actual ledger read endpoints after local PostgreSQL entries. The frontend validates and renders these exact responses; this establishes the local cross-layer contract without pretending to be authenticated deployed or live-provider acceptance.

The review/testing process caught and corrected incomplete component amounts being marked complete, ambiguous payment bindings, insufficient refund-list totals, the capture-before-payment notification regression, legacy document-link validation a duplicate unlimited-usage display, sub-minute duration truncation and a nonempty ledger response-shape mismatch. Independent duties were split between root, the invoice/provider agent, the billing read/refund review agent and the frontend agent.

Intermediate test failures are retained separately: the new refund table was missing and then unsorted in the schema inventory assertion; an older downgrade test expected the 0054 message even though 0056 correctly refused first; a synthetic pending notification lacked fixture cleanup and affected a later global-drain test; and a frontend assertion did not accept Intl's valid nonbreaking currency space. These are not counted as final acceptance. Final database verification uses a freshly bootstrapped disposable database and serial execution, with immutable history retained.

## Remaining closure requirements

1. Apply the reviewed migrations and coordinated API/UI candidate in the designated environment, verify the actual application role and refresh existing clients. Verify the signed webhook endpoint's event subscriptions and account/mode.
2. A named finance reviewer must compare representative actual invoices, taxes/discounts/credits, historical plan changes and a designated test refund from provider through stored records, API and screen. Local synthetic fixtures are not that acceptance.
3. Approve partial-refund minute treatment and reconcile legacy/ambiguous payment and notification history. A provider refund observation alone does not authorize an allowance adjustment.
4. Verify the documented support mailbox, billing recipient/delivery behavior, retention ownership and operational recovery. CP06 retains the known recipient/cancellation work; CP03/CP02 account acceptance remains relevant.
5. Complete the overall production-ready plan's deployed voice, integration, customer-value and release gates. This billing package does not certify STT/TTS, assistant quality or the whole product as ready for paid users.

The feature freeze remains active. Package closure is not inferred from local test counts.
