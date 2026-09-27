# Additional QA findings — 17 September 2026

## Outcome

Two additional application defects reproduced, one latent storage-helper defect reproduced, and existing operational blockers freshly reconfirmed. No application code or production state changed. This is diagnosis, not a release-readiness certification.

Read-only production observations: approximately 21:53–21:57 UTC on 16 September (02:53–02:57 PKT on 17 September). Production remains clean at `2f34c72ecef26827768180019a97b989e4537269`, schema `0045_refresh_session_binding`. The preceding remediation worktree is based on `88ebc8f3`, contains uncommitted fixes and has not been deployed.

## 1. High — database compatibility calls block the API event loop

Root cause: `backend/app/core/postgres_adapter.py:197` executes synchronously before returning its awaitable wrapper. At line 217, it submits work to an executor and immediately calls blocking `.result()`. RPC repeats this at line 823. Adding `await query.execute()` does not fix it because execution has already blocked before the wrapper is awaited. The adapter also opens a new asyncpg connection per query at line 224 rather than using its supplied pool.

Reproduction used the actual QueryBuilder execution path, replacing only database work with an async 50 ms delay. A callback scheduled for 10 ms was delayed by four queries:

```text
Frozen deployed source: public await execute callback = 248.50 ms
Genuinely asynchronous control callback = 15.13 ms
Remediation worktree: public await execute callback = 250.01 ms
Genuinely asynchronous control callback = 15.80 ms
```

Production `talky-api` has one uvicorn worker, as required by the current process-local telephony design. Connector endpoints are concrete async consumers of this blocking adapter. A slow dashboard query can therefore delay other tasks in that API process, including live telephony work. This is a reproduced mechanism, **not proof that it caused any particular live audio gap or historical disconnect**.

Fix direction: introduce genuinely asynchronous, pooled execution and migrate async consumers with an explicit contract; keep synchronous callers separate. Verify heartbeat latency under slow queries and preserve tenant context, error handling and ignored-return-value write behavior. Merely adding `await`, enlarging the executor, or increasing uvicorn workers is not a valid root-cause fix.

## 2. High — connector disconnect reports success after database failure

Source: `backend/app/api/v1/endpoints/connectors.py:554–584` and `:1066–1094`. The PostgreSQL adapter converts exceptions to `PostgrestResponse(error=...)` at `postgres_adapter.py:268`. These disconnect handlers do not inspect those errors. Their independent account/connector deletions also do not share an atomic transaction.

Actual endpoint functions with synthetic error responses produced:

```json
{"lookup_failure":{"success":true,"message":"Nothing to disconnect","removed":0}}
{"both_deletes_fail":{"success":true,"message":"Connector disconnected","removed":1}}
{"legacy_both_deletes_fail":{"success":true,"message":"Connector disconnected"}}
```

Impact: an operator can be told a connector was disconnected while its account and stored credentials remain. Lookup failure is also misrepresented as an empty successful result. No live connector was disconnected and no production occurrence is claimed.

Fix direction: transactional tenant-scoped disconnect, verified write results and explicit failure responses. Test lookup failure, either deletion failing, all deletions failing, repeated disconnect, concurrent refresh/reconnect and separate provider cards. Define external-provider token revocation separately; deleting a local row is not proof of provider-side revocation.

## 3. Lower priority, latent — legacy storage path containment is incorrect

Source: `backend/app/core/postgres_adapter.py:971–978` checks a resolved path using string `startswith(bucket_root)`. A sibling directory with the same prefix is accepted:

```text
recordings / ../recordings-sibling/proof.wav
accepted_outside_bucket = true
read_or_write_performed = false
```

Only path resolution was exercised; no file was read or written. The current public recording stream uses a separate realpath/commonpath check, and the legacy recording writer sanitizes generated identifiers. This finding is **not a demonstrated public path-traversal exploit**. Repair the helper with path-component containment and test sibling prefixes, symlinks/junctions and ordinary paths.

## 4. Operational — retention is not deleting expired telemetry

The cleanup timer is enabled, active and successfully executing, but its actual log says `dry_run=True`. Its 16 September run would delete 137 call_events and 79 call_legs older than 90 days, but deletes nothing.

This is an intentional safety default, not an accidental missing timer. It becomes a retention/capacity gap if operators assume cleanup is enforced. The source explicitly requires policy approval before destructive mode. Do not enable it without confirming retention and legal-hold requirements; do not silently extend its allowlist to recordings or calls.

## 5. Recording recovery remains unproven

All 141 local recording files for this account exist, are nonempty and match metadata sizes. Their metadata still says storage_provider=s3 and status=uploaded, although the bucket is local; the preceding local migration fixes that label only.

Nightly DB backups are working: the latest journal reports a 174,314,361-byte dump with 112 TABLE DATA entries and checksum verification. That script backs up PostgreSQL, not the separate recording audio files. No recording backup/restore proof or off-host copy was found in the inspected application units/scripts. This is a missing recovery assurance, not evidence that no external host snapshot or operator-managed backup exists. No restore test was run.

## Existing blockers freshly reconfirmed

| Item | Fresh evidence |
|---|---|
| Earlier fixes not live | Production HEAD `2f34c72e`; migration 0045, not prepared 0046/0047 |
| Account inbound setup incomplete | Three draft inbound base campaigns, zero inbound campaign configurations, zero account-owned assignments |
| Verified DID assigned elsewhere | Actual availability service returns available=false, reason=reassignment_required, owned_by_current_tenant=false; other owner's identifier remains hidden |
| Inbound watchdog inactive | Timer enabled but inactive, LastTriggerUSec empty; no next scheduled execution |
| Stale retry job | Call `bb8e6dc2…` ended at attempt 2; job `e520b2a2…` remains retry_scheduled at attempt 1, last_error=tenant_gap, last_outcome=null |
| Billing visibility defect still present | Actual quota function without tenant context: 0 used / 5000 remaining; with context: 24 used / 4976 remaining |
| Backup tenant table not protected | tenant_ai_configs_backup_20260907: RLS false, FORCE false, zero policies; preceding repair migration not applied |

## What was healthy, and limits

- API, voice worker, dialer worker, reminder worker, gateway and Asterisk all active. API automatic restart count was zero.
- Deep health: ready=true, database=ok, Redis=ok.
- Active account trunk registered with fresh runtime evidence. Two intentionally inactive trunks were reported inactive, not counted as failures.
- Account has no nonterminal calls and no observed call/campaign tenant-direction mismatches or inbound-linked outbound leads/jobs.
- Four telephony_audio_gap signatures across the API journal in the last 24 hours; no traceback signatures in the five inspected service journals. These counts are platform-wide, not attributed to this account.
- Latest real account call remains 15 September. Quiet services therefore do not prove today's calling works.
- Gmail/calendar access credentials are expired but refresh credentials exist. This is not sufficient evidence of a broken integration; no provider refresh/send was attempted.
- Disk is 58% used, approximately 20 GB available; no present disk-full incident established.

## Evidence and reproducibility

Diagnostic script: `scripts/archive/2026-09-17-additional-qa-probe.py`, invoked with either the frozen production backend or remediation backend path. It uses synthetic responses and timers; no external requests, deletes or credential reads.

SHA256 of the production files matches the frozen local files exactly:

```text
postgres_adapter.py 012685e51c160e0f1ee13ea0f5c391e7ee1c0e6e27f9e3ec5dda0a2dbc93e074
connectors.py       27e808b3a8b9feac52ca6cfb4a94c98af28e29f9a6f8e516dcb144ce6bc53c78
```

The two files are also unchanged in the committed production-to-remediation-base diff and have no local implementation edits. These new findings therefore survive the previously prepared repairs.

Not run this turn: full canonical suites, live browser acceptance, real calls/transfers, sends/payments, external token refresh, backup restoration or Linux media tests. No release claim is made; previous-turn test totals are not used as fresh evidence. No code fixes, deployments, migrations, restarts or ownership changes were performed.
