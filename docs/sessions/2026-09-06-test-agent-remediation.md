# Test Agent remediation and release evidence

Date: 2026-09-06, Asia/Karachi.

## Outcome

Seven reproduced audit findings have code corrections and regression tests in eight commits. Release candidate: `a802d1838e021421d4da1338a7a227e3d38d661a`.

The eight commits have been pushed successfully to `origin/main`. Final local canonical checks and both GitHub workflows passed, including the Docker build/scan. **The backend fixes are not live.** The supported deployment requires a secure sudo operator session and a valid, independently approved traffic-drain manifest. Neither has been supplied. No production writes have been performed.

The fixes use the bug-hunt workflow: reproduce failure, change the responsible boundary, test the failure path, then run canonical suites. They do not bypass RLS, hardcode the pilot tenant, substitute a generic agent on a database outage, or weaken deployment gates.

## Scope and isolation

- Base: `origin/main` at `e390f1e6f18c87ac66d97268808e80af235293d8`.
- Candidate built in `C:\Users\AL AZIZ TECH\AppData\Local\Temp\talky-release-e390f1e6-clean`.
- Shared primary checkout and its unrelated backend/frontend modifications were preserved.
- Twelve explicitly named backend application/test files changed; no frontend application files changed.
- No new dependencies, database migrations, telephony configuration edits, or C++ source changes were introduced by these eight commits.
- Commits use `tooti12 <umar.jwork@gmail.com>`, a subject plus one body line, and no trailers.
- Reports and scratch probes are not included in the code commits.
- The isolated clone has an unrelated test-generated tracked bytecode modification and an untracked audit probe; neither was staged. A deployment must use a clean release checkout.

## Commit inventory

| Commit | Root-cause correction |
|---|---|
| `423432b6` | Bind Test Agent to the signed user's revocable login session |
| `fc502ae1` | Require active tenant membership in addition to permission grants |
| `257062fb` | Refuse Test Agent startup when saved AI settings cannot be read |
| `698009ad` | Validate the full runtime guidance budget without middle truncation |
| `89b3d104` | Resolve realtime identity from the voice that actually speaks |
| `8b7fabd8` | Continue independent transcript, session, and row cleanup obligations |
| `e51732bb` | Distinguish campaign infrastructure failure from ownership/missing errors |
| `a802d183` | Prevent failed authorization/receiver tasks from skipping teardown |

## F1 — Signed token without server-session revocation binding

**Root cause:** JWT verification alone allowed a token to open the endpoint without proving that its corresponding login session remained valid.

**Change:** `_session_is_active()` delegates to the existing revocable session query, bound to both session ID and signed user ID. Missing, malformed, revoked, expired, and wrong-user session references fail closed. The existing session query checks `revoked = FALSE` and `expires_at > now`.

Source: `backend/app/api/v1/endpoints/campaign_test_ws.py:149`; existing query: `backend/app/core/security/sessions/queries.py:148`.

An active test rechecks authorization every 15 seconds through `_watch_login_session()` at endpoint line 218. Session and permission lookups are bounded by five-second timeouts. Revocation is therefore polled, not instantaneous. Startup work before the watcher starts is not claimed to have an overall time bound.

Errors: `auth_required` / WebSocket 1008 for invalid authorization; `authorization_unavailable` / 1011 for lookup failure. Database exception details are not sent in these new error frames.

Tests: `test_invalid_login_session_never_allocates_provider`, `test_login_session_database_failure_is_retryable_and_closed`, `test_login_session_query_uses_revocable_user_bound_lookup`, `test_open_test_stops_when_login_is_revoked`.

Observed red/green: 7 failing new cases before the fix; 27 combined endpoint cases passed afterwards.

Additional PostgreSQL proof: the existing `get_session_by_id()` helper was executed against CTE fixture rows in a read-only transaction on the server. Active returned true; revoked, expired, wrong-user, and wrong-session each returned false. All five assertions passed without reading or changing real login-session rows.

**Why this is a root fix:** it validates the missing authorization fact against the existing authoritative session table; it does not lengthen tokens or suppress authentication errors.

## F2 — Direct grants surviving inactive membership

**Root cause:** permission aggregation could expose a direct grant without independently proving active tenant membership.

**Change:** Test Agent now requires active membership for the requested tenant before resolving its effective campaign permission. The explicit exception is an active platform administrator with a non-tenant-scoped role. Membership and permission are rechecked during an active test.

Source: `backend/app/api/v1/endpoints/campaign_test_ws.py:181`.

Tests: `test_direct_grant_cannot_override_inactive_membership`, `test_membership_lookup_checks_active_tenant_or_explicit_platform_role`, `test_open_test_stops_when_membership_is_removed`.

Observed red/green: 4 failures before the correction; 31 combined endpoint cases passed afterwards.

Additional evidence: executed the membership SQL against PostgreSQL in a read-only transaction with CTE fixture rows, not production membership mutations:

| Fixture | Allowed | Expected |
|---|---|---|
| Active tenant member | true | true |
| Suspended member | false | false |
| Removed member | false | false |
| Member of a different tenant | false | false |
| Active global platform administrator | true | true |
| Suspended global platform administrator | false | false |

All six fixture assertions passed. These verify SQL behavior; they are not six browser-login tests.

**Scope limit:** the shared RBAC aggregator was not changed globally. Other endpoints using it without their own membership prerequisite are not certified by this fix.

## F3 — Saved AI settings replaced by defaults after lookup failure

**Root cause:** configuration resolvers treated an unavailable database lookup like an absent tenant configuration and returned defaults. A test could sound generic instead of reporting that its saved settings were unavailable.

**Change:** provider configuration and voice-tuning resolvers now support `require_available=True`. Test Agent uses that mode before allocating a provider. An unwired lookup, lookup exception, or timeout closes with `ai_config_unavailable` / 1011. A successful query returning no configuration row still uses the documented new-tenant defaults.

Sources: endpoint lines 674–675; `tenant_ai_config_resolver.py:52,89`; `voice_tuning.py:152`.

Tests: `test_unreadable_tenant_config_refuses_default_agent`, `test_voice_tuning_outage_refuses_to_substitute_defaults`, `test_new_tenant_without_config_uses_documented_defaults`.

Observed red/green: two failing outage cases and one already-passing legitimate-default case; then 48 combined endpoint/config-isolation tests passed.

**Why this is a root fix:** absent data and failed access are different outcomes. The endpoint now preserves that distinction rather than pretending a generic voice is the saved choice.

**Scope limit:** strict mode is enabled for Test Agent. Existing phone-call resolver callers retain their previous default behavior; this release does not claim to eliminate fallback globally. Legacy malformed-value normalization in configuration parsing is also not a new contract addressed here.

## F4 — Runtime prompt truncation removed campaign instructions

**Root cause:** the runtime builder retained the beginning and ending of an oversized prompt but removed its middle. Save/start/preview promised a shared budget and no silent trimming; runtime composition violated that promise.

**Change:** runtime uses the same `guidance_budget_violation()` and error wording as save/start/preview. Final instructions plus the structured campaign brief are checked together, including any existing name substitution. Accepted guidance remains intact; oversized guidance raises `CampaignPromptValidationError`. Test Agent responds with `campaign_prompt_invalid` before provider allocation.

Source: `backend/app/domain/services/telephony_session_config.py:377`, final validation near line 1353; endpoint error mapping near line 906.

Tests: runtime combined-budget cases in `test_campaign_guidance_budget.py`; preservation/rejection contracts in `test_telephony_session_config.py` and `test_prompt_composer.py`; `test_oversized_test_prompt_is_rejected_before_provider_creation`.

Observed red/green: three failures before the change; then 137 combined guidance/builder/composer/endpoint cases passed.

Old tests requiring truncation were rewritten to assert the current preserve-or-reject invariant. The limit was not raised and an assertion was not loosened to permit trimming.

Read-only production compatibility check:

```text
configured_env_budget: 12000
campaign_rows_checked: 27
oversized: [{campaign: b6a61ac6, status: deleted, chars: 19597}]
```

Only a deleted campaign exceeded the measured canonical guidance budget. This is a snapshot, not a permanent assurance against later configuration edits or post-substitution expansion at the boundary.

**Why this is a root fix:** no campaign instructions are discarded to hide the budget conflict. Operators receive an actionable error.

## F5 — Realtime identity depended on an unused TTS voice

**Root cause:** name/gender selection examined the cascaded TTS voice even when the call's active speech engine was realtime.

**Change:** the builder determines the effective pipeline and campaign/tenant realtime voice before resolving identity. Realtime uses that audible voice; cascaded uses its effective TTS voice. Realtime metadata comes from the existing catalogue. Neutral and unknown voices do not acquire an invented gender.

Sources: `telephony_session_config.py:1193,1225`; `global_ai_config.py:169`.

Tests: all eight cases in `test_realtime_identity_voice.py`, including conflicting unused TTS settings, campaign realtime override, catalogue metadata, and unknown voice behavior.

Observed red/green: 5 failed and 3 passed before the correction; then 123 combined identity/builder/endpoint tests passed.

**Scope limit:** this corrects identity selection, not provider audio itself. It does not prove a provider account can access every catalogue voice or that an LLM always follows every instruction. Campaign-specific overrides still deliberately take precedence where configured.

## F6 — Cleanup errors prevented later cleanup

**Root cause:** an exception during transcript persistence, task cancellation, or session teardown could prevent durable call finalization and resource cleanup.

**Change:** transcript persistence, session teardown, and final row update are independent obligations using nested `finally` blocks. Each has a ten-second cooperative timeout. Caller cancellation is preserved after subsequent obligations execute. The orchestrator catches an exception from a pipeline's cancellation cleanup and continues releasing gateway/providers. Failed authorization and receiver tasks cannot bypass this sequence.

Sources: endpoint cleanup at line 918; `voice_orchestrator.py:1087`.

Tests: `test_cleanup_failure_does_not_skip_other_obligations`, `test_pipeline_exception_during_cancel_still_releases_gateway_and_session`, `test_transcript_timeout_or_cancellation_still_finalizes_test`, `test_failed_auth_watchdog_cannot_skip_session_cleanup`.

Observed red/green: three failures and one passing existing case; then 75 endpoint/orchestrator cases passed. Timeout/cancellation additions yielded 42 combined cases passed. The separate failed-watchdog reproduction failed once before the follow-up and passed afterwards with 45 endpoint cases.

**Scope limit:** attempting finalization is not a guarantee that a database outage can be overcome. A finalization failure is logged. Async timeouts are cooperative; cancellation-resistant provider code, process termination, or persistent database failure requires broader recovery/reconciliation work. No claim of exactly-once durable teardown under arbitrary failures is made.

## F7 — Campaign database failure shown as missing campaign

**Root cause:** campaign fetch failures were converted to `None`, indistinguishable from a missing or unauthorized campaign.

**Change:** valid campaign reads retain explicit `id = $1 AND tenant_id = $2`, use bounded access, and raise a typed unavailable error on infrastructure failure. Missing/malformed campaign IDs retain policy-denial behavior; infrastructure failure produces retryable `campaign_lookup_failed` / 1011 without exposing private error details.

Source: `backend/app/api/v1/endpoints/campaign_test_ws.py:252`.

Tests: `test_campaign_read_outage_is_retryable_not_missing`, `test_campaign_miss_keeps_ownership_predicate_and_policy_close`.

Observed red/green: one failure and one already-passing ownership case; then 44 combined endpoint cases passed.

## Canonical verification

The interpreter comes from the primary checkout's virtual environment, but the working directory, application imports, and tests come from the isolated origin/main-based release tree.

```text
backend: python -m pytest tests/unit tests/security -q
initial full run: 8685 passed, 7 skipped, 1466 warnings in 679.42s (0:11:19)
```

That run began before the final failed-watchdog regression was added. It is not final-candidate certification. A fresh full run against the final `a802d183` code completed successfully:

```text
8686 passed, 7 skipped, 1466 warnings in 441.79s (0:07:21)
exit: 0
```

```text
ruff check app/ --select F --extend-ignore F401,F841
All checks passed!

Admin: npm run lint; npm test; npm run build
sequence exit: 0
test recheck: tests 13; pass 13; fail 0; skipped 0
build: 1764 modules transformed; built in 45.76s

Talk-Leee: npm run typecheck; npm run lint; npm test
sequence exit: 0
tests 370; suites 3; pass 368; fail 0; skipped 2
duration_ms 258104.6761

git diff e390f1e6 HEAD --check
exit: 0

git push origin HEAD:main
e390f1e6..a802d183  HEAD -> main

git ls-remote origin refs/heads/main
a802d1838e021421d4da1338a7a227e3d38d661a refs/heads/main
```

Package-install notices: Talk-Leee reported six vulnerabilities (one low, five moderate); Admin reported one moderate. No dependency upgrades or automatic audit fixes were applied. Existing backend deprecation warnings and frontend React `act()` warnings were observed; a passing suite is not a warning-free suite.

## Production evidence and deployment boundary

Read-only SSH recheck during this implementation:

```text
/opt/talky HEAD: d11a16679fe0e324ddcade5420d080a63cf34b2e
git status --porcelain: empty
sudo -n true: sudo: a password is required
```

Final read-only recheck after CI completed still returned production HEAD `d11a16679fe0e324ddcade5420d080a63cf34b2e`, an empty porcelain status, and `/health` reporting `healthy`, container `initialized`, Redis enabled, and zero active API voice sessions. This is liveness evidence, not evidence that the new Test Agent fixes are running or that traffic has been drained across all systems.

Additional production checks during the task found the pilot's active membership/platform authorization and a valid bound login session. The earlier profile-discovery RLS correction remains included in the candidate's ancestry.

Deployment is larger than these eight commits: main already contains unapplied changes after production, including migrations 0041–0043. The last verified production marker is `0040_calls_campaign_nullable`. No schema migration has been run in this implementation.

The supported `deploy_to_server.sh` requires the exact candidate SHA, a manifest digest, ingress and outbound origination frozen, zero gateway/Asterisk/Redis/database active counts, fresh independent evidence references, and two distinct real approvers. It then requires interactive sudo to reconcile/install/restart services. These are actual script gates, not optional notes.

No approval artifact was fabricated, no drain was claimed from a single health counter, and no manual restart/hotpatch was substituted for deployment.

A clean detached worktree was prepared at `C:\Users\AL AZIZ TECH\AppData\Local\Temp\talky-deploy-a802d183`, with `git status --porcelain` empty and HEAD matching the candidate. Running the actual supported script there stopped at its first gate, before SSH or mutation:

```text
bash ./deploy_to_server.sh
!! Set TALKY_DEPLOY_DRAIN_MANIFEST and TALKY_DEPLOY_DRAIN_MANIFEST_SHA256.
exit: 1
```

GitHub verification runs for this exact SHA:

- [CI — Lint, Test, Build, Scan](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/33987415437)
- [Backend voice and full unit/security suites](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/33987415438)

The backend-voice-tests workflow completed successfully on the pushed SHA. Its logs report:

```text
full unit + security suite:
8689 passed, 4 skipped, 1467 warnings, 18 subtests passed in 260.20s (0:04:20)

voice pipeline (fast):
239 passed, 128 warnings in 5.50s
```

The Linux CI counts and Windows local counts are recorded separately, not combined. The general CI workflow also completed successfully. All eight jobs passed: secret scan, secret placeholder guard, SQL schema validation, frontend, backend, admin frontend, telephony-ingress safety, and Docker build/scan.

Additional general-CI log evidence:

```text
Preserved baseline bootstrap contract: 13 passed, 1 skipped in 4.28s
Additional baseline assertion: 1 passed in 0.63s
Migrated inbound database integration: 16 passed, 1 skipped in 5.09s
Coverage-backed backend suite: 8689 passed, 4 skipped, 1468 warnings in 357.92s (0:05:57)
Reported total coverage: 62.26%
```

Green checks are not a claim of full test coverage, zero warnings, or production migration success. The push does not constitute a backend production deployment.

GitHub's commit-status API reports the Vercel status for `a802d183` as `success`, description `Deployment has completed`, linking to [this frontend deployment](https://vercel.com/allestateestimation-9391s-projects/talkleeai/H53yJqn7W3nuj88DBA4V8dA9nxeT). This is provider-reported deployment status, not an authenticated browser check. These fixes are backend changes and remain inactive until the Hetzner deployment succeeds.

## Pre-mortem and remaining release checks

| Possible failure | Mitigation in this release | Still to prove |
|---|---|---|
| Old token starts or retains a paid provider session | Bound server-session lookup and periodic recheck | Browser revocation exercise after deployment |
| Suspended member retains a direct grant | Active membership prerequisite | End-to-end suspended account test |
| Database outage silently selects generic defaults | Strict Test Agent configuration mode | Live failure telemetry; ordinary phone path remains unchanged |
| Important middle prompt instructions disappear | Shared full-budget reject-or-preserve rule | Audible response evaluation on real prompts |
| Realtime female voice receives male identity from unused TTS setting | Identity uses active speech engine's effective voice | Listening test for chosen provider voice |
| Cleanup failure strands the calls row | Independent attempts, logging, cooperative timeouts | DB-outage recovery/reconciliation beyond this patch |
| Campaign outage sounds like deleted campaign | Retryable structured error | Browser display/retry after deployment |
| Deploy interrupts active calls | Existing external drain approval gate retained | Operator-supplied live drain evidence |
| GitHub main mistaken for live backend | Separate SHA, schema, readiness and process proofs | Successful supported deployment and authenticated browser test |

## Not done

- Backend production deployment and migrations: blocked on secure sudo access and a valid approved drain manifest.
- Post-deploy Test Agent conversation, actual voice listening, and saved-prompt response checks: cannot be certified before the candidate is live.
- Real inbound/outbound carrier test calls: not placed during this remediation.
- Global RBAC redesign and strict fallback behavior for all phone paths: outside these seven audit fixes.
- Vercel browser verification: no frontend source changes were needed for this patch, and no frontend deployment is evidence of backend deployment.
- The old scratch audit probe file is not used as final proof after the test harness changes; the named behavioral regressions above replace it.
