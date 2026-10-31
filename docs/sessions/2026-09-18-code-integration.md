# Coding integration and verification — 18 September 2026

## Outcome and scope

The retained QA and PBX implementation has been integrated into a new worktree based on fetched origin/main. Additional reproduced defects in asynchronous database access, connector disconnect, storage containment, migration ordering and PBX startup dependencies were corrected there. This is a local candidate, not a deployment or proof that all customer workflows work live.

- Worktree: `C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918`
- Branch: `codex/release-code-integration-20260918`
- Base commit: `db8b1381863b3a414741fc5a3d5e07cd33ef9e20`
- Application edits remain uncommitted. Nothing was pushed or deployed in this coding pass.
- The shared application's files and retained worktrees were not overwritten. This report is placed in the shared repository for visibility.
- Named source/test/release inventory: 168 files, all matching their recorded SHA-256 hashes at verification. The inventory is `docs/sessions/2026-09-18-code-source-manifest.json` in the integration worktree. It is NOT a deployment approval or drain manifest.

## Retained work integrated

### Backend QA implementation

The retained changes cover tenant-aware billing/quota reads and numbered SQL parameters; email readback validation; honest responses for unavailable actions; structured summary schema and durable summary claims/retries; reminder ownership and uncertain SMS delivery; current credential resolution; optional event savepoints; recording metadata; and transcript presentation.

These are integrated changes, not a claim that each problem was newly discovered today. Their tests were run together against the integration candidate, rather than relying on green results from separate old branches.

### Frontend implementation

Integrated inbound DID display/filtering, answered-call classification, direction-aware campaign persona defaults, DID reassignment conflict guidance, PBX capability and routing presentation, and health/billing presentation. Existing main-branch inbound draft retention and notification changes were preserved.

### PBX implementation

Integrated explicit connection grants, outbound-default routing and caller-ID authorization; fail-closed origination boundaries; SIP account/DID/default distinctions; durable reconciliation work; generated PJSIP and network-policy validation; egress enforcement units; and supported deployment checks.

Registration is not treated as tenant authorization. A configured connection, an authorized tenant grant, a callable DID and an outbound default are distinct concepts. Runtime acceptance of this design still requires the Linux/server checks below.

## New root-cause fixes and evidence

### 1. Conflicting migration histories

Reproduction: the integrated histories produced two Alembic heads. A test and the actual heads command exposed the conflict.

Fix: retained QA migrations 0046 and 0047, then renumbered the unpublished PBX migrations to 0048 and 0049 with explicit sequential parents. Updated the PBX integration tests accordingly.

Result: one head, `0049_durable_pbx_reconcile`. Real PostgreSQL integration tests passed. This does not substitute for replaying the migrations against a restored production database.

### 2. Blocking database adapter in async request paths

Reproduction: four simulated 50 ms queries delayed an unrelated timer by 243.28 ms through the old awaited public execute path; the genuinely asynchronous control delayed it by 15.25 ms. Awaiting an eagerly evaluated synchronous result did not make its execution non-blocking.

Fix: introduced `execute_query()` for asynchronous consumers. Native query builders await their coroutine directly and use the supplied connection pool. Compatibility clients run through a worker thread. Synchronous `.execute()` remains eager so existing ignored-result writes are not silently lost.

The shared connection helper establishes tenant scope transactionally, avoids implicit bypass for missing scope, and releases pooled connections on completion/cancellation. Synchronous worker-loop execution does not reuse a pool owned by a different event loop.

Migrated the asynchronous consumers and identified async-to-sync database helper calls. The source guard prevents direct eager execution in the covered async consumers. The helper inventory found no remaining direct edges of the identified pattern; this is not a proof about every dynamically dispatched call in the application.

Evidence includes scheduling, ignored-write and cancellation tests plus real pooled-connection scope/reset/cancellation tests. Inventory/rewrite scripts are archived under `scripts/archive/` in the candidate.

### 3. Connector disconnect reporting success on database failure

Reproduction: lookup errors and delete errors could still yield a successful disconnect response; one path reported one removal after failed deletes.

Fix: both customer disconnect routes now use one tenant-scoped transaction. It locks the selected connector rows, deletes dependent accounts, deletes the parents and checks the exact returned ID set. Either delete failing rolls the transaction back. Failure produces a sanitized 503 instead of success; the ID route preserves a not-found response for an absent owned connector. Repeated removal by card can correctly return zero.

Evidence: route failure tests and real PostgreSQL tests for scoping, repeatability and rollback on either child or parent deletion failure.

Boundary: this removes local connector credentials. It does not claim external-provider token revocation. Admin/revocation-service error-envelope behavior was not comprehensively certified in this pass.

### 4. Storage containment used a string prefix

Reproduction: `recordings/../recordings-sibling/proof.wav` could pass the old prefix comparison.

Fix: compare resolved path components with `is_relative_to()` instead of string prefixes. Tests reject the sibling escape and accept valid nested paths.

This proves a helper-level defect and correction, not that a public endpoint exploit was demonstrated.

### 5. Test fixture prevented the event loop from yielding

Reproduction: an isolated silence-monitor test passed only after 34.12 seconds and produced a faulthandler trace in the loop, despite its short intended deadline. The fixture replaced sleep with a non-yielding mock.

Fix: use the existing immediate-but-yielding sleep helper. Production timing and behavioral assertions were not relaxed.

Result: the six focused tests passed in 11.27 seconds. A legacy direction-guard structural assertion was also updated to recognize its exact awaited worker-thread call; behavioral direction/race tests remain in place and all 16 focused tests passed.

### 6. Asterisk startup did not require successful egress setup

Reproduction: two new tests failed because the Asterisk dependency drop-in and its installer wiring did not exist. Ordering the reconciliation service alone did not constrain Asterisk startup.

Fix: a repository-owned Asterisk drop-in requires and orders startup after `talky-pbx-egress.service`. The supported installer publishes that drop-in before reloading systemd, without replacing the distribution unit or unrelated overrides.

Rationale: systemd requirement and ordering dependencies serve different purposes; both are needed for this startup gate. See the [primary systemd unit documentation](https://github.com/systemd/systemd/blob/main/man/systemd.unit.xml).

Evidence: four boot/reconciliation tests passed; updated release scripts pass Bash syntax checks. Actual systemd, nftables and Asterisk behavior has not been exercised on Linux in this coding pass.

Operational consequence: dependency stop/restart propagation can affect Asterisk. Installation/restart must stay behind the supported maintenance and zero-session drain gates. Rollback must restore compatible unit/drop-in/configuration state, not just an old code commit with a missing egress service.

## Verification ledger

| Check | Observed result |
| --- | --- |
| Initial complete backend/security run | 9,039 passed; 8 skipped; 1,453 warnings; 396.86 seconds |
| Final backend/security run including two added PBX boot tests | 9,041 passed; 8 skipped; 0 failed; 1,453 warnings; 381.46 seconds; exit 0 |
| Frontend typecheck | Exit 0 |
| Frontend lint | Exit 0 |
| Frontend test suite | 501 total: 499 passed, 2 skipped, 0 failed |
| Admin lint | Exit 0 |
| Admin tests | 13 passed, 0 failed |
| Admin production build | Exit 0; 1,764 modules built |
| PostgreSQL integration | 31 passed in 19.12 seconds |
| Ruff F checks, existing F401/F841 exclusions | All checks passed |
| Alembic heads | Exactly one: 0049_durable_pbx_reconcile |
| RLS acquisition inventory | 453 total; 404 OK; 0 needs-tenant; 49 needs-review |
| Release/install/reconcile shell syntax | All three checks exited 0 |
| Source manifest hash verification | 168 entries; 0 mismatches |
| Whitespace diff check | Exit 0 |

PostgreSQL was an isolated local PostgreSQL 16 instance bound to loopback. Its test schemas/databases were disposable, not production data. The instance was stopped cleanly after verification.

The PostgreSQL run covered `test_release_adapter_disconnect.py`, `test_pbx_connection_authority.py`, `test_backend_followup_regressions.py` and `test_qa_contracts_postgres.py`.

Backend evidence resides in the candidate's `output/backend-canonical.log` and XML, `output/backend-release-final.log` and XML, and `output/postgres-final.log`. Frontend/Admin results were read from the completed command output. Their full console output was not separately saved to a file.

Interrupted diagnostic runs are not counted as passes. One diagnostic full run failed on the outdated structural direction-guard assertion before it was corrected. The first new PostgreSQL test run failed because the new assertion expected a string rather than the existing UUID return type; that assertion was corrected and all 31 tests were rerun together. Test totals above are individual runs, not accumulated across retries.

## Premortem and remaining acceptance gates

1. **Mixed old/new release:** do not deploy the frontend ahead of matching backend contracts. Nothing has been pushed to auto-deploying main.
2. **Migration surprise:** one head is necessary, not sufficient. Rehearse on a production-shaped restored database, including existing-data constraints and lock time.
3. **Unintended tenant routing:** explicitly review connection grants, DID ownership and outbound defaults before rollout. Do not infer them from SIP registration or carrier account names.
4. **PBX restart interrupts calls:** prove zero sessions and use the supported maintenance/drain path before installation, reconciliation or dependency restart.
5. **Firewall/startup lockout:** validate generated policy and systemd dependencies on the actual Linux host; keep a compatible configuration and unit rollback immediately available.
6. **Code passes but caller hears silence:** real inbound greeting, both transfer outcomes, outbound regression and audio artifacts remain mandatory. Unit tests cannot certify the carrier/audio path.
7. **Accounting appears healthy despite incomplete records:** verify recording, transcript, summary and billing against the same real call IDs after deployment.
8. **Review debt hidden by a green gate:** 49 RLS acquisitions still require review. The zero-needs-tenant result is not a claim that all SQL is audited.
9. **Browser behavior missed:** no new browser/visual acceptance run was performed here; typecheck and component tests do not prove every layout or interaction.
10. **False completion:** no fresh production registrations, running gateway SHA, live route, real transfer, consent recording or end-to-end billing evidence was collected in this coding pass. The broader deployment goal remains incomplete.

## Packaging state

Coding/integration and the listed local verification gates for this batch are complete. The final backend run includes the last source change, the PBX startup dependency. No application code changed after that run began. This completion does not include the remaining acceptance gates above.

The candidate source/test/release changes are listed explicitly in the hash manifest. Generated bytecode, local test output and scratch artifacts are not application changes to commit. A tracked diagnostic bytecode file was regenerated by tests and is excluded from that manifest.

No commit, push, migration on production, service restart, DID reassignment, customer call or provider credential change was performed in this pass. The next release must freeze a committed candidate, rerun the appropriate release gates and preserve an immediately usable compatible rollback.
