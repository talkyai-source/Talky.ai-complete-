# Backend follow-up fixes — 2026-09-16

## Outcome

The eight backend defect areas from the follow-up QA have been implemented locally, with reproductions converted into acceptance tests. Production has not been changed. This is not a live-call acceptance certificate.

Implementation location: `C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-qa-fixes-20260916`.

Branch: `codex/qa-remediation-20260916`.

Base commit: `88ebc8f338d4c50e9d7456239a28343afd20561b`, with the preceding QA remediation changes retained in the same isolated worktree. The working changes are uncommitted and unpushed. This report is deliberately in the main repository; implementation files remain in the isolated worktree to avoid mixing the shared checkout's edits.

## Fixes and their evidence

Paths in this section are relative to that isolated worktree.

| Finding | Root-cause correction | Executable evidence |
|---|---|---|
| Tenant provider credentials remained cached indefinitely | `backend/app/domain/services/credential_resolver.py` no longer caches credential values or missing-credential results. Each resolution reads the current tenant-scoped database state. | Real-database tests create, rotate and disable credentials, then resolve through separate resolver instances without invalidation. |
| Reminder retry SQL did not bind the delay | `backend/app/workers/reminder_worker.py` uses a bound numeric argument to `make_interval`, outside a string literal. Retry count and future scheduling commit together. | A failed send leaves a pending row with incremented retry count and a future retry timestamp in PostgreSQL. |
| Competing workers could deliver one reminder twice | The worker commits a conditional pending-to-processing claim and unique ownership token before provider I/O. Completion requires the same tenant and token. | Two processors compete for one real database row; only one email invocation occurs. |
| An optional event INSERT could roll back a lead update | `backend/app/domain/services/event_emitter.py` isolates the event INSERT in a nested transaction/savepoint. Serialization failures are also contained. | A PostgreSQL constraint rejects the event while the surrounding lead update still commits. Malformed optional metadata does not escape. |
| Summary failures had no durable ownership/retry state and could appear available | `backend/app/domain/services/call_summary/store.py` claims a durable lease, bounds retries and deadlines, records failure/cooldown, and fences stale results. `backend/app/api/v1/endpoints/calls.py` returns unavailable rather than a successful sentinel summary. | Concurrent requests generate once; failed reads respect cooldown; third failure exhausts the budget; expired owners cannot overwrite newer results; timeouts persist failure; other tenants cannot claim the row. |
| SMS failure prevented email fallback | Fallback now depends on an explicit provider result: confirmed not-sent may use email; unknown acceptance may not. The result travels through the SMS provider, result contract, service and reminder worker. | Definitive SMS rejection invokes email once; unknown delivery invokes no email and is not automatically resent. |
| Incomplete call evidence could qualify a lead | Summary lead-marking fails closed for unknown/non-finite duration and excludes interim or explicitly excluded transcript turns. | Tests reject null duration and excluded-turn evidence. Existing positive qualification tests still run. |
| Reminder database failures looked like an empty healthy scan | Scan errors propagate to the worker's consecutive-error/restart policy. A successful empty scan resets the error streak. | The database-scan regression raises; repeated failures reach the worker's nonzero-exit restart path. |

### Connected defects traced and corrected

1. A resolver first created before the service container was initialized could retain no database connection indefinitely. It now binds when the container becomes ready. A regression reproduces that startup sequence.
2. The Vonage SMS connector reported simulated success when its SDK was absent. Missing SDK now returns a definitive not-sent failure, never a fabricated delivery result.
3. Unknown and internal provider response statuses were unsafe candidates for fallback. Only recognized rejection statuses authorize fallback; ambiguous results remain unknown. Tests cover missing status, unknown status and internal-error status.
4. The summary backfill script automatically forced retries, bypassing ownership/retry protection. Automatic attempts now use the same durable budget. Only an explicit operator `--force` resets the budget, and it still cannot steal an active lease.
5. Optional event metadata serialization previously happened outside its failure boundary. Serialization is now contained alongside insertion, without logging customer message content.

These are changes to the shared source of truth and the connected callers, not delays or frontend masking. No production telephony timeout was changed for these fixes.

## Premortem and failure behavior

### Credentials

- Creating, rotating or disabling a credential must be visible across processes without a local cache clear: every resolution reads the database.
- The existing platform-key fallback policy for missing/disabled credentials or lookup failures is unchanged. This work does not redesign BYOK entitlement policy.
- Provider clients that already hold a credential are not forcibly interrupted during a call. The guarantee applies to subsequent resolution, not revocation of already-open provider connections.

### Reminder delivery

- A process can crash after a provider accepts a message but before database completion. Such work becomes `delivery_unknown`, not pending.
- Stale processing ownership is held after 120 seconds. It is not automatically reassigned for another send.
- A timeout does not prove non-delivery. SMS timeout/ambiguous acceptance does not trigger email fallback.
- Provider I/O runs after the database claim commits and outside a long-lived database transaction. The scan connection is also released before individual sends.
- SMS and email each have a 30-second wait limit. Blocking SDK work runs outside the event loop; cancellation cannot prove that the provider did not accept it.
- Definitive failures use bounded exponential retry scheduling. Unknown deliveries require reconciliation, not blind replay.
- An ownership token prevents a different worker from overwriting a result. A late result from the same owner may resolve its held row.
- `sent` means provider acceptance, not verified delivery to the handset. Vonage distinguishes API acceptance from later delivery receipts. [Vonage delivery receipt guide](https://developer.vonage.com/en/messaging/sms/guides/delivery-receipts).
- No delivery-reconciliation operator screen or delivery-receipt ingestion was added in this change.

### Summaries

- Multiple API/worker processes share a database lease, not an in-memory lock.
- A generation has a 70-second deadline and a 120-second lease; provider work runs outside the database transaction.
- Automatic work is limited to three generation attempts, with recorded retry timestamps. The final attempt is exhausted rather than automatically retried again.
- Existing lower-level bounded provider retry behavior remains in place; a generation attempt is not necessarily one HTTP request.
- Process crashes leave an expiring lease. Expired ownership cannot overwrite the next owner's successful result.
- Legacy unavailable-sentinel rows use the same bounded recovery path.
- Failed generation is not stored as a successful summary and is not presented as available by the API.
- Explicit force is an operator action; automatic backfill does not use it to defeat the retry budget.

### Optional events and tenant isolation

- A rejected optional event must not undo the primary business operation. A savepoint supplies that boundary.
- This containment does not grant missing tenant permissions or fix every caller that emits an event with incorrect RLS context. Event delivery failures remain observable.
- Summary and reminder claims include tenant predicates and tenant-scoped access. The tests include wrong-tenant claim attempts.
- The inventory's remaining review entries are not certified safe solely because its blocking gate passes.

## Migration and release boundary

New migration: `backend/Alembic/versions/0047_backend_work_claims.py`, after the preceding local `0046_qa_data_contracts` migration.

It adds summary state, attempt counts, ownership tokens, lease/cooldown timestamps and failure code to calls; reminder processing ownership/timestamps; and an index for processing-age scans. It does not delete historical call or reminder records.

The actual migration SQL was exercised against disposable PostgreSQL fixtures. This is not a rehearsal against a full production database clone. New worker/store code requires the migration before those processes restart.

Downgrade deliberately does not erase ownership/failure evidence. A code rollback retains the additive schema. In particular, do not replay held reminders during rollback: old delivery code does not provide the new ownership guarantees. Stop/drain the affected workers for a controlled rollback and reconcile held work first.

No commit, push, production migration, service restart, live call, customer email or customer SMS was performed in this turn.

## Verification

The initial defect reproductions failed before implementation. The converted tests assert corrected behavior, rather than merely passing when the old defect is observed.

- Follow-up integration/provider/safety tests: `29 passed in 6.71s`. This consists of 18 integration cases, six SMS-delivery cases and five connected safety cases. Provider calls are mocked; database cases use real disposable PostgreSQL, not production.
- Final combined PostgreSQL/targeted run, also including the preceding ownership/backup-RLS migration regression: **`30 passed in 5.48s`**, exit 0.
- Talk-Leee: typecheck and lint exit 0; `480 tests, 478 pass, 0 fail, 2 skipped`, duration 161168.7565 ms.
- Admin: lint and production build exit 0; `13 tests, 13 pass, 0 fail`.
- Repository-defined Ruff gate (`--select F --extend-ignore F401,F841`): `All checks passed!`. A stricter diagnostic run without those documented exclusions reports 198 unused-import/local findings; that broader cleanup was not performed.
- Alembic: exactly one head, `0047_backend_work_claims`.
- RLS inventory: 448 acquisitions; 401 okay, zero needs-tenant, 47 need review. Blocking gate passes; the 47 review entries remain review work.
- Backend application/test/migration whitespace check: exit 0.
- Full backend run with concurrent frontend checks: `1 failed, 8968 passed, 8 skipped, 1453 warnings in 390.59s`. The failure was unchanged `test_confirmation_waits_until_every_transfer_human_leg_is_absent`, whose fixture allows only 30 ms. Its file then passed all 27 tests in isolation. No test assertion or production timeout was changed.
- Final full-suite rerun without competing test jobs: **`8969 passed, 8 skipped in 317.29s (0:05:17)`**, exit 0. This used the repository's documented warning suppression and disabled pytest's cache provider. The earlier timing-sensitive failure is retained above rather than hidden.

## Not done / remaining acceptance

1. These fixes are not live. They must be reviewed/committed from the isolated worktree, merged against current main, gated again if main has moved, and released through the supported deployment path.
2. Production was not re-audited in this turn. The prior read-only snapshot reported code `2f34c72e`, schema 0045 and an inactive synthetic timer; those are historical observations, not freshly verified state here.
3. Live browser QA, real inbound/outbound calls, provider acceptance and recording/summary/billing end-to-end acceptance remain unperformed for these changes.
4. Synthetic monitoring repair, historical stuck-job reconciliation and recording backup/restore verification remain operational work from the earlier audit.
5. The event savepoint prevents a failed optional event from rolling back other data. Specific production RLS-denied event callers still need separate context diagnosis if those denials persist.
6. Held uncertain deliveries need an operator reconciliation workflow. This change intentionally does not guess whether to resend them.
7. Passing unit/security tests does not certify carrier audio quality or explain every prior audio-gap warning.

Test-generated changes to `telephony/deploy/keepalived/notify.sh` and tracked telephony bytecode are not application fixes and must not be included in a future commit.

The temporary PostgreSQL server was stopped after verification; retained local database files/evidence schemas were not deleted.
