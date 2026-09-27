# QA remediation — 16 September 2026

## Outcome

The reproduced application defects have code fixes and regression coverage in an isolated worktree. Production has not changed. Operational remediation and real-call acceptance are not complete; this is not a claim of full client readiness.

- Worktree: `C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-qa-fixes-20260916`.
- Branch: `codex/qa-remediation-20260916`.
- Frozen starting point: freshly fetched `origin/main`, `88ebc8f338d4c50e9d7456239a28343afd20561b`.
- Changes remain uncommitted and unpushed. The shared checkout's existing edits were not mixed into the fixes.
- Prior diagnosis: `QA-report-2026-09-16.md` in the shared repository root.
- Account in scope: AllStateEstimation, tenant `1845a165-08aa-4554-bcec-2d31ac523662`, not the older tenant beginning `790ca2db`.

All source references below are relative to the isolated worktree above.

## Implemented changes and proof

### 1. Dialer job updates: repair the shared SQL binding transformation

Root cause: repeated string replacements shifted `$4` to `$10`, then rewrote the `$1` prefix again. The six-column/four-ownership-parameter update failed after a call had originated.

Change: one pass over complete numbered bindings; the generated ownership clause retains the correct job, tenant, campaign and lead arguments. No ownership predicate or anti-duplicate safeguard was removed.

- Source: `backend/app/core/db.py:424`.
- Regression: `test_update_preserves_ownership_bindings`, including multi-digit original parameters and offsets.
- Real PostgreSQL proof: the actual Database.update helper updates the intended job; changing the tenant matches zero rows.
- Not repaired by this change: already-corrupted historical job state. The observed job linked to call `bb8e6dc2` still needs a controlled reconciliation against durable call evidence, not a blind retry.

### 2. Billing: tenant-scoped usage and a single effective allocation source

Root causes: top-up balance queried protected usage without tenant context; subscription display used base-plan minutes while quota enforcement used effective tenant allocation.

Changes:

- `backend/app/api/v1/endpoints/billing_topups.py:148` uses `acquire_with_tenant` before computing usage.
- `backend/app/api/v1/endpoints/billing.py:225` uses the same quota service for allocation, used minutes and remaining balance.
- `Talk-Leee/src/components/billing/billing-overview.tsx:326` labels the figure “Total minute allocation,” not base-plan included minutes.
- `Talk-Leee/src/lib/status-colors.ts:137` displays legacy outbound `billing_status=none` as “Usage-based,” without changing inbound settlement labels or inventing a billed amount.
- No allocation, entitlement, subscription or production call was changed.

Tests: `test_topup_balance_has_tenant_context`, `test_subscription_preserves_actual_allocation_not_just_plan`, and the frontend billing-label regression. The protected-connection fixture returns 0 usage without context and 24 minutes with context; the corrected endpoints return allocation 5000 and remaining 4976. This endpoint fixture is not presented as a production database execution.

### 3. Email confirmation: recognize the observed complete-readback exchange

Root cause: the capture gate rejected the agent's “Should I send … there?” confirmation wording despite its preceding full address readback.

Change: accept destination-confirmation questions only alongside the complete pending email. Wrong addresses, absent readbacks and unrelated callback questions remain rejected. This does not authorize email delivery.

- Source: `backend/app/domain/services/voice_pipeline/turn_runner.py:107` and `:193`.
- Regression: `test_complete_readback_and_destination_confirmation`, five cases.
- Existing capture/correction suites are included in the passing backend canonical suite.
- Live caller replay and confirmation persistence after deployment remain acceptance work.

### 4. Unsupported action promises: correct both prompt wiring and the speech guard

Root cause: completed-action checks did not catch future promises. Also, the capability instruction was conditional on offering tools; ordinary Cerebras streaming turns could omit it.

Changes:

- `backend/app/domain/services/llm_guardrails.py:71` rejects tested unsupported first-person email, callback, form and transfer promises without successful action evidence.
- `backend/app/domain/services/voice_pipeline/action_tools.py:429` states actual unavailable capabilities and permits confirmed-detail capture without promising a human will act.
- `backend/app/domain/services/voice_pipeline/turn_streamer.py:464` includes that instruction on ordinary streaming turns, including the Cerebras-shaped path, while preserving legacy end-session instructions.

Tests: `test_unavailable_action_promises_are_not_spoken` and the voice-action contract test exercising the actual pipeline with a plain-stream provider. The latter checks the system instruction and that the unsupported promise is replaced before speech synthesis.

Important limit: email delivery, callback scheduling, form submission and controlled transfer were not implemented or enabled. Honest limitations are the correct behavior for these unavailable actions. Prompting plus phrase guards is not a proof against every possible paraphrase.

### 5. Summaries: match the schema and bound recovery

Root causes: strict schema declared arrays of strings while the prompt and consumers expected objects; the completion allowance was insufficient for some responses.

Changes in `backend/app/domain/services/call_summary/summarizer.py`:

- Strict object entries for objections (`objection`, `handled`) and action items (`item`, `owner`), with required fields and no extra properties.
- Completion budgets 4096, then 8192 on one classified retry; low reasoning effort for the supported strict model.
- Uses `max_completion_tokens` at line 200.
- Explicit 30-second provider timeout and no hidden SDK retry loop.
- Retry once on incomplete JSON/object output, length exhaustion or `json_validate_failed`; unrelated failures return the existing unavailable result.
- Logs exception type/status, not provider bodies that can contain customer transcripts.

Tests: nested schema contract, `test_summary_retries_length_once_with_larger_budget`, and `test_summary_failure_log_does_not_leak_provider_body`. A stale old string-array assertion was replaced with the stronger object-field contract.

The API keyword and reasoning setting were checked against the installed SDK and [Groq's API reference](https://console.groq.com/docs/api-reference). No chargeable live model request was made.

Remaining: the existing fallback is not a durable failed-summary job state. A later read can request generation again. This change bounds each invocation; it does not claim persistent failure status or cross-request retry exhaustion.

### 6. Silence nudges: refresh the appropriate activity clock

Root cause: audio activity could suppress a nudge for one tick without advancing its deadline, allowing a stale nudge to fire during a natural pause.

Changes in `backend/app/domain/services/voice_pipeline/audio_ingest.py`:

- Accepted StartOfTurn stamps caller activity after the echo gate.
- Monitor consumes new caller activity at line 662.
- Acoustic activity advances the nudge clock at line 733, not the hangup clock. Noise alone therefore cannot indefinitely hold the call open.

Test: deterministic speech-then-pause regression in `test_audio_ingest_caller_first_silence.py`; existing silence/hangup and pipeline suites also pass. This is a timing/state fix, not an unsupported claim of improved carrier audio or universal listening accuracy.

### 7. Transcripts: one canonical reader for both call screens, explicit UTC

Root cause: raw diagnostic STT fragments were rendered as conversation turns; old UTC timestamps lacked an offset.

Changes:

- `Talk-Leee/src/lib/dashboard-api.ts:815` filters non-final/non-plaintext turns centrally for call history and call detail.
- `Talk-Leee/src/lib/call-presentation.ts` preserves legacy turns and parses old offset-free backend stamps as UTC.
- `backend/app/domain/services/transcript_service.py:20` and `:168` emit offset-aware UTC timestamps.
- Raw diagnostics stay available in backend transcript storage/API. Text-format responses are unchanged.

Tests: `dashboard-api.transcript.test.ts` first failed with four entries where two canonical entries were expected; then passed. `call-presentation.test.ts` covers filtering, legacy records and timezone parsing. The campaign-script formatter already filters `include_in_plaintext` and was left unchanged.

### 8. Health badge: align with the backend status contract

Root cause: UI accepted `ok` but not the actual `healthy` response.

Change: `Talk-Leee/src/lib/health-status.ts:3` maps both to Healthy, distinguishes initial Checking, and retains Degraded/Down behavior. Tested in `call-presentation.test.ts`.

This badge describes the health probe, not complete product readiness.

### 9. Recording metadata and backup-table isolation

Changes:

- `backend/app/domain/services/recording_service.py:966` explicitly writes `storage_provider=local` for the existing local bucket marker and `s3` otherwise.
- New migration `backend/Alembic/versions/0046_qa_data_contracts.py:15` preserves the historical AI-config backup and applies the canonical tenant/bypass policy with ENABLE/FORCE RLS if the table exists.
- The same migration corrects existing local recording metadata without moving or deleting files.
- Clean installs without the historical backup are supported. Downgrade refuses to remove tenant isolation; rollback should retain this additive migration.
- The RLS inventory guard now includes the protected backup table rather than silently ignoring it.

Real PostgreSQL test: `backend/tests/integration/test_qa_contracts_postgres.py` executes the migration SQL in an isolated empty database, verifies cloud metadata remains unchanged, exercises the missing-backup case, and checks a non-superuser/non-BYPASSRLS role sees zero rows without context, one own-tenant row with context, and cannot insert another tenant's row.

Not complete: migration is not applied to production; local-file backup/restore/retention is not verified. Accurate metadata alone is not recording durability.

## QA evidence

All results below were read from executions during this remediation, not copied from earlier claims.

### Backend canonical rerun

```text
python -m pytest tests/unit tests/security -q --disable-warnings
8958 passed, 8 skipped, 1453 warnings in 373.85s (0:06:13)
```

The preceding full run found two test-contract issues: the new backup was absent from the explicit RLS table inventory, and an already-collected summary assertion still expected strings. Both were corrected to enforce the current invariants, then the full suite above was rerun. Skips and warnings were not represented as passes.

```text
ruff check app/ --select F --extend-ignore F401,F841
All checks passed!

alembic heads
0046_qa_data_contracts (head)

rls_acquire_inventory.py --fail-on-needs-tenant
total=445, ok=398, needs_tenant=0, needs_review=47
GATE PASS: 0 needs_tenant site(s)
```

The 47 review entries remain; a green static inventory is not proof that every delegated SQL path is safe.

### Real-database verification

```text
test_qa_contracts_postgres.py
1 passed in 2.65s
```

Ran against an isolated local PostgreSQL 16 instance on loopback port 55439, not production. The temporary server was stopped afterward; evidence files were retained.

### Frontend and Admin

- Talk-Leee: final integrated typecheck and lint passed, followed by the complete test suite after the shared-reader change.
- The final shared-reader regression run passed all five selected tests before the canonical run.
- Admin: lint passed, all 13 tests passed, production build succeeded.
- React test `act(...)` warnings were observed; no claim of warning-free frontend tests.
- No real-browser microphone session, mobile visual matrix or production browser verification was performed in this implementation turn.

```text
npm run typecheck && npm run lint && npm test
tests 480
pass 478
fail 0
skipped 2
duration_ms 114733.8462
```

Final `git diff --check -- Talk-Leee backend` exited 0.

## Premortem: safeguards and residual risk

| Failure risk | Safeguard / remaining proof |
|---|---|
| Correct SQL accidentally weakens tenant ownership | Original predicates preserved; real database rejects a mismatched tenant update |
| Billing repair changes entitlement | Read-only quota-source consolidation; no allocation rewrite |
| A stray yes saves the wrong address | Complete matching readback still required; negative cases tested |
| Agent claims unavailable delivery | Capability instructions on plain streaming plus pre-TTS checks; no fake executors enabled |
| Provider retries create excessive cost or expose PII | Two attempts maximum per invocation, explicit timeout, sanitized logs; durable cross-request failure tracking still open |
| Noise keeps a call alive forever | Acoustic activity refreshes nudge clock only; hangup policy preserved |
| Transcript cleanup loses diagnostic evidence | Filter at presentation boundary; raw backend events retained |
| Migration destroys backups or recordings | No deletes/moves; conditional backup protection and metadata-only update; local real-DB test |
| Unit checks hide deployment failure | No live claim; privileged deploy and real-call acceptance remain separate gates |

## Production blockers and not-done

1. **Nothing deployed, committed or pushed.** This turn's code lives in the isolated worktree. Vercel and the server do not have these changes.
2. **Privileged deployment is blocked in the current SSH session.** `sudo -n true` returned `sudo: a password is required`. No password was written into a file or searched for in unrelated private data. Use the supported interactive deployment path; do not bypass drain/config/migration gates.
3. **Synthetic monitoring remains unresolved.** The read-only synthetic config check returned `config is not readable`; the timer was previously enabled but inactive. Old manual originate acceptance logs are not proof of a current completed probe. An approved probe target and privileged configuration access are needed before activation.
4. **Historical dialer-job reconciliation is pending.** Do not blindly retry the known completed call to make a queue row look healthy.
5. **Persistent summary-failure status and retry exhaustion across requests remain open.** The schema and per-invocation recovery fixes are not a full job-state redesign.
6. **Recording restore/retention is not proven.** Local storage remains local; no object-store migration or restore exercise occurred.
7. **Real inbound, outbound and browser Test Agent acceptance remains pending.** Specifically verify a sustained conversation, natural pauses, the observed email confirmation, persisted capture, transcript, summary, correct outcome and usage. The reported browser three-minute drop is still not reproduced.
8. **No external action delivery was enabled or exercised.** Connected Gmail UI does not prove voice email execution. Transfer success/failure cannot be declared tested here.
9. **Audio attachments were not acoustically analyzed.** No claims about audible quality or carrier attribution are made.
10. Full tests generated a line-ending-only change in `telephony/deploy/keepalived/notify.sh` and a tracked Python bytecode change in the isolated tree. They are test artifacts, not intended remediation files, and must not be included in a release commit. No shared telephony configuration was edited.

## Release acceptance checklist

1. Review and commit only named remediation source/test/migration files; omit generated telephony artifacts and reports if release policy requires.
2. Refresh against origin/main and resolve any new migration-head conflict without rewriting another agent's work; rerun canonical gates after integration.
3. Deploy through the supported script with a frozen SHA, privileged operator, zero-session drain and rollback available. Apply migration through the migration unit, not manually.
4. Verify server commit, gateway readiness, schema head, service PIDs/status and frontend deployment identity.
5. Confirm live billing surfaces agree and backup RLS is active without exposing tenant data.
6. Replay controlled browser/outbound/inbound conversations with approved destinations. Check capture, silence behavior, honest action limitations, canonical transcript, summary, recording playback and accounting.
7. Reconcile the historical job only using durable evidence; verify no duplicate dial is scheduled.
8. Activate the approved synthetic monitor and prove both a completed probe and alert delivery.

Until these operational checks pass, the honest status is **locally tested remediation, not fully resolved in production**.
