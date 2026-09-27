# Service stability audit and remediation — 18 September 2026

## Document status and navigation

This expanded report describes the completed stability engineering pass and its saved evidence. The report-only follow-up re-read the logs/XML and verified all 180 candidate-file hashes; it did **not** rerun the application suites or take a new production snapshot. Production observations below are historical snapshots from that engineering pass, not continuous monitoring.

- [Executive outcome](#outcome)
- [Seven defects and their fixes](#confirmed-defects-and-fixes)
- [Live service observations](#live-service-evidence)
- [Two-minute call investigation](#two-minute-call-question)
- [Verification totals and failed attempts](#verification)
- [Remaining risks](#premortem--what-remains)
- [Service-by-service coverage](#service-by-service-coverage-matrix)
- [Implementation contracts and connected files](#implementation-contracts-and-connected-files)
- [Focused test-case evidence](#focused-test-case-evidence)
- [All skipped checks](#skipped-checks-and-their-consequences)
- [Release acceptance checklist](#release-acceptance-checklist)
- [Evidence locations and fingerprints](#evidence-locations-and-fingerprints)
- [Complete backend test ledger — every one of the 9,061 cases](./2026-09-18-service-stability-backend-tests.md)

The full test ledger is separate so the engineering conclusions remain readable. It records exact test identities, execution outcomes and per-case durations from JUnit. It does not invent a prose description of what each test proves.

## Outcome

Seven concrete failure areas were identified and corrected in the local integration candidate. This is not a declaration that every service is production-ready: deployment, Linux/PBX acceptance, real-call testing and part of the database review remain open.

Candidate: `C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918`, branch `codex/release-code-integration-20260918`, based on `db8b1381863b3a414741fc5a3d5e07cd33ef9e20`.

Production was inspected read-only. No services were restarted, no production records/configuration were changed, and no customer calls or provider requests were initiated. No commit or push in this pass. Previous integration work was preserved.

## Confirmed defects and fixes

### 1. Tenant helper inherited platform bypass

- Root cause: `acquire_with_tenant()` set the tenant identifier but did not disable an already-enabled bypass setting. A role/session default or outer transaction could therefore keep the bypass branch of the RLS policy enabled.
- Reproduction: focused unit test failed; a real PostgreSQL connection with session-level bypass enabled also returned `true` inside the supposedly tenant-scoped helper.
- Fix: tenant scope explicitly executes `SET LOCAL app.bypass_rls = 'off'`. Platform scope still explicitly enables bypass. Transaction exit restores the outer setting.
- Source: `backend/app/core/db_utils.py:100`.
- Tests: `test_tenant_helper_bypass_reset.py`; `test_tenant_helper_masks_session_level_bypass_in_real_postgres`.
- Boundary: this establishes the correct setting. It is not evidence that all historical queries have correct ownership predicates.

### 2. Call-guard policy helpers received unscoped database connections

- Root cause: `_check_rate_limit()` and `_check_concurrency()` acquired raw connections. Passing `tenant_id` to their helper did not set the database RLS context used by the policy/lease queries.
- Reproduction: both delegated paths observed a missing tenant setting on real PostgreSQL connections.
- Fix: both use `acquire_with_tenant(pool, tenant_id)` before the delegated operation.
- Source: `backend/app/domain/services/call_guard.py:737` and `:768`.
- Test: `test_call_guard_scopes_delegated_policy_queries` for both paths.
- Rollout consequence: previously invisible tenant policy/count rows can become visible. Review actual allocations/concurrency/rate settings before deployment; do not disguise newly enforced limits as a routing outage.

### 3. Campaign activity events used bare connections

- Root cause: start/pause/stop/delete event writes acquired fresh connections without tenant context. The optional emitter does not establish the caller's tenant setting; it logs and swallows write errors.
- Reproduction: invoking the pause route with a real pool showed no tenant context at its event emitter.
- Fix: all four sibling event-connection acquisitions now establish tenant context.
- Source: `backend/app/api/v1/endpoints/campaigns.py`.
- Test: `test_pause_campaign_scopes_activity_event_connection`; existing campaign suites remain part of full verification.
- Limitation: the added behavioral proof directly exercises pause, not four separate live browser actions.

### 4. Admin connector lifecycle returned misleading success/errors

- Root cause: database error envelopes were ignored. Revocation returned success after a failed token write; reconnect/detail treated failed lookups as not-found. Parent and credential writes were independent, and stale refreshes could reactivate a connector after revocation.
- Reproduction: route tests produced false success and incorrect 404 responses. New PostgreSQL lifecycle tests initially failed because no atomic transition implementation existed.
- Fix: explicit read-error checks; sanitized 503 responses for storage failures; tenant-scoped transactions for local revocation and refresh persistence. Parent rows are locked before credentials. Refresh requires an allowed current state and the original encrypted refresh-token snapshot. Changed state yields a conflict rather than success.
- Provider refresh occurs outside the database transaction and is bounded to 20 seconds. Database acquisition and statements are also bounded. No database lock is held while waiting for provider I/O.
- Source: `backend/app/domain/services/connector_lifecycle.py`; `backend/app/api/v1/endpoints/admin/connectors.py`.
- Tests cover repeatable revocation, wrong tenant, stale refresh snapshot, rollback on either table failure, and revocation winning during a provider request.
- The response explicitly says **local** access was revoked. It does not claim the external provider grant was revoked.
- Design reference: [PostgreSQL row-lock semantics](https://www.postgresql.org/docs/17/explicit-locking.html). Locks serialize the local commit; they do not make an external OAuth provider transaction atomic with PostgreSQL.

### 5. Runtime connector refresh could return credentials after revocation

- Root cause: the shared runtime resolver wrote refreshed credentials by IDs/tenant alone. If revocation happened during the provider request, the write still matched and the resolver returned the new access token.
- Reproduction: a real PostgreSQL test revoked the account inside the simulated provider request. The old resolver still returned refreshed credentials.
- Fix: token write-back additionally requires account status `active`. A concurrent revoke makes the update match no rows, and the existing durable-write check refuses to return the token.
- Source: `backend/app/services/connector_resolver.py:136`.
- Test: `test_runtime_refresh_does_not_return_credentials_after_revoke`.
- Test-harness note: the first attempt failed for an unrelated timestamp-binding reason because the adapter introspects `public` while the fixture uses an isolated schema. The fixture was corrected to read that schema's real catalog metadata; the test then reproduced the actual race before the production fix. The passing result is not based on the unrelated error.
- Boundary: an external action already dispatched before revocation is not retroactively cancelled. Fully coordinated parallel provider-token rotation remains a separate review concern.

### 6. Production readiness tolerated loss of shared Redis

- Root cause: deep readiness reported ready when Redis was down or absent, citing an in-memory fallback. The actual dialer queue and worker pub/sub use Redis; production inbound explicitly requires cross-process Redis ownership.
- Reproduction: three new production-mode tests returned ready for Redis failure, timeout and absence.
- Fix: those conditions now return not-ready/503 in production. Existing optional-Redis development behavior remains unchanged. Liveness is unchanged; this is not an instruction to restart the process when Redis fails.
- Source: `backend/app/api/v1/endpoints/health.py`.
- Test file: `test_health_deep_probe.py`, 10 focused tests passed.
- Operational boundary: a monitor/load balancer must actually consume this endpoint. This change alone does not deploy or configure that monitoring.

### 7. Optional lease cleanup poisoned the surrounding transaction

- Root cause: `get_status()` caught a failed cleanup query and continued, but PostgreSQL had already aborted the transaction. The following authoritative live-count query then failed too.
- Reproduction: a deliberate SQL failure during cleanup caused `InFailedSQLTransactionError` on the subsequent real PostgreSQL count.
- Fix: optional cleanup runs inside a nested transaction/savepoint. Failure rolls back that operation before the count proceeds; it does not invent a zero count or permit a call without checking.
- Source: `backend/app/domain/services/telephony_concurrency_limiter.py:581`.
- Test: `test_optional_stale_lease_cleanup_does_not_abort_status_transaction`. The focused concurrency/integration set passed 23 tests.

## Live service evidence

| Surface | Observed, read-only | What this does not prove |
| --- | --- | --- |
| API, voice worker, dialer, reminder, gateway, Asterisk | All six active/running; reported restart counters 0 and last main exit status 0 | Long-running workload correctness or carrier behavior |
| API health/capacity | Healthy, initialized, ready, zero active sessions, not draining | Complete dependency or audio readiness |
| Deep dependencies | Database OK, Redis OK | Restore/reconnect/failover behavior |
| Worker health | Dialer, voice and reminder heartbeat ages healthy | Provider success or correct content of responses |
| C++ gateway | Ready; protocol 2; PCMU; build SHA `f9fccd893fa72290e87381293531d6affb76dd98` | Deployment of the latest candidate or new sanitizer/soak evidence |
| Backup, cleanup, healthwatch | Last service results success, exit status 0 | A successful restore or an exercised alert delivery |
| Disk/memory snapshot | Root filesystem 59% used; approximately 2.45 GB available RAM | Capacity under concurrent calling load |
| SIP registrations | Not readable through the current non-sudo Asterisk CLI access | No conclusion about registration success or failure |

Confirmed live deployment gaps:

1. `talky-pbx-reconcile.timer` and `talky-pbx-egress.service` are not installed.
2. `talky-inbound-synthetic.timer` is enabled but inactive, with no next run shown.
3. `talky-migrate.service` is loaded/linked and inactive. Inactivity is normal for an idle one-shot; this alone is not a migration fault.
4. The local candidate has not been installed through the supported release path.

## Two-minute call question

Read-only production aggregates for the last seven days showed 5 real outbound ended calls; 3 recorded durations above 120 seconds. This disproves a universal two-minute cutoff across all outbound calls. It does not diagnose the user's particular dropped call or certify the accuracy of every historical duration.

The same query saw 11 inbound ended calls and 1 inbound failed call, none above 120 seconds. Reasons and media were not inspected, so no causal inference is made from those shorter inbound calls.

No rows older than five hours were found in the checked states: initiated, ringing, answered, in_progress and termination_pending. This is a bounded stale-row check, not proof of every possible terminal-state discrepancy.

The 120-second stuck-job timer is distinct from call-duration ceilings. Existing tests cover answered ten-minute calls surviving the short pre-answer timeout. Call limits were not changed in this pass.

Aggregate-only probe: `scripts/archive/service_stability_readonly_probe.py`. It executes inside a read-only transaction with a statement timeout and prints no phone numbers, credentials or transcripts.

## Verification

| Gate | Result |
| --- | --- |
| Final full backend/security | 9,053 passed, 8 skipped, 0 failed; 1,454 warnings; 269.69 seconds; exit 0 |
| Frontend typecheck/lint/test | Exit 0; 501 tests: 499 passed, 2 skipped, 0 failed; 450,691 ms |
| Admin lint/test/build | Exit 0; 13 passed; 1,764 modules built |
| Final fresh PostgreSQL integration | 42 passed in 13.88 seconds |
| Recheck of initial failed backend test files | 95 passed in 24.24 seconds |
| RLS invariant test file | 1,118 passed in 78.04 seconds |
| Ruff F checks, existing F401/F841 exclusions | All checks passed |
| Alembic | Exactly one head: 0049_durable_pbx_reconcile |
| Whitespace check | Exit 0 |

Failed runs are retained, not counted as successes:

- Initial full backend: 6 failed, 9,044 passed, 8 skipped. One expectation was updated to require the newly explicit bypass reset. Five other failures involved timing/subprocess deadlines; their source and limits were not relaxed, and the affected files passed after the concurrent frontend workload ended.
- A Windows resource exception was observed in a concurrent Python import/WMI path. Do not interpret that run as a clean performance benchmark.
- First full PostgreSQL rerun: 40 passed, 1 failed because a fixture correctly refused a reused database with existing public tables. Fresh databases were used for subsequent runs. Final 42-test run includes the lease-savepoint change.

Evidence files are under the candidate's `output/`: `service-stability-backend.log/.xml`, `service-stability-backend-final.log/.xml`, `service-stability-frontend.log`, `service-stability-admin.log`, `service-stability-recheck.log`, and `service-stability-postgres-complete.log`.

## Premortem / what remains

1. **Release ordering:** commit only the named candidate changes; freeze a SHA and coordinate backend/frontend deployment. Pushing frontend main alone can publish UI contracts before the backend is ready.
2. **Tenant behavior changes:** inspect policy values and allocations before rollout. Correcting invisible reads can correctly block calls that were previously allowed under defaults or missing counts.
3. **PBX boot/configuration:** install and verify the prepared reconciliation/egress units and Asterisk drop-in under maintenance/drain gates. Confirm generated configuration and every required SIP registration.
4. **Rollback compatibility:** retain binary, units, configuration and database compatibility evidence. A code-only rollback that removes the egress service but leaves Asterisk requiring it is not a usable rollback.
5. **Real-call certification:** inbound greeting, transfer success/failure, recording consent, transcript, summary, billing and sustained outbound regression remain unverified on the candidate.
6. **Synthetic monitoring:** configure an approved route/destination and activate the timer through the supported release. Do not silently originate calls or guess a recipient.
7. **Database review:** inventory now reports 455 acquisitions: 409 OK, 0 needs-tenant, 46 needs-review. Six bare acquisitions were replaced; only three changed the needs-review total because the static analysis scores some entire functions as OK. Remaining flags include intentional admin/auth/platform work, non-database TTS pools, and delegated SQL. They have not all been certified or allowlisted.
8. **Provider resilience:** no live STT/LLM/TTS outage, throttling, reconnect, silence or long-call acceptance exercise in this pass. Healthy processes and green unit tests are not equivalent to usable voice conversations.
9. **Credential legacy debt:** no application call sites were found for the old TokenRotationService/ConnectorRevocationService outside their own modules. They were not rewritten or declared safe; their remaining behavior must be considered before reconnecting them to runtime.
10. **Operational resilience:** backup restore, Redis durability/failover and alert delivery still need exercised evidence. Last-job success is insufficient.
11. **Gateway verification:** C++ source was not changed in this pass. No fresh Linux compiler/sanitizer/concurrency test or gateway installation was performed here.
12. **Browser QA:** typecheck, lint and component tests passed, but no new browser/visual acceptance sweep was performed.

The updated source/test inventory is the candidate's `docs/sessions/2026-09-18-code-source-manifest.json` (180 named entries). It is an inventory, not a release approval. The full product/deployment goal remains open.

Final verification: all 180 source-manifest hashes matched. No application source changed during the final backend run. The isolated local PostgreSQL instance was stopped cleanly after the integration tests; test data/logs remain available. Frontend/Admin and backend evidence was read from completed commands, not inferred from progress output.

## Service-by-service coverage matrix

“Covered” means the listed observation or test was performed. It does not mean an entire service has passed all possible production scenarios.

| Service or responsibility | Covered in this pass | Change made | Remaining acceptance |
| --- | --- | --- | --- |
| Shared PostgreSQL access | Inherited bypass reproduced with a real connection; tenant context checked at delegated consumers | Explicit bypass reset and missing tenant contexts | Remaining 46 inventory reviews; production data/role verification after deployment |
| Call admission guards | Rate/concurrency helper connections inspected and tested | Tenant-scoped database acquisition | Confirm actual policy values and expected allow/deny decisions on production-shaped data |
| Concurrency accounting | SQL cleanup failure injected inside a real transaction | Savepoint around optional cleanup | Real lease expiry, abandoned call and transfer accounting under load |
| Campaign lifecycle | Pause event context reproduced; four sibling event paths corrected | Scoped event writes | Browser start/pause/stop/delete feedback and live event delivery |
| Connector admin API | False-success and false-not-found reproduced | Checked reads and atomic local transitions | Authenticated browser exercise with an approved connected account |
| Runtime connector resolver | Revoke-during-refresh race reproduced | Conditional write-back only to active accounts | Parallel provider rotation and in-flight external-operation boundaries |
| Readiness endpoint | Production Redis failure, timeout and absence simulated | Production reports not-ready rather than false health | Verify deployed monitor uses deep readiness and does not confuse it with liveness |
| Dialer worker | Running state and heartbeat; existing suite includes live-call reaper protection | Indirect guard/concurrency hardening | Real outbound call, queue retry recovery and concurrent dialer workload |
| Voice worker | Running state and heartbeat | No worker implementation change in this pass | Real browser voice session, provider recovery and sustained audio |
| Reminder worker | Running state and heartbeat; retained reminder tests in the full suite | No new reminder implementation change in this pass | Approved delivery test, restart recovery and uncertain-delivery handling |
| STT/LLM/TTS providers | Existing unit/security suites executed | No new provider/model tuning in this pass | Real account availability, throttling, silence, reconnection, prompt and voice fidelity |
| C++ media gateway | Ready response, build identity, protocol and codec observed | No C++ source change in this pass | Latest supported build installation, Linux tests, packet impairment and sustained audio |
| Asterisk / SIP edge | Service running; current CLI access insufficient for registration inspection | No live config change | Generated/live reconciliation, account-to-DID routing and SIP registration proof |
| PBX reconciliation/egress | Missing deployed units confirmed | Earlier candidate implementation remains pending deployment | Install through supported release; boot/restart/rollback verification |
| Inbound synthetic monitor | Enabled but inactive timer observed | Not activated | Approved route and recipient; executed probe and delivered failure alert |
| Backup/cleanup/healthwatch | Last job results successful | None | Restore drill and alert-delivery proof; verify actual retention behavior |
| Customer frontend | Typecheck, lint and full automated tests completed | No new frontend code in this stability pass | Fresh authenticated browser/visual QA against matching deployed backend |
| Admin frontend | Lint, automated tests and production build completed | No new Admin frontend code in this stability pass | Browser handling of new honest 409/503 responses |

## Implementation contracts and connected files

### A. Tenant context is a database setting, not just a Python argument

Before the fixes, a service could know its tenant while its database connection did not. The relevant chain is:

1. An API/worker invokes a guard or event operation with a tenant identifier.
2. The operation acquires a connection.
3. A downstream helper executes SQL protected by tenant policies.
4. PostgreSQL evaluates the connection's transaction-local settings, not the caller's Python variables.

The correction is at both required boundaries: callers use the tenant helper, and that helper explicitly turns bypass off for tenant scope. Fixing only one endpoint would leave sibling callers exposed. Replacing every platform bypass with tenant scope would also be wrong: legitimate platform-wide operations still exist.

Relevant candidate files:

- [Shared tenant helper](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/core/db_utils.py:61>)
- [Call rate-limit guard](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/domain/services/call_guard.py:737>)
- [Call concurrency guard](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/domain/services/call_guard.py:768>)
- [Campaign lifecycle routes](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/api/v1/endpoints/campaigns.py>)
- [Optional event writer](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/domain/services/event_emitter.py>)

The optional event writer's existing savepoint prevents an event SQL error from undoing surrounding business data. That does not supply tenant context; its caller must still establish the correct scope. The change preserves this separation of responsibilities.

### B. Local revocation must be durable before reporting success

The local revoke operation now has one transaction boundary:

1. Validate tenant and connector identifiers.
2. Acquire a tenant-scoped connection with a five-second acquisition bound.
3. Apply a ten-second statement timeout inside the transaction.
4. Lock the owned connector row.
5. Mark its owned account rows revoked and clear stored access/refresh tokens.
6. Mark the parent connector disconnected and confirm the returned identifier.
7. Commit, then report success.

An exception before commit rolls back both tables. A missing connector is not represented as a successful change. Repeated revocation of an existing already-disconnected connector remains safe.

These timeouts bound individual acquisition/statements; they are not a promise that every entire HTTP request finishes within ten seconds.

Relevant files: [transaction service](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/domain/services/connector_lifecycle.py:12>) and [admin routes](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/api/v1/endpoints/admin/connectors.py>).

### C. Refresh results are provisional until persisted against current state

An OAuth refresh is an external operation. The application cannot roll the provider back with a PostgreSQL transaction.

The admin path therefore:

1. Reads an owned, eligible credential snapshot.
2. Contacts the provider outside database locks, with a twenty-second bound.
3. Rejects a provider result with no access token.
4. Encrypts the returned credentials.
5. Locks the connector and checks that it is still eligible.
6. Updates only the same owned account with an eligible status, no revocation timestamp and the original encrypted refresh-token value.
7. Persists the parent activation/provider configuration in the same local transaction.

The expected-value condition prevents a stale refresh response from blindly replacing newer credentials. It is not a complete distributed scheduling protocol for all possible concurrent provider requests.

The runtime resolver has a narrower correction: its existing checked write-back now also requires active account status. That prevents the reproduced revoke-during-refresh path returning usable credentials. It does not retroactively withdraw an external request that was already sent.

Relevant files: [refresh commit service](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/domain/services/connector_lifecycle.py:38>) and [runtime resolver](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/services/connector_resolver.py:94>).

### D. Readiness and liveness answer different questions

- Liveness asks whether the application process/event loop can respond.
- Capacity readiness asks whether it is draining or full.
- Deep readiness checks dependencies.
- Worker health checks recent worker heartbeats.

The corrected deep probe must report not-ready when production cannot use its shared Redis coordination. This should stop new work through whatever routing/monitoring consumes that signal. It must not be interpreted as permission to restart every process or tear down active calls.

Source: [health endpoints](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/api/v1/endpoints/health.py>).

### E. Optional SQL work needs an error-containment boundary

The lease status path performs optional cleanup before reading authoritative counts. A Python exception handler cannot clear PostgreSQL's failed-transaction state. The nested transaction/savepoint contains the optional failure, allowing the subsequent count query to run.

The fix does not change an unknown count into zero, weaken the capacity gate, or claim that a failed cleanup actually released a lease.

Source: [concurrency status](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/backend/app/domain/services/telephony_concurrency_limiter.py:572>).

## Focused test-case evidence

The following matrix describes the intended assertions in the focused tests. The full execution ledger is authoritative for individual backend unit/security outcomes; PostgreSQL integration results come from their separate saved run.

| Test or case | Failure injected / boundary exercised | Required outcome |
| --- | --- | --- |
| `test_tenant_scope_overrides_inherited_bypass_and_restores_outer_scope` | Tenant helper entered while outer bypass is on | Bypass off inside tenant scope; outer value restored afterward |
| `test_platform_scope_is_still_explicitly_enabled` | Explicit platform scope entered from non-bypass state | Platform bypass enabled only within its scope |
| `test_tenant_helper_masks_session_level_bypass_in_real_postgres` | Real PostgreSQL session-level bypass | Same tenant-scope override/restoration behavior in PostgreSQL |
| `test_call_guard_scopes_delegated_policy_queries[_check_rate_limit]` | Rate helper reads its connection's tenant setting | Setting equals the guard's tenant |
| `test_call_guard_scopes_delegated_policy_queries[_check_concurrency]` | Concurrency helper reads its connection's tenant setting | Setting equals the guard's tenant |
| `test_pause_campaign_scopes_activity_event_connection` | Pause route invokes the event writer | Event connection carries the caller's tenant |
| `test_admin_revoke_does_not_report_success_when_token_write_fails` | Local revocation persistence fails | Sanitized 503; no success response |
| `test_admin_reconnect_distinguishes_failed_lookup_from_missing_connector` | Database error envelope on lookup | 503, not misleading 404 |
| `test_admin_detail_does_not_hide_database_outage_as_not_found` | Detail lookup fails | 503, not missing-connector result |
| `test_admin_list_does_not_hide_database_outage_as_empty_list` | List lookup fails | 503, not healthy empty list |
| `test_revoke_prevents_inflight_refresh_from_resurrecting_credentials` | Refresh commit follows local revocation | State conflict; account remains revoked and tokens remain cleared |
| `test_revoke_rolls_back_on_either_table_failure[connectors]` | Parent update refused by PostgreSQL | Both parent and credential state remain unchanged |
| `test_revoke_rolls_back_on_either_table_failure[connector_accounts]` | Account update refused by PostgreSQL | Both parent and credential state remain unchanged |
| `test_refresh_is_tenant_scoped_and_rejects_stale_token_snapshot` | Wrong tenant and then stale original credential | Both unsafe updates rejected; valid update retained |
| `test_revoke_is_repeatable_and_cannot_touch_other_tenant` | Wrong tenant, followed by repeated owned revocation | No cross-tenant mutation; repeat remains safe |
| `test_runtime_refresh_does_not_return_credentials_after_revoke` | Revocation occurs inside simulated provider refresh | No token returned from an unconfirmed/ineligible write-back |
| `test_production_is_not_ready_without_shared_redis` (three cases) | Redis exception, timeout, or missing client | Production not-ready and HTTP 503 |
| `test_optional_stale_lease_cleanup_does_not_abort_status_transaction` | Cleanup executes deliberately invalid SQL | Count query still runs after savepoint rollback |

Tests use controlled failures, mocks where appropriate and a disposable local PostgreSQL instance. They do not send real OAuth refreshes, place carrier calls, or exercise customer credentials.

## Skipped checks and their consequences

### Backend/security: eight skips

| Exact test identity | Saved skip reason | What remains unproved by this run |
| --- | --- | --- |
| `TestConvertForRTP::test_convert_f32_to_ulaw` | librosa required for resampling | That optional Float32-to-PCMU resampling case |
| `TestConvertForRTP::test_convert_f32_to_alaw` | librosa required for resampling | That optional Float32-to-PCMA resampling case |
| `test_endpoint_auth_audit::test_known_public_routes_actually_exist` | Stale `KNOWN_PUBLIC_ROUTES` entry: `DELETE /api/v1/rbac/roles/{role_id}/permissions/{permission_id}` | Public-route allowlist freshness; this skip is review debt, not proof the route is insecure or secure |
| `test_groq_model_menu::test_the_offered_caching_model_is_production_ready[openai/gpt-oss-120b]` | Model not offered | That model/menu-specific readiness assertion; not a failure of the configured Cerebras model |
| `test_pjsip_config_generator::test_write_file_mode_is_0640_group_readable` | POSIX file modes only | Linux ownership/mode behavior |
| `test_recording_encoding::test_real_ffmpeg_produces_a_much_smaller_file` | ffmpeg not installed here | Actual external encoder compression behavior |
| `test_sd_notify::test_sends_real_datagrams_to_notify_socket` | AF_UNIX unavailable; runs on Linux CI/prod | Real systemd notification datagrams |
| `test_sd_notify::test_bad_socket_path_degrades_to_noop` | AF_UNIX unavailable; runs on Linux CI/prod | That real-socket error path |

None of these skips were counted as passed. The Linux-only and external-audio checks belong in the release acceptance environment; installing dependencies was not part of the report-only follow-up.

### Frontend: two skips

The saved output marks these database-backed checks skipped:

1. `sessions enforce absolute expiry, idle timeout, binding, and rotation (db)`.
2. `sessions rotate on role/scope change and include usage/billing mapping (db)`.

The frontend's 499 passing tests do not replace those database-backed session checks. No additional reason beyond the saved output is asserted here.

### Warnings and count interpretation

The backend run reported 1,454 warnings. The saved output contains datetime deprecation warnings and other warning categories; this report does not claim every warning was classified or resolved.

The suite contains parameterized and structural checks. For example, the RLS invariant file accounts for 1,118 cases. Therefore 9,053 passing cases must not be described as 9,053 distinct customer workflows or a percentage of total product correctness.

## Two-minute timing controls: code-level distinctions

| Mechanism inspected | Code default or rule | Important distinction |
| --- | --- | --- |
| Stuck outbound job timeout | 120 seconds | Protects against stuck job state; live linked calls are excluded |
| Outbound soft call cap | 300 seconds | Normal wrap-up target, with an actively-closing exception |
| Outbound hard ceiling | 600 seconds | Separate absolute backstop |
| Telephony inactivity timeout | 300 seconds | Lack of activity, not total conversation length |
| Inbound maximum duration | Pinned campaign/admission value; configuration default 1,800 seconds | True inbound disables the outbound soft cap |
| Browser Test Agent | No universal fixed 120-second total cap found in inspected paths | Does not rule out transport, pipeline, provider or effective configuration failures |

These are inspected code defaults/rules, not a complete dump of effective per-campaign/process configuration. The earlier read-only source check also found the 300/600-second defaults in the deployed lifecycle source. A specific failed call still requires its terminal event, effective route/configuration, transport evidence and media timeline.

Do not increase a timer merely because a caller reports “about two minutes.” That could leave the actual fault hidden while increasing runaway-call exposure.

## Release acceptance checklist

This is proposed remaining work, not a record of actions already executed.

### Gate 1 — package an identifiable candidate

- Confirm the intended branch/worktree and review named changes only.
- Reconfirm the source manifest against the candidate.
- Preserve unrelated work and generated artifacts outside the commit.
- Commit reviewed source/tests through the repository's author/trailer policy.
- Freeze the exact candidate SHA and prove release-path reachability.
- Record dependency/runtime versions; source hashes alone do not freeze the runtime environment.

### Gate 2 — rehearse data and policy behavior

- Run all migrations against a restored, isolated production-shaped database.
- Confirm one head and the expected applied revision.
- Examine existing rows for grant, direction and tenant constraints before applying them.
- Measure migration lock time and determine a maintenance window from actual results.
- Compare tenant rate/concurrency allocations with observed usage now that formerly invisible rows can be read.
- Finish the flagged database-access review; do not auto-approve the remaining 46 entries.

### Gate 3 — prove a compatible rollback before changing the edge

- Capture the current code SHA and running gateway identity.
- Retain the corresponding binary, generated PBX configuration, systemd units and drop-ins.
- Record database migration compatibility; do not assume a destructive downgrade is necessary or safe.
- Verify the fallback unit/configuration combination does not reference a service absent from the fallback release.
- Define explicit abort conditions for registration loss, readiness failure and routing mismatch.

### Gate 4 — controlled backend/PBX release

- Arrange the operator's interactive sudo and maintenance participation.
- Enter the supported drain process and obtain fresh zero-session proof.
- Build/install through the supported release path; do not substitute a hand-built production binary.
- Install/reconcile the prepared PBX units and generated Asterisk configuration.
- Apply migrations through the migration unit before dependent Python restarts.
- Check process identity, running gateway build SHA, readiness, applied revision and registrations after restart.
- Confirm effective account-to-DID mapping and tenant grants, not merely that a SIP account registered.

### Gate 5 — customer-facing acceptance

- Use an approved inbound route and a controlled answering destination.
- Listen to the configured greeting and confirm campaign-specific behavior.
- Exercise caller-first/agent-first behavior only where configured; confirm direction is independent.
- Run sustained browser, inbound and outbound conversations beyond the reported 120/180-second failure point, while staying within the approved duration policy.
- Exercise successful transfer to a controlled target and failed transfer to a deliberately unavailable controlled target.
- Confirm the caller's experience and the resulting call/leg states agree for both transfers.
- Enable recording only with approved consent configuration; prove the notice and actual artifact agree.
- Match transcript, summary and billing to the same real call identifiers.
- Check that inbound and outbound campaigns do not cross-populate leads, jobs or actions.
- Check the deployed UI handles genuine busy, unavailable, conflict and storage-failure responses accurately.

### Gate 6 — recovery and monitoring

- Activate the approved synthetic inbound monitor and observe an actual run.
- Exercise alert delivery, not just alert creation or a successful healthwatch exit code.
- Restore a backup into an isolated environment and validate the restored application data.
- Rehearse dependency failure/recovery without inducing an uncontrolled production outage.
- Perform a bounded load/soak test with documented inputs and machine-readable results.
- Do not convert an idle health snapshot into an uptime or capacity claim.

## Evidence locations and fingerprints

The following files were re-read during the report-only follow-up. Hashes identify those saved artifacts; they are not signatures or proof of deployment.

| Artifact | SHA-256 |
| --- | --- |
| Final backend JUnit XML | `E6FDF91F9330BE4725A57DE19A70AEAA8DA8A8A97A74894CD21315F2911160CA` |
| Final backend console log | `B18427C99E56B4DB44621A07C299184768E71CF88ABFCC874CEECC64E4FF5B01` |
| Final PostgreSQL integration log | `7C83D578B2143AC811456D0C921DF9D592FD5B84A881F2224BD3984860D295E9` |
| Frontend canonical-check log | `BD417CBF74E7BE0CD52514EB89535B8BA4C070703D07501F0B75813C41E71854` |
| Admin canonical-check log | `8AFB648A3B89D76027C1CFA1952F491953CC179FFF190978C882B98C9CB2D977` |

Direct evidence links:

- [Final backend log](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/output/service-stability-backend-final.log>)
- [Final backend XML](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/output/service-stability-backend-final.xml>)
- [Initial failed backend run](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/output/service-stability-backend.log>)
- [Rechecked test files](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/output/service-stability-recheck.log>)
- [PostgreSQL integration log](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/output/service-stability-postgres-complete.log>)
- [Frontend log](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/output/service-stability-frontend.log>)
- [Admin log](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/output/service-stability-admin.log>)
- [180-file candidate inventory](<C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-release-code-20260918/docs/sessions/2026-09-18-code-source-manifest.json>)
- [Earlier integration report, for changes predating this stability pass](./2026-09-18-code-integration.md)
- [Complete backend execution ledger](./2026-09-18-service-stability-backend-tests.md)

The candidate and raw logs currently reside under a temporary-worktree path. Preserve them as release evidence before deleting or retiring that worktree. The Markdown report and execution ledger are in the main project's `docs/sessions/` directory.

## Final handoff

**Completed locally:** the seven documented failure areas, focused regressions, final backend/security run, fresh PostgreSQL integration, frontend/Admin checks and evidence inventory.

**Not completed:** publication/deployment, production-shaped migration rehearsal, all remaining database reviews, SIP registration proof through authorized access, candidate live-call certification, provider fault exercises, synthetic activation, restore drill and compatible rollback rehearsal.

The correct status is **tested local stability improvements, with production acceptance still open**. Nothing in this report should be read as “every service is production-ready” or “all changes are live.”
