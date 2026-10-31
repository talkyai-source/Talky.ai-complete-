# Feature QA remediation — 17 September 2026

Implementation: `C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-qa-fixes-20260916`, based on freshly fetched origin/main `88ebc8f3`. Existing remediation changes are retained. Shared checkout edits are not being merged or overwritten. No subagents.

| Area | Scope | Done condition |
|---|---|---|
| Billing, SQL update, email readback, summary contract | Review/re-test existing QA remediation in backend and matching frontend readers | Previously failing reproductions pass; full backend suite and frontend gates complete |
| DID display/filter | dashboard-api list/detail, call-panels, call-history filter | Outbound targets/test markers never become inbound DIDs; inbound canonical DID survives |
| Answered totals | Call History classifier and its consumer | Ended+answered counted; completed-without-outcome not invented as answered; failures/voicemail remain distinct |
| Inbound default persona | Guided and detailed create forms, shared direction-aware persona helper | Fresh inbound defaults to receptionist; outbound default unchanged; explicit saved choices preserved |
| Health badge | Existing normalization/loading fix | Healthy/ok, loading, degradation and error contracts tested |
| Backup isolation | Existing additive migration | Canonical policy tests and one Alembic head; production application separately reported |
| Stale job | Trace terminal completion/recovery ownership, prepare narrowly guarded reconciliation if safe | No new origination, no fabricated attempt/outcome; refusal on incomplete or changed evidence |
| Synthetic monitoring | Inspect configuration and supported deployment path | Validated synthetic config and active timer proof; do not enable calls against an unapproved target |
| DID ownership | Retain ownership guard; clarify conflict | Audited reassignment only after owner decision; never auto-steal a route |
| Acceptance | Canonical tests and report | Fresh output, explicit local/live boundary, remaining operational steps |

Premortem: a broad DID fallback can relabel outbound contacts; a status-only classifier invents answers; a persona default must not overwrite retained drafts; quota display must not mutate entitlement; a stale job must not redial after a prior answer; enabling a timer can place paid calls; schema/code release order must be preserved.

## Implemented in this pass

All paths below are relative to the isolated implementation worktree above, not the shared checkout.

| Root cause | Root-cause change | Regression evidence |
|---|---|---|
| Both API adapters and the DID filter treated every destination as a DID | `Talk-Leee/src/lib/call-presentation.ts:8` requires explicit inbound direction; list/detail adapters and option/filter consumers use it | Actual list/detail transport fixtures and filter tests retain inbound DID, reject outbound destinations and browser-test markers |
| Answered totals inferred an answer from terminal `completed` status and missed `ended` + answered outcome | `call-presentation.ts:16` uses explicit answer outcomes and preserves failed/voicemail distinction; Call History uses this classifier | Ended/answered, completed/unknown, contradictory failed outcome, voicemail, legacy explicit answer and goal outcomes |
| Both new-campaign forms selected outbound lead generation regardless of direction | `campaign-personas.ts:4` provides a direction-aware default; both forms use it; persona descriptions respect direction | Inbound receptionist, outbound lead generation, existing draft/edit choice retained, incoming-enquiry sales copy |
| All inbound HTTP 409 errors showed stale-edit/reload advice, including DID ownership conflicts | `lib/inbound/inbound-types.ts:163` provides assignment-specific guidance; form consumes it before generic conflict handling | Ownership conflict explains audited reassignment; other codes keep their existing handling. No owner identity exposed |

The DID error message does not change assignment ownership or bypass its backend guard. That requires an explicit ownership decision.

## Existing local repairs retained and re-tested

Billing reads use tenant context and the canonical allocation source; numbered SQL arguments are shifted in one pass; full email readback is recognized without accepting wrong/incomplete addresses; summary structured output declares the correct object schema. Their targeted regression set passed **24 tests in 3.01s** this turn. These repairs were already present locally, not newly written today and not proven deployed.

Other retained changes include summary/reminder durable claims, SMS uncertainty handling, optional-event savepoints, current credential reads, transcript presentation, honest unavailable-action responses, and additive migrations 0046/0047. Their complete verification is recorded below when finished; older report totals are not used as today's evidence.

## Fail-before/pass-after evidence

- Deployed-source contract probe: **3 failures in 1.66s** (SQL binding, email readback, summary schema), before using the local remediation.
- New UI regression run before implementation: **12 passed / 9 failed**. The actual filter and adapter reproduced wrong DID values; remaining failures were absent helper exports, not execution of the old inline classifier. The preceding QA source probe separately established that classifier's behavior.
- Targeted UI after fixes: **25 passed / 0 failed**, 5838 ms.
- DID-message helper before implementation: **2 failed**, missing export; the old incorrect message was traced directly in the form catch branch.
- DID-message and existing state parity after implementation: **10 passed / 0 failed**, 2465 ms.
- First canonical frontend run, before the final DID-message edit: **487 passed / 2 skipped / 0 failed**, 157092 ms; typecheck/lint exit 0. A final rerun is required below.

## Operational boundaries / not done

- No production write, migration, restart, outbound send, commit, push or deployment in this fix pass.
- DID remains assigned to its existing account. Asked the user whether to retain it or arrange an audited reassignment; no ownership decision received.
- Synthetic timer was enabled but inactive. The SSH account cannot read `/etc/talky/inbound-synthetic.env` through its protected parent directory; passwordless sudo is unavailable. This is **not** evidence that the file is missing. Configuration validation and activation remain blocked; scheduled paid calls were not enabled against an unverified target.
- The historical terminal-call/retry-job inconsistency is not repaired by changing SQL alone. No direct status rewrite or replay was attempted: completion also has attempt ownership, settlement and retry semantics. A controlled reconciliation remains outstanding.
- Migrations must precede starting the new summary/reminder code. Test fixtures are not a production-clone migration rehearsal.
- No new real inbound/outbound call or live browser acceptance of these unshipped changes. No new callback/email/form/transfer executor was implemented; unavailable operations stay fail-closed.
- RLS inventory reports zero `needs_tenant` sites, but 47 `needs_review` sites remain; passing the narrow gate does not certify all of them.

## Fresh integrated verification

- Canonical backend/security: **8969 passed, 8 skipped, 1453 warnings in 344.24s (0:05:44)**, exit 0. Evidence: `evidence/2026-09-17-backend-fixes.log` and `.xml` (individual test cases).
- Real PostgreSQL plus targeted provider/safety regression set: **30 passed in 5.08s**, exit 0. Evidence: `evidence/2026-09-17-database-fixes.log`. This executes the two integration files plus `test_reminder_delivery_contract.py` and `test_backend_followup_safety.py`. No live external provider calls.
- Used a fresh local PostgreSQL 16 cluster at `C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-qa-postgres-20260917/data`, loopback port 55439, database `talky_qa_20260917`. Stopped after verification; evidence data retained. An earlier attempt to reopen yesterday's cluster could not authenticate using the assumed role, so that cluster was stopped without changing its data and replaced by the new disposable instance.
- Ruff canonical F gate: **All checks passed!**
- Alembic: **0047_backend_work_claims (head)**, one head.
- `git diff --check -- Talk-Leee backend`: exit 0.
- Implementation HEAD and freshly fetched origin/main: **88ebc8f338d4c50e9d7456239a28343afd20561b** plus uncommitted named remediation files; no claim that this equals the deployed release.
- Admin canonical checks: lint exit 0; **13 tests passed, 0 failed**; production build exit 0, 1764 modules transformed. Evidence: `evidence/2026-09-17-admin-fixes.log`.
- Final Talk-Leee canonical checks, including the DID-message correction: typecheck exit 0; lint exit 0; **491 tests, 489 passed, 2 skipped, 0 failed**, 165426 ms. Evidence: `evidence/2026-09-17-frontend-fixes-final.log`. React act/test-environment warnings are retained in the log; this is not a warning-free claim. The two skipped cases require the frontend database test environment.

## Outcome

The identified local code corrections are implemented and the canonical suites passed this turn. New changes in this pass are DID presentation/filtering, answered classification, direction-aware campaign persona defaults/copy and truthful DID conflict guidance. Prior backend and presentation remediation was retained and re-tested, not represented as newly implemented today.

**Not a live-completion claim:** no deployment or historical production-data repair occurred. DID ownership, the protected synthetic-monitor configuration/activation and the stale retry-job reconciliation remain open. Real-call and post-deploy browser acceptance remain necessary after an authorized, migration-ordered release.
