# OP07: durable opt-out through final settlement

Source commits: `3ae053b081fdced8614e1712add0d6dae4b8af03` and compatibility follow-up `3da14e88734c4c9771b68c97e3f01eeb006ec09d`, based on `e7b8a84c9d8e74b12a10a6bab88a98fef40e8050`. Work was isolated in `tmp/production-ready-dnc-settlement-20261005`; no production, provider, telephone, email, or deployment actions were performed.

## Reproduced problems

The shared live opt-out purge correctly wrote `leads.status=dnc`, `last_call_result=caller_opt_out`, and an active DNC row. Later outbound finalization resolved the real call outcome and unconditionally replaced the lead projection with `contacted/answered` or `completed/goal_achieved`. The lifecycle then skipped its second purge because the earlier purge had been acknowledged. A subsequent pipeline failure could also book a new retry against the retained processing job. The independent phone-level DNC lookup still blocked origination; these probes did not demonstrate a successful forbidden call.

`DNCService.bulk_import` ignored the `INSERT 0 0` result from a duplicate insert. An expired same-source entry was reported as accepted while `/dnc/check` still returned false. The bulk endpoint accepts a source and has no expiry parameter; both `caller_opt_out` and ordinary `bulk_import` were reachable cases.

The initial method probe and its synthetic results are retained in [initial-probe.py.txt](artifacts/op07-dnc-settlement/initial-probe.py.txt) and [initial-result.json](artifacts/op07-dnc-settlement/initial-result.json). To replay the original probe against its base revision, copy the script to that checkout's `tmp/op07_dnc_followup_probe.py` and run the recorded Python interpreter with `-I`. It calls real service methods and endpoint handlers through in-memory SQL ports, not real HTTP authentication or PostgreSQL.

## Repair and boundaries

`CallService` now locks the tenant-bound lead before observing suppression. It uses the locked call's tenant and original phone number, normalized by the same DNC helper, and requires an active `caller_opt_out` entry. Tenant and global matching follow the existing CallGuard policy. A matching DNC row remains share-locked during settlement; an acknowledged deletion waits for this transaction. Lookup failure or an invalid normal outbound destination aborts settlement rather than authorizing a retry. Persisted inbound/test records with no dialer-job or retry-payload ownership may contain non-dialable labels; an inapplicable DNC lookup is omitted for those records without claiming suppression.

The actual call outcome and duration remain intact. Job `last_outcome` remains the actual outcome. The lead keeps its separate `dnc/caller_opt_out` projection, and the just-finished job becomes non-retryable without incrementing the retry counter. The manual `do_not_call` flag is untouched. Other sources, expired or removed entries, and other tenants or phone numbers retain the existing disposition policy. A changed lead phone cannot substitute for the original call destination.

Recovery also checks suppression before replaying an unacknowledged retry outbox. It removes that pending retry intent without recounting the call, and only cancels not-yet-originated work. It does not release a newer `processing` or `calling` attempt sharing the job ID. The normal finalizer of that attempt still owns its settlement. Already completed call history is preserved.

Bulk acceptance now means an acknowledged active suppression, not a newly inserted row count. New entries remain permanent as before. Existing explicit caller opt-outs become permanent. Other-source entries retain their existing expiry: an expired duplicate is skipped rather than silently extended or reported active. Active duplicates are accepted and counted per input, preserving the existing response shape. Per-row savepoints allow a genuine row failure to coexist with later successful inputs; authorization/RLS failure propagates and rolls the batch back instead of being labelled an invalid row.

The obsolete no-pool RPC/sequential settlement entrypoints already reject `atomic_pool_required`; repository search found no callers of their historical private mutation helpers. No alternate compatibility settlement was enabled.

## Verification

- Initial new settlement controls: **6 failed, 1 passed** on the unmodified base. [Log](artifacts/op07-dnc-settlement/settlement-initial.txt).
- Independent review found two draft ordering gaps. The newer-owner control reproduced **2 failures** before its fix. [Log](artifacts/op07-dnc-settlement/old-owner-red.txt). The cached-negative DNC race reproduced **1 actual PostgreSQL assertion failure** before moving the lead lock. [Valid red](artifacts/op07-dnc-settlement/lead-race-valid-red.txt).
- Final unit regression: **227 passed, 0 skipped**, across 15 modules. The 124 warnings are existing datetime/TestClient deprecations. [Log](artifacts/op07-dnc-settlement/regression-final.txt).
- Final actual PostgreSQL acceptance: **35 passed, 0 skipped**: 25 new cases plus 10 existing native DNC cases. [Log](artifacts/op07-dnc-settlement/postgres-final.txt). The retained disposable localhost database had the full migrated schema through `0061` supplied by the parent workflow; this slice did not bootstrap or migrate it. Fixtures used UUID synthetic tenants, a random `NOSUPERUSER NOBYPASSRLS` role, existing RLS context helpers, and canonical call/CRM-trigger permissions. The settlement pool used the application's JSONB codec. Fixtures and roles were cleaned, and the exclusive DB slot was released.
- CI-rule Ruff and `git diff --check` passed. [Ruff](artifacts/op07-dnc-settlement/ruff-ci.txt). Exact commands and module inventory are in [verification.json](artifacts/op07-dnc-settlement/verification.json).
- LLM agent independently read the final bounded application diff and the retained race/ownership controls; no further material defect was found. That was a read review, not an additional test run.

The first reviewed source had 220 unit passes and 31 PG passes; these overlap the final runs and are not additional coverage. A subsequent compatibility review identified legitimate stored `anonymous` inbound and `browser-test` rows. Two actual pooled unit controls failed before the narrow compatibility exception. [Red](artifacts/op07-dnc-settlement/non-dialable-red.txt). Final PG controls revoke DNC SELECT and show these records can still settle actual outcome/duration when they own no retry, while malformed normal outbound and malformed test records with a dialer owner fail closed. The current browser and inbound finalizers use separate paths; this preserves supported stored-record compatibility, not a demonstrated failure in those live paths. The follow-up received another independent read review.

The PG tests cover first settlement, duplicate accounting, pending-outbox recovery, newer live ownership, original-destination binding, foreign/global/expired/removed/other-source controls, transaction rollback on DNC lookup failure, concurrent opt-out while waiting for the lead, acknowledged unblock lock ordering, expired bulk imports, concurrent duplicate imports, row-error savepoint recovery, and authorization failure.

Earlier PG harness attempts are retained but are not product defects or passing evidence. They lacked task-local teardown context, canonical CRM-trigger grants, or the production JSONB codec. One client-cancelled DELETE had an ambiguous remote result; the final test instead awaits a server lock-timeout error and rollback before releasing settlement. A newer-call fixture also initially omitted its mandatory campaign binding. The first broader unit run's 19 failures were old fake call rows missing canonical tenant/phone fields and query responses; the actual retry-cap assertions were preserved when those fixtures were corrected. The first Ruff run identified an imported-fixture alias, corrected without changing test behavior.

## Limits and rollout

This is local method/transaction evidence, not a live telephone call, deployed browser, or production-host result. PostgreSQL and Redis are still separate boundaries. A new opt-out can arrive after the settlement observation; the existing final CallGuard remains the phone-admission safety boundary. This repair does not claim globally atomic queue cancellation or cross-process exactly-once origination.

The existing manual unblock API still removes only the selected DNC entry. This change does not add a new unblock policy, rewrite completed history, backfill old lead projections, or modify the user-owned `do_not_call` flag. A terminal contact projection may continue to display `caller_opt_out` through existing UI fallback text rather than the manual-flag DNC badge; no frontend change is claimed.

Promote alongside the existing `0061` DNC schema contract. No new migration or provider configuration is required. Include `tests/integration/test_dnc_settlement_truth.py` in the migrated database CI matrix. The source and evidence are local commits only; the parent owns integration and release.
