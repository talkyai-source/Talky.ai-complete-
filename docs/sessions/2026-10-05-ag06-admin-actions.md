# AG06 Admin action cancellation and retry truth

This is local candidate evidence, not deployment or provider acceptance. The baseline is `71cdeefa59a0f786f236d3a3d25ba592470ddf05`. No customer call, email, SMS or calendar operation was performed. The parent session owns the final AG06 source binding and aggregate validation.

## Reproduced problems

1. Admin cancellation read `pending`/`scheduled` and then updated by ID without a status/ownership fence. A deterministic unit interleaving let a claim become `running` after the read; Admin still changed it to cancelled.
2. A pending email/SMS audit row did not own execution admission. Those services can already be awaiting a provider while their audit is pending. Treating every pending audit as cancellable was unproved.
3. Callback preparation committed a dialer job before awaiting Redis, but Admin still permitted cancellation. The preserved baseline reproduction loads the two original modules in memory from the recorded commit and pauses a synthetic queue. Actual PostgreSQL then records **cancel reply cancelled, action receipt cancelled, dialer job queued, one queue submission**. No source checkout is replaced and no telephony worker consumes the synthetic queue.
4. `RETRYABLE_ACTION_TYPES` listed `send_email`, `send_sms`, `set_reminder`. The endpoint inserted a new pending audit and replied “queued for retry”; there is no generic consumer for these pending rows. The existing callback consumer handles only voice `schedule_callback` rows in `scheduled` state. Three independent initial controls reproduced the false retry acknowledgment.

Initial unit result: **5 failed** (`artifacts/ag06/admin-actions-initial.txt`). Actual database baseline queue-race reproduction: **1 expected failure**, with the observed boundary facts printed in `artifacts/ag06/callback-race-baseline.txt`; executable source is retained alongside it. The first attempt to invoke that artifact outside backend pytest configuration was skipped by pytest's coroutine handling; it is not counted as evidence. The recorded reproduction uses `-c pytest.ini` and actually executes the case.

## Narrow repair

- Cancellation locks the authoritative action row, sharing the same PostgreSQL ordering as durable claim and callback preparation. A conditional tenant-bound update acknowledges cancellation only before dispatch ownership changes. The operation has a five-second bound and an inconclusive database outcome returns 503, not success.
- An unclaimed payload-bound durable execution receipt may cancel. A legacy pending audit or a scheduled non-callback audit cannot claim that stopping its row stops underlying work.
- A scheduled voice callback may cancel before its deterministic dialer job is reserved. Callback preparation now saves the existing job ID into its output receipt in the same transaction as creating that job, before the queue await. A reserved job, including a historical reservation without the new JSON marker, prevents cancellation and produces 409. A lost queue acknowledgment retries only the same job/key through the existing idempotent queue handoff.
- All existing Admin action list/detail/retry/cancel paths preserve the existing admin-role policy but scope tenant/partner admins to their authenticated tenant. Missing tenant context fails with 403; requested foreign-tenant filters fail with 403 and foreign IDs produce no receipt details. Only normalized platform admins retain platform-wide receipt access. This avoids expanding authority when using the async pool for cancellation.
- Unsupported Admin retry returns structured 501 with the original receipt identity, current status, `retryable=false`, and explicit **no action was queued** wording. Detail flags do not advertise unsupported retry. No claim, account reference, idempotency key or original effect is erased.

## Retry and reconciliation remain explicit

The 501 response is containment of a false acknowledgment, **not completion of automatic retry or operator reconciliation**. There is no new worker, retry queue or generic orchestration API.

The supported existing paths are distinct:

- A repeated execution with the original durable key may recover its saved receipt; it must not replay running/unknown effects. The shared receipt recovery owner handles dashboard presentation.
- A callback outbox retries its known deterministic queue identity. Queue admission proves neither a completed telephone call nor customer contact.
- A fresh assistant preview can be requested and confirmed under current permissions after authoritative evidence establishes the old attempt did not execute. A fresh preview is a new reviewed intent, not automatic reuse of a failed receipt. Unknown/running effects remain held; an immediate empty search, timeout or model opinion does not establish non-execution.
- Original-account/provider-reference inspection and any supported resolution operation are separate AG06 work. Legacy records without sufficient destination/effect proof remain unresolved. These edits do not introduce a resolution mutation or declare an uncertain remote effect failed.

## Verification and limits

Final owned batch: **20 passed, no skips, four existing datetime deprecation warnings, 14.34 seconds**: twelve unit cases and eight actual PostgreSQL cases. The latter uses the retained, fully migrated synthetic public database at `0057_transcript_save_state`; `0058` is owned by the CRM track and was not yet applied to public for this run. It reuses UUID-scoped actual-table fixtures and a `NOLOGIN NOSUPERUSER NOBYPASSRLS` pool. Legacy synchronous Admin read-adapter connections are explicitly redirected to the same synthetic loopback DSN; their explicit tenant predicates, rather than a claim of restricted-role enforcement on those separate connections, are tested.

Actual database controls cover cancellation before claim, rejection after committed claim, callback cancellation before reservation, rejection during the queue await, a lost acknowledgment reusing one job, a legacy reserved job without JSON marker, and tenant/partner list/detail/retry/cancel isolation. Unit controls also preserve running/unknown/completed/failed receipts and reject missing tenant context. Queue and external-executor boundaries are synthetic; no live Redis, carrier or messaging provider result is implied.

Scoped Ruff `--select F` passed for both owned application files and both owned test modules (`artifacts/ag06/admin-cancellation-lint.txt`); owned `git diff --check` passed. After this batch, the dashboard receipt owner applied `0058_crm_contact_effect` to the same retained database. That later migration is not retroactively attributed to this 20-case run; the parent session owns the final combined acceptance at the new head.

Final command from `backend`:

```powershell
$env:TEST_DATABASE_URL='postgresql://talky@127.0.0.1:55434/cp04_acceptance_test'
& 'C:/Users/AL AZIZ TECH/Desktop/Talky.ai-complete-/backend/.venv/Scripts/python.exe' -m pytest -q tests/unit/test_ag06_admin_actions.py tests/integration/test_ag06_action_cancellation.py 2>&1 | Tee-Object '../docs/sessions/artifacts/ag06/admin-cancellation-final.txt'
```

Baseline reproduction command, also from `backend`:

```powershell
python -m pytest -c pytest.ini -q -s '../docs/sessions/artifacts/ag06/callback-race-baseline.py'
```

Owned files: `backend/app/api/v1/endpoints/admin/actions.py`, `backend/app/services/voice_callback_service.py`, the two named test modules, this note, and their named evidence artifacts. No migration is introduced by this slice. Rollback must preserve existing running/unknown/completed receipts and reserved callback job identities; do not restore false cancellation/retry acknowledgments or reset idempotency fences.
