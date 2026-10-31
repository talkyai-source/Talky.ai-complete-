# Test Agent fix: independent verification and deeper defect audit

Date: 2026-09-05, Asia/Karachi.

## Outcome

The two reviewed commits are real, narrow corrections to their stated defects. Neither proves that the whole Test Agent experience is stable, and neither is running on the production backend yet.

- `65dfcc8a932e2113dd0bc3ae70aaa4c7d11f3d0b`: corrects the RLS-blind profile lookup before WebSocket tenant discovery.
- `e390f1e6f18c87ac66d97268808e80af235293d8`: makes migration 0043 refuse downgrade before dropping its guard or moving the Alembic marker.
- Seven surrounding findings remain. Eight review probes fail against the reviewed main commit; two probes cover separate variants of the same session-authentication finding.
- These probes intentionally assert unmet behavior. Their failures are separate from the canonical repository suite, not failures introduced into that suite.
- No production code, schema, configuration, or service was changed during this audit. The only repository additions are this report and its review probes. Nothing was committed or pushed in this audit.

## Scope and source of truth

Reviewed `origin/main` at `e390f1e6f18c87ac66d97268808e80af235293d8` in the isolated checkout:

```text
C:\Users\AL AZIZ TECH\AppData\Local\Temp\talky-release-e390f1e6-clean
```

The user's primary checkout remains on a different branch with many uncommitted frontend/backend edits. Those files are not evidence of what reached main or production and were not incorporated into this review.

Source references below refer to the reviewed SHA. Reproduction uses the existing endpoint test harness for transport, database-return values, and provider substitutes. It does not place a real call, contact a speech provider, or forge a production token.

The audit follows CLAUDE.md and the code-review skill: examine executable behavior, reproduce findings, and distinguish observed defects from unverified consequences.

## Production: rechecked in this audit

| Item | Observed value | Meaning |
|---|---|---|
| GitHub main | `e390f1e6f18c87ac66d97268808e80af235293d8` | Includes both reviewed fixes |
| `/opt/talky` HEAD | `d11a16679fe0e324ddcade5420d080a63cf34b2e` | Does not include either reviewed fix |
| Running gateway `/ready.build_sha` | `a4aa0c5669ea8cd0e87b6ac1b653e49719db43b8` | Gateway and production checkout also differ |
| Alembic marker | `0040_calls_campaign_nullable` | 0041, 0042, and 0043 remain unapplied |
| API health | `healthy`, initialized, Redis enabled, active sessions 0 | Liveness does not prove Test Agent works |
| Gateway readiness | `ready:true`, protocol 2, codecs `[pcmu]` | Readiness does not prove the release SHAs match |
| Database application role | `rolsuper:false`, `rolbypassrls:false` | Tests must account for actual RLS |

There are six commits between production HEAD and the reviewed main SHA, including campaign-boundary work, ownership constraints, and call-history changes. Deploying main is therefore larger than applying the profile helper alone.

This audit did not run deployment, apply migrations, disable traffic, or change the production approval mechanism. The earlier deployment stopped before mutation because its required drain manifest was absent. Actual live behavior still needs post-deployment verification.

## What was fixed correctly

### A. Profile discovery: real root-cause correction

Root cause: the compatibility `.table()` adapter requires a tenant context. A WebSocket has not passed through the HTTP tenant middleware, and this lookup is the very operation that discovers its tenant. The old adapter therefore applies a nil tenant and hides the existing user profile.

Change: `_resolve_user_tenant()` opens a transaction through `acquire_with_tenant(pool, None, user_id=...)`, selects only the signed subject's profile using `WHERE id = $1`, then lets the endpoint establish the discovered tenant. Missing profiles and database failures now have different machine-readable errors and close codes.

Source: [campaign_test_ws.py:146](https://github.com/talkyai-source/Talky.ai-complete-/blob/e390f1e6f18c87ac66d97268808e80af235293d8/backend/app/api/v1/endpoints/campaign_test_ws.py#L146), endpoint use at line 480; `backend/app/core/db_utils.py:60` implements the transaction scope.

Read-only reproduction against the real production database:

```text
role.rolsuper = false
role.rolbypassrls = false
nil_tenant_lookup_visible = false
bootstrap_lookup_matches = true
audit_user_matches = true
returned_connection_context = {"bypass":"", "tenant":"", "usr":""}
```

This selected one existing tenant-backed profile without printing its ID or tenant. It compared the old nil-context query to the new helper's exact SQL pattern, using the actual production helper and a single-connection pool. The returned connection carried no remaining tenant, user, or bypass value after the transaction.

Why this is not a workaround:

- It resolves the circular dependency between tenant discovery and tenant-scoped lookup.
- It does not hardcode a tenant, suppress the error, create duplicate profiles, or grant the database role permanent BYPASSRLS.
- The bootstrap query is parameterized and restricted to one authenticated subject.
- Subsequent campaign lookup still includes both campaign ID and tenant ID, and permission checking still precedes provider creation.

Limits: this proves the database access pattern. It does not prove server-session revocation, membership suspension, pool exhaustion, or an end-to-end browser conversation. The new regression test mocks `acquire_with_tenant`; the real database check above is the additional evidence for its RLS behavior.

Tests: `test_profile_lookup_bootstraps_through_pool_before_tenant_context`, `test_missing_profile_has_stable_error_code`, `test_profile_database_failure_has_distinct_stable_error_code`.

### B. Migration 0043 downgrade: real contract correction

Root cause: 0043 had a reversible downgrade that removed its direction-lock trigger/function. Alembic could consequently move off the current head, violating the repository's existing forward-only migration contract and its integration assertion.

Change: downgrade raises immediately. No `op.execute()` is reached; upgrade SQL is unchanged.

Source: [0043_campaign_direction_lock.py:76](https://github.com/talkyai-source/Talky.ai-complete-/blob/e390f1e6f18c87ac66d97268808e80af235293d8/backend/Alembic/versions/0043_campaign_direction_lock.py#L76).

Why this is not merely hiding a CI failure: refusing downgrade is the pre-existing contract in `test_current_head_cli_downgrade_refuses_without_moving_marker`. The integration test was not weakened. The unit test now checks immediate refusal and zero executed schema statements, instead of expecting a prohibited reversal.

Limits:

- This is a migration-policy correction, not a fix for Test Agent authentication or a proof of every direction-conversion race.
- Code rollback and schema downgrade are separate actions. This change deliberately prevents the latter through 0043.
- The trigger's documented lock-order limitation remains: application writers must take the advisory lock before row locks; the trigger alone cannot eliminate deadlocks from incorrectly ordered writers.
- A future migration with a permissive downgrade could recreate the head-marker problem. The existing current-head integration test needs to remain in CI.
- No downgrade command was executed against production in this audit.

Tests: `test_0043_downgrade_refuses_before_mutating_schema`, plus the existing bootstrap and current-head integration checks in the reviewed GitHub workflow.

## Remaining findings, ordered by consequence

### F1 — High: revoked or unbound login sessions can reach Test Agent

Evidence: `campaign_test_ws.py:454–480` validates the JWT and extracts `sub`, but never verifies `sid` against server-side session storage. REST authentication explicitly checks this binding in `dependencies.py:249–293`. The centralized JWT decoder checks signature and time claims; it does not query session revocation.

Reproduction, retaining all downstream endpoint gates and substituting provider/database results:

```text
sid=None session_lookups=0 provider_creations=1
sid='revoked-session' session_lookups=0 provider_creations=1
```

These tests model a signature-valid token at the decoder boundary. They do not test cryptographic verification or demonstrate a live exploit. They establish that the endpoint ignores the server-session state completely.

Consequence: logging out or revoking a login does not necessarily prevent a still-unexpired access token from starting this endpoint, provided the user still has the required database permission. Existing sessions also have no session-revocation recheck in the receive loop.

Classification: pre-existing authentication gap exposed by repairing the earlier profile blocker; not introduced by the new SQL helper.

Smallest durable correction: reuse the session-bound authentication checks for the WebSocket before profile/provider access. Verify `sid` and user together, reject revoked/expired/missing sessions, and fail closed on lookup errors. Avoid duplicating a second independently evolving authentication policy.

Done condition: a valid active session starts; missing/revoked/expired/wrong-user sessions never create a provider session. Decide and test the policy for revocation during an already open test session.

### F2 — High, conditional: direct grants bypass the membership-status filter

Evidence: `rbac.py:552–554` requires active membership only for role-derived permissions. The direct-permission query at lines 582–593 reads unexpired user grants without requiring an active tenant membership. Test Agent checks the resulting permission set but performs no separate active-membership check.

Reproduction:

```text
role_grants=[]
direct_grants=campaigns:update
effective_permissions includes campaigns:update
```

The probe substitutes the query results expected when membership-derived grants are absent and a direct grant remains. It does not assert that production currently contains such a suspended user or that all global/platform grants should be removed.

Consequence: a suspended/removed tenant member with a surviving direct grant can remain authorized through this path. This is distinct from revoking an individual permission.

Classification: pre-existing shared RBAC behavior, with impact beyond Test Agent. Treat as an authorization-policy correction with explicit platform-admin exceptions, not a blind edit to every direct grant.

Smallest durable correction: require active membership for tenant-scoped Test Agent access before aggregating its grants. Preserve intentionally supported platform access through an explicit separate rule.

Done condition: real membership fixtures covering active, suspended, removed, direct grants, expired grants, and supported platform access. Both role and direct grants must obey the chosen membership policy.

### F3 — Medium: a tenant-config read failure starts a default agent as if nothing failed

Evidence: `tenant_ai_config_resolver.py:104–120` catches lookup failures and returns the process default. The endpoint passes that result into the real builder at `campaign_test_ws.py:609–620` and emits normal `ready` at line 734. The browser receives no indication that its saved configuration was unavailable.

Reproduction:

```text
tenant lookup raises RuntimeError('audit database outage')
ready frame emitted
resolved_llm=gpt-oss-120b
```

A warning is logged on the server. This is hidden from the operator's Test Agent session, not literally unlogged. Missing configuration, an unwired resolver, and a transient lookup failure all converge on defaults.

Consequence: the operator can test a different pipeline/model/voice fallback from the saved settings and reasonably conclude that AI Options is broken. This is a demonstrated failure mode, not proof it caused a particular historical call.

Classification: an intentional availability fallback that is inappropriate for claiming exact saved-agent fidelity. It remains workaround behavior around configuration failure.

Smallest durable correction: distinguish a genuinely absent tenant configuration from an unreadable configuration. For Test Agent, refuse startup with a precise retryable error when a saved configuration cannot be resolved. A broader real-call fallback policy should be explicit and independently reviewed.

Done condition: a changed saved config reaches the next test; a lookup outage does not emit ordinary `ready` or create a default-agent session; a new tenant's documented default behavior remains tested.

### F4 — Medium: runtime prompt truncation still removes operator instructions

Evidence: `telephony_session_config.py:377–461` keeps the beginning/end of over-budget text and inserts an omission marker. The builder invokes this at lines 1376–1382 before composing. Save/start/preview budget validation exists in `campaign_prompt_service.py`, but does not remove this runtime truncation path for legacy/imported data or a reduced runtime budget.

Reproduction through the real builder:

```text
input_chars=192041
marker_preserved=False
elision_present=True
```

The deliberately large stress input contained a unique instruction in its middle. The builder returned a usable config with that instruction absent. This is not evidence that all UI saves accept such input. There is a server warning and an LLM-visible omission marker, but the operator's rule is still missing.

Consequence: a legacy campaign can run while omitting a business rule. The product message at `campaign_prompt_service.py:58` says nothing is trimmed automatically, which conflicts with this behavior.

Classification: a genuine remaining workaround for oversized prompts. It does not belong to either of the two reviewed fixes.

Smallest durable correction: enforce the same combined guidance budget before Test Agent/session creation and reject over-budget input with the existing actionable message. Inventory existing over-budget campaigns before changing runtime behavior. Keep canonical guidance and preview composition aligned.

Done condition: over-budget guidance is rejected before provider allocation, or preserved under an explicitly changed budget. A unique middle instruction must never disappear from an otherwise successful config.

Pilot limit: production campaign `50847cc9` currently has 9,465 guidance characters. This is below the code-default 12,000-character budget. This audit did not inspect the running process's budget environment override, so it does not attribute that pilot's replies to truncation.

### F5 — Medium: realtime agent naming consults the unused TTS voice

Evidence: `telephony_session_config.py:1298` derives name gender from `tts_voice_id`. Realtime mode and its distinct speech voice are selected later at lines 1661–1673. The TTS gender resolver also does not include a realtime voice catalogue.

Reproduction:

```text
actual_realtime_voice=marin
consulted_for_name=['aura-zeus-en']
```

Consequence: in realtime mode, an inactive cascaded TTS setting can determine the name-selection logic even though a different engine produces the audio. This proves incorrect wiring; it does not assign a gender to the audible realtime voice or prove an audible mismatch in a real call.

Classification: incomplete identity/voice wiring, separate from the fixed profile discovery.

Smallest durable correction: resolve the active pipeline and effective audible voice before selecting an automatic name. Use metadata for that active voice; preserve an explicit operator identity according to a documented rule. Unknown metadata must remain unknown instead of borrowing another pipeline's voice.

Done condition: changing an unused cascaded voice cannot change realtime name selection; supported active voice changes follow the intended identity rule. Test both pipelines and campaign/tenant overrides.

### F6 — Medium: a pipeline cancellation exception skips durable call finalization

Evidence: endpoint cleanup at `campaign_test_ws.py:848–859` awaits transcript persistence, then `end_session()`, then `_finalise_test_call()`. `VoiceOrchestrator.end_session()` catches only `CancelledError` while awaiting a cancelled pipeline task at lines 1077–1083. A task that raises another exception during its own cleanup escapes this sequence.

Reproduction uses the real `VoiceOrchestrator.end_session()` method and an actual asyncio task whose `finally` raises during cancellation:

```text
persisted=1 finalized=0
```

Consequence: the test row can remain unfinished, and downstream gateway/provider cleanup may be skipped. This is a demonstrated exception path, not evidence that every provider cleanup failure escapes; most provider cleanup methods are separately guarded.

Smallest durable correction: make the independent cleanup obligations execute even if an earlier obligation fails, with bounded waits and explicit logging. Finalize the test row in a separate protected cleanup path; preserve the teardown error for diagnosis.

Done condition: pipeline cancellation errors, transcript persistence errors, provider shutdown errors, and client disconnects each still reach the required cleanup/finalization steps. These are test rows, so do not infer a billing overcharge from this finding.

### F7 — Medium: database failures are still disguised as a missing campaign

Evidence: `_fetch_campaign_row()` catches every lookup exception and returns `None` at `campaign_test_ws.py:184–200`. The endpoint translates that into `Campaign not found` and close 1008 at lines 551–555.

Reproduction:

```text
campaign lookup raises RuntimeError('audit database outage')
close=1008
message='Campaign not found.'
```

Consequence: the newly precise profile error handling stops one step too early. A second database failure looks like deletion or a tenant-ownership problem, and the operator receives the wrong recovery advice.

Classification: pre-existing error classification defect, adjacent to the fixed profile error path. The explicit tenant predicate prevents this failure from becoming cross-tenant access.

Smallest durable correction: preserve `None` only for a real tenant-scoped miss; propagate or return a typed temporary lookup failure. Reply with a sanitized retryable error and close 1011 for that failure.

Done condition: real missing/other-tenant campaigns remain inaccessible with policy semantics; database failures carry distinct temporary-failure semantics and never create a provider session.

## Voice, prompt, and generic-answer interpretation

The normal path is campaign row plus tenant AI configuration, followed by `build_telephony_session_config()`, then `VoiceOrchestrator`. Cascaded speech uses the effective campaign/tenant TTS choice. Realtime speech uses a separate realtime voice. Campaign-specific provider/voice overrides intentionally take precedence over tenant defaults; a tenant setting alone is not guaranteed to override a campaign-specific voice.

Read-only production check for the campaign associated with the reported test attempt:

```text
campaign=50847cc9
direction=outbound
status=stopped
campaign_tts_provider=elevenlabs
tenant_tts_provider=elevenlabs
campaign_voice=lfPTQbwnu1oXQ9g6V0r4
tenant_voice=lfPTQbwnu1oXQ9g6V0r4
tenant_pipeline=cascaded
campaign_pipeline=null
tenant_realtime_voice=marin
campaign_realtime_voice=null
guidance_chars=9465
persona=lead_gen
```

These settings do not establish a current campaign-versus-tenant voice disagreement. They also show that the realtime naming defect is not on this pilot's currently configured cascaded path.

Additional source-level limits, not counted as reproduced defects above:

- Knowledge injection is deliberately fail-soft at `campaign_test_ws.py:709–728`. A knowledge failure can continue the test with less context; the log is not proof knowledge reached a model.
- Profile resolution supplies no explicit acquisition timeout even though the shared helper supports one. The browser's connection timeout does not prove a blocked server-side pool wait is cancelled.
- The frontend awaits auth refresh and microphone permission without a session-generation cancellation check in all those continuations. This deserves a real browser test for Stop/unmount while awaiting permission or refresh. No browser race was reproduced in this audit.
- Successful prompt composition is not proof an LLM obeyed all instructions. A real conversation needs the final provider request, effective prompt identity, selected provider/voice, and the resulting transcript/audio correlated to one session.

## Verification and reproducibility

Focused existing regression/migration tests were rerun in this audit:

```text
python -m pytest tests/unit/test_campaign_test_ws.py tests/unit/test_campaign_direction_lock_migration.py tests/unit/test_bootstrap_contract_repair_migration.py tests/unit/test_bootstrap_contract_repair_normalization.py -q
56 passed in 12.43s
```

Canonical Ruff check was rerun:

```text
python -m ruff check app/ --select F --extend-ignore F401,F841
All checks passed!
```

Canonical backend unit/security suite was rerun against the reviewed main checkout in this audit:

```text
python -m pytest tests/unit tests/security -q
8651 passed, 7 skipped, 1467 warnings in 611.64s (0:10:11)
```

The interpreter came from the project's existing `backend/.venv/Scripts/python.exe`; the working directory and application/test imports were the isolated main checkout's backend. The independent review probes were outside `tests/unit` and `tests/security`, so this existing-suite result and the eight review failures measure different coverage.

New review probes:

```text
python -m pytest audit_test_agent_review.py -q -s --tb=short
8 failed in 7.08s
```

All eight failures reached their intended behavioral assertions. They represent seven findings because the missing-session and revoked-session cases share F1. The probes are preserved alongside this report in `2026-09-05-test-agent-audit-probes.py`; the executable copy used in the reviewed checkout is `backend/audit_test_agent_review.py`. They are not added to normal test discovery or committed.

To reproduce with the preserved probe file, use the reviewed checkout's backend as the working directory, its application/test imports, and the available project Python environment:

```powershell
Set-Location 'C:\Users\AL AZIZ TECH\AppData\Local\Temp\talky-release-e390f1e6-clean\backend'
& 'C:\Users\AL AZIZ TECH\Desktop\Talky.ai-complete-\backend\.venv\Scripts\python.exe' -m pytest 'C:\Users\AL AZIZ TECH\Desktop\Talky.ai-complete-\docs\sessions\2026-09-05-test-agent-audit-probes.py' -q -s --tb=short
```

The existing harness mocks identity verification, most persistence, permissions, and providers. F3 runs the real tenant config resolver and builder; F4 runs the real builder; F5 instruments the real builder's gender-resolution input; F6 runs actual task cancellation through the real teardown method. F2 substitutes database query results and requires a future real-database authorization test before remediation is considered complete.

GitHub results were re-read during this audit for the exact reviewed SHA:

- [Main CI — all jobs successful](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/33930468143).
- [Voice CI — successful](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/33930468145).

Those include earlier frontend/build/integration results, not fresh browser or local frontend test execution in this audit. The standalone direction-advisory-lock integration test exists in `tests/integration/test_campaign_direction_advisory_lock.py`; its existence must not be confused with proof it ran in this audit.

## What remains before calling the experience stable

1. Close session-binding and membership-status gaps with authorization tests.
2. Give config-read failures and campaign-read failures precise behavior before provider creation.
3. Eliminate runtime instruction loss and resolve identity from the active speech path.
4. Make teardown and durable test-row finalization independent under faults.
5. Exercise browser Stop, auth retry, microphone denial, disconnection, and both speech pipelines with a controlled session.
6. Deploy through the supported release path, reconcile the pending schema and gateway, and verify the exact running commit.
7. Complete a live Test Agent session with the intended account/campaign and correlate settings, final instructions, first audio, transcript, and ended test row.

## Not done

- No remediation for F1–F7 was implemented: this request was to inspect and report, and these are distinct changes requiring their own regression fixes.
- No live test call or provider synthesis was initiated.
- No current-user session was revoked, no membership was suspended, and no production row was inserted/updated/deleted.
- No live end-to-end JWT/browser exploit test was attempted.
- No production migration or downgrade was executed.
- No local frontend/admin suites or browser audio test were rerun; no frontend/admin code was changed.
- No claim is made that the pilot's historical generic answers have one conclusively identified cause. The report identifies reproduced mechanisms and rules out specific explanations for the currently observed saved settings where evidence permits.
