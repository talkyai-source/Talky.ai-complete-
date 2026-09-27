# Backend follow-up QA — 16 September 2026

## Outcome

**Eight additional/open backend defect areas were reproduced. Production is not yet ready for an unconditional all-working claim.** The complete existing backend/security suite passes, but it does not detect these cases. This turn added diagnostic evidence only; no application code, production data, credentials, services or deployment was changed.

## Evidence boundary

- Local review: `C:/Users/AL AZIZ TECH/AppData/Local/Temp/talky-qa-fixes-20260916`, base `88ebc8f338d4c50e9d7456239a28343afd20561b`, plus the preceding uncommitted remediation.
- Live checkout freshly verified: `2f34c72ecef26827768180019a97b989e4537269`, clean.
- Live schema: `0045_refresh_session_binding`; prepared migration 0046 is not applied.
- Account checks: tenant `1845a165-08aa-4554-bcec-2d31ac523662` only, in read-only transactions with explicit tenant context/predicates.
- Journal statistics cover the five services across the server, not only this account.
- All new finding files listed below have an empty committed diff between the deployed commit and the local base. None of those files was changed by the preceding remediation. Therefore these are not merely bugs in a discarded old branch.
- Database failure/concurrency reproductions used a disposable local PostgreSQL 16 database with synthetic fixtures. External messaging/model providers were mocked. No emails, SMS or calls were sent.

## 1. High: AI key creation, rotation and disable do not take effect in a warm resolver

**Observed:** a resolver with no tenant key continues using the platform fallback after a tenant key is created. A resolver with an old tenant key keeps returning it after rotation. After the new key is loaded and disabled, it still returns that disabled key.

Root cause chain:

1. `backend/app/domain/services/credential_resolver.py:120` reads the process-lifetime cache.
2. Lines 124, 132 and 134 return/cache plaintext credentials or an environment-fallback sentinel without expiry/version checks.
3. `backend/app/api/v1/endpoints/tenant_ai_credentials.py:115` commits creation/rotation, and `:172` commits disable, but neither invalidates this cache.
4. The resolver's invalidation method exists, but the application search found no calls to it for provider-key mutations.

Proof: two real-database diagnostic cases call the actual create/disable endpoint functions and actual resolver. Only explicit test-side invalidation discovers the new key. Keys are synthetic, not production secrets.

Impact: configuration changes can appear successful while calls use stale credentials. “Disabled” is not effective revocation in a running worker. A key cached before an outage can also retain the platform fallback.

Required fix: versioned/expiring resolver state with invalidation that reaches every API and voice/dialer process, plus fail-closed policy for revoked tenant credentials. Clearing one API process's dictionary is insufficient. Test create-after-cache-miss, rotation, disable and multi-process convergence.

Production tenant impact was not proven by rotating or reading any real key.

## 2. High: reminder retry SQL fails and rolls back its retry count

Source: `backend/app/workers/reminder_worker.py:354–355` embeds `$2` inside `interval '$2 seconds'`. PostgreSQL treats that as literal text, not a bound parameter.

Actual local PostgreSQL output from the real worker failure path:

```text
REMINDER_RETRY_SQL: IndeterminateDatatypeError could not determine data type of parameter $2
```

After a simulated provider rejection, the stored reminder remains `pending`, `retry_count=0`. Its attempted processing/retry transaction rolled back.

Impact: failed reminders can be retried on later scans without the intended backoff or exhaustion progression. No production message loop is claimed: this account currently has no reminder rows.

Required fix: parameterized interval construction, committed retry transitions and tests exercising actual PostgreSQL rather than a fake `execute()` that accepts invalid SQL.

## 3. High: two processors can send the same email reminder twice

Source: `backend/app/workers/reminder_worker.py:257` changes the row to processing by ID without an expected-state predicate or claim result. `_send_email_reminder` at line 421 does not carry a durable provider-delivery idempotency contract.

Proof: two concurrent calls to the actual worker for the same real database row resulted in two calls to the mocked email sender. PostgreSQL serialized the row updates, but the second processor still proceeded after the first completed because it never rechecked ownership/state.

Impact: duplicate delivery during overlapping workers or recovery. This is a reproduced concurrency fault, not proof that duplicates have already reached customers.

Required fix: a short durable claim transaction before external I/O, ownership/lease validation and a stable delivery attempt identity. Persist ambiguous provider outcomes and reconcile them; do not blindly resend after an uncertain result. A Python-only lock cannot protect separate processes or restarts.

## 4. High: a best-effort event failure can undo a successful business update

Source chain:

- `backend/app/domain/services/call_summary/store.py:258` qualifies a lead inside a tenant transaction.
- Line 321 emits the lead alert on that same connection.
- `backend/app/domain/services/event_emitter.py:65` performs the event INSERT; line 82 catches its error without recovering the database transaction.

Proof: a real PostgreSQL CHECK failure was deliberately injected into the synthetic event table. The actual `mark_lead_from_summary` returned `True`, but after the transaction the actual stored `leads.is_lead` was still `False`. Catching the Python exception did not undo PostgreSQL's aborted-transaction state.

The existing mock test asserting “alert failure never breaks qualification” misses this database behavior.

Production relevance: 11 `emit_event.failed` entries in the last 24 hours were classified as RLS denials. This proves live event-write failures; it does **not** prove those 11 events came from this lead-marking path or that 11 lead updates rolled back.

Required fix: contain optional event INSERT failures with a savepoint or a durable outbox/after-commit design. Prove the business write remains committed while the event failure remains observable. Trace callers that emit inside a transaction before choosing the shared fix.

## 5. Medium/high: summary failure and concurrency state are still missing

This remains open after the previous schema/token-budget repair.

- `backend/app/domain/services/call_summary/store.py:100` generates outside the read transaction.
- At line 107, a failed result is returned without durable failure/retry state.
- Two simultaneous readers can both see no summary, generate it and write it.
- `backend/app/api/v1/endpoints/calls.py:1672` returns `available=true` even when the returned headline is “Summary unavailable.”

Proof:

- Three sequential failed endpoint reads invoked the mocked summarizer three times, persisted no state and each reported `available=true`.
- Two synchronized concurrent reads invoked generation twice and performed two persistence calls.

Impact: duplicate model spending and misleading UI status; retries are bounded per invocation by the previous fix, but not coordinated across reads or processes.

Required fix: durable pending/running/succeeded/failed state, atomic job ownership, retry count/cooldown, explicit retry behavior and an accurate response envelope. Do not hold a database transaction open across a slow provider request.

Production: this account has 66 transcript-bearing calls without `summary_json` in the last 30 days. Missing summaries are not automatically provider failures because generation can be on demand. Four summary-failure log messages were independently observed platform-wide in 24 hours.

## 6. Medium: failed SMS does not fall back to email

Source: `backend/app/workers/reminder_worker.py:292` uses `elif email`, so email is considered only when there is no phone number, not when an SMS attempt failed. This contradicts the preceding code comment promising fallback.

Proof: a reminder containing both a phone and email, with simulated SMS failure on the last attempt, never calls the email sender and follows the failure path.

Required fix: define an explicit fallback policy. Fall back only after a definitive delivery failure—not an ambiguous timeout that could still have delivered an SMS—and verify recipient consent/preferences. Test missing phone, definitive rejection, uncertain delivery and both channels failing.

## 7. Medium: lead-quality evidence can pass with unknown duration or excluded transcript turns

Source: `backend/app/domain/services/call_summary/store.py:167` ignores `is_final=false` but does not reject `include_in_plaintext=false`. Line 200 only enforces minimum duration when duration is non-null.

Two independent diagnostic inputs pass `_conversation_has_substance`:

1. Three otherwise valid caller turns with `duration_seconds=None`.
2. A 60-second call with three final caller turns explicitly excluded from canonical plaintext.

Impact: the fail-closed evidence gate is weaker than documented. This does not show that a particular production lead was incorrectly qualified.

Required fix: define and use canonical caller turns consistently, require trustworthy duration evidence for this gate, and test historical/unknown-duration records explicitly rather than treating absence of evidence as proof of adequate conversation.

## 8. Medium: reminder database failure looks like an empty healthy scan

Source: `backend/app/workers/reminder_worker.py:218–220` catches database scan errors and returns 0. The outer consecutive-error handling at lines 149–161 therefore does not see these failures. Heartbeat runs independently at line 503.

Proof: a simulated database fetch failure returns the same 0 as “no due reminders.” The worker can remain alive while processing nothing.

Required fix: distinguish an empty scan from a failed scan, surface meaningful processing health, and make sustained errors reach restart/alert policy. A heartbeat should establish liveness, not claim that the work loop is succeeding.

## Fresh production checks

| Check | Observed result |
|---|---|
| API, voice worker, dialer worker, reminder worker, C++ gateway | All active |
| Readiness | ready=true, draining=false, active_sessions=0, max_sessions=50 |
| Synthetic inbound timer | enabled but inactive; no LastTriggerUSec |
| Database head | 0045_refresh_session_binding |
| Historical AI-config backup | RLS=false, FORCE RLS=false; prepared local migration not deployed |
| Account calls in 30 days | 238 total, including 17 tests |
| Transcript present, summary_json absent | 66 calls; not all proven failed generation |
| Account call/campaign direction mismatches | 0 |
| Account retry_scheduled jobs linked to an ended call | 1; requires evidence-based reconciliation |
| Account calls in last 7 days | 7 ended, 2 completed, 1 failed |
| Account reminder rows | 0 |

Journal aggregation used fixed signatures and did not expose transcript content, phone numbers or credentials:

| Last-24-hour signature | Count | Interpretation boundary |
|---|---:|---|
| SQL parameter-binding error | 12 | Old deployed dialer is still logging this error; local repair is not live |
| Event INSERT failure | 11 | All classified as RLS denials; individual transaction consequences not established |
| Summary failure | 4 | Log entries, not unique calls |
| telephony_audio_gap | 538 | Warnings, not 538 failed calls; attribution requires correlated media/provider timing |

No matched signatures appeared in the inspected voice-worker/reminder-worker logs. Gateway journal returned zero entries in this window. Neither observation proves those services have no defects.

## QA executed this turn

### Canonical backend and security suite

```text
python -m pytest tests/unit tests/security -q --disable-warnings
8958 passed, 8 skipped, 1453 warnings in 319.28s (0:05:19)
```

This includes existing credential, tenant/IDOR, recording access/path, refresh/session, billing, dialer recovery, Groq/Cerebras and resilient STT/LLM/TTS tests. It is not a live-provider or live-call certification.

### New defect observations

File: `backend/tests/diagnostics/test_backend_followup_observations.py` in the isolated worktree.

```text
python -m pytest tests/diagnostics/test_backend_followup_observations.py -q -s --disable-warnings
REMINDER_RETRY_SQL: IndeterminateDatatypeError could not determine data type of parameter $2
11 passed in 2.20s
```

**These 11 tests assert the observed broken behavior; passing confirms the defects. They are not acceptance tests claiming the product works.** The file is deliberately outside the canonical unit/security directories. Convert the relevant assertions into desired-behavior regression tests when implementing fixes.

The local database was named `talky_qa_followup_20260916`; each database case created a unique retained evidence schema. Its loopback-only PostgreSQL server was stopped afterward.

### Static checks

```text
ruff check app/ --select F --extend-ignore F401,F841
All checks passed!

rls_acquire_inventory.py --fail-on-needs-tenant --only needs_review
total=445, ok=398, needs_tenant=0, needs_review=47
GATE PASS: 0 needs_tenant site(s)
```

The 47 review entries are not 47 proven bugs. Spot checks found legitimate platform-admin bypasses and cleanup helpers that set transaction-local context. They also demonstrate why the static gate cannot replace testing delegated SQL and transaction failures.

## Fix order and acceptance

1. Integrate/deploy the already tested SQL, billing and data-protection repairs through the supported release path; recheck current main and migration heads first.
2. Make credential mutations effective across processes; prove a disabled key cannot be returned for a new session.
3. Repair optional event transaction isolation; prove the business update survives an event RLS/constraint failure.
4. Repair reminder retry SQL, durable claims/idempotency and processing health as one connected workflow; then define safe channel fallback.
5. Add durable summary state and coordinated generation; return truthful availability.
6. Tighten canonical lead-quality evidence, without inventing missing duration or claiming a lead on excluded turns.
7. Reconcile the historical completed-call/retry-job mismatch only from durable provider/call evidence.
8. Enable and prove the approved synthetic monitoring path; investigate correlated audio-gap warnings with controlled calls.

## Still left beyond these findings

- Previous changes are still uncommitted/unpushed and not deployed. Vercel is unchanged too.
- Privileged deployment and synthetic configuration access still need the approved operator path; no sudo workaround was used.
- Local recording backup/restore/retention has not been demonstrated.
- Real browser Test Agent three-minute failure, sustained inbound/outbound conversations, email capture persistence and audio quality still need controlled acceptance runs.
- Voice email/callback/form/transfer executors remain unavailable by design; do not confuse connector connectivity with working voice actions.
- No password/token rotation, customer messages, calls, production migration, job retries or application fixes occurred in this audit.
- No new frontend QA was run this turn; the request was backend-focused, and frontend source was unchanged.

The appropriate status is **core services running; substantial automated coverage passing; eight further backend defect areas confirmed; release and end-to-end acceptance still outstanding**.
