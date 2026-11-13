# DSAR export and bounded retention — F12-06

This release prepares an audited platform-admin export and erase **preview**. It does not implement destructive DSAR erasure or hash-only suppression, and it does not close F12-06 in full.

## Export and preview

The API exposes `POST /api/v1/admin/dsar/preview` and `POST /api/v1/admin/dsar/export`. Both require platform-admin authentication and a fresh database authority check. The CLI uses the same service and requires a current platform-admin actor UUID. Preview writes an audit receipt, but does not change subject rows or files.

Choose one tenant and exactly one phone (including `+country code`), email, or `whole_tenant: true`. The operational registry covers leads and their notes/custom fields, matching calls and inline content, captured contacts, transcript/conversation rows, recording metadata, feedback audio/transcripts, conversation reviews, CRM sync receipts, tenant-owned DNC rows, guard decisions, call events/legs, stream events, dialer jobs, assistant actions, meetings and reminders. Confirmed captured email/phone values can resolve their call and lead. A person export does not include another tenant's rows or platform-global DNC entries. Nullable legacy transcript/recording tenant IDs are resolved through the owning call; conflicting tenant IDs are excluded.

The private ZIP contains JSONL rows, supported local/S3 media bytes, hashes and a manifest. It is **not an encrypted archive**. The API sends it as a non-cacheable attachment and removes its private temporary directory after streaming, malformed Range responses, or interrupted sends. CLI output is created exclusively with mode `0600` on POSIX and never overwrites an existing file. Keep that file in an operator-controlled directory and use an approved secure delivery channel.

Archive writing yields between bounded rows and 64 KiB audio chunks, so large exports do not monopolize the API event loop. Cancellation unwinds the same ZIP writer and removes its partial output; there is no detached archive-writing thread.

Read the manifest before describing an export as complete:

- `complete` means the declared operational registry and supported artifacts were exported within the stated limits. It is not a full legal DSAR-completion claim.
- `full_dsar_complete` is always false in this release. Whole-tenant requests additionally include `tenant_scope_not_complete`.
- `missing_tables`, `issues`, `counts`, `row_files` and `artifacts` expose unavailable schema, missing media, unsupported Drive storage, truncation and hashes. No failed media read is represented as a complete audio member.
- Defaults are 250 rows per table, 64 KiB per row, 16 MiB of row data, 64 MiB per artifact and 256 MiB of artifacts. Row limit can be raised by the CLI up to 10,000; other caps still apply. An incomplete result needs a reviewed follow-up, never automatic erasure.
- Queries use read-committed transactions. Live writers are not quiesced; this manifest is not a deletion approval token. Historical noncanonical identifiers and arbitrary mentions inside free-text content are not inferred into additional identities.
- Immutable audit/media/financial/inbound/legal-acceptance records, remote CRM/Drive copies and backups remain separately disclosed scopes. No provider credentials table is exported.

Run from the deployed backend directory in an operator-provisioned environment containing the existing database and audit signing configuration. These examples do not print or source configuration files. Replace the UUID placeholders before use; the CLI rejects them as written.

```bash
cd /opt/talky/backend
TENANT_ID='replace-with-tenant-uuid'
ACTOR_ID='replace-with-current-platform-admin-uuid'
EXPORT_PATH='/private/operator/directory/dsar-export.zip'
venv/bin/python scripts/dsar.py preview --tenant "$TENANT_ID" --actor "$ACTOR_ID" --phone '+14155552671'
venv/bin/python scripts/dsar.py export --tenant "$TENANT_ID" --actor "$ACTOR_ID" --email 'person@example.invalid' --output "$EXPORT_PATH"
venv/bin/python scripts/dsar.py export --tenant "$TENANT_ID" --actor "$ACTOR_ID" --whole-tenant --output '/private/operator/directory/tenant-export.zip'
```

The phone/email above are synthetic examples. There is no `erase` command or `--apply` option. A successful CLI exit means the requested preview/archive was produced; inspect its completeness fields.

## Retention subset and proposed windows

The worker's fixed table registry is `call_events`, `call_legs`, `stream_events`, `transcripts`, `conversations` and `call_guard_decisions`. Counts/deletes bind the tenant and one fixed UTC cutoff. Optional absent tables are reported/skipped. Missing hold schema prevents unchecked apply. Each delete transaction takes the same tenant hold lock used by compliance-hold creation, then rechecks the hold; a hold created after counting still prevents deletion.

The owner must review the proposed windows before enabling personal-data deletion:

| Target | Proposed default | Configuration |
| --- | --- | --- |
| Call events / legs / stream events | 90 days | Existing `CLEANUP_CALL_EVENTS_DAYS`, `CLEANUP_CALL_LEGS_DAYS`, `CLEANUP_STREAM_EVENTS_DAYS` |
| Standalone transcripts | 365 days | `CLEANUP_TRANSCRIPTS_DAYS` |
| Conversations | 365 days | `CLEANUP_CONVERSATIONS_DAYS` |
| Guard decisions | 90 days | `CLEANUP_GUARD_DECISIONS_DAYS` |
| Recordings | Existing actual tenant recording policy | Separate existing recording purge |
| Audit rows | Existing category retention, currently 1/3/7 years | Not pruned by this release |

These worker windows are proposed operational settings, not a change to purchased-plan entitlements. The existing plan model's Basic 90-day and Professional/Enterprise 365-day transcript defaults are not automatically mapped onto the worker; that product/policy mapping remains an owner decision.

`CLEANUP_DRY_RUN` remains true by default. New personal targets additionally require `CLEANUP_PERSONAL_RETENTION_ENABLED=true`; an existing installation already applying telemetry cleanup does not silently enable the new targets. `dry_run_tables` distinguishes preview counts from actual deletes in a mixed run. Batch limits are at most 5,000 rows and 200 batches **per table across all tenants in one run**, with bounded tenant-directory pages. `limited_tables` means a run cap was reached; remaining eligible rows need a later pass. Held/busy tenants are reported separately.

Preview all table targets and recordings, without deleting either:

```bash
cd /opt/talky/backend
CLEANUP_DRY_RUN=true CLEANUP_PERSONAL_RETENTION_ENABLED=false RECORDING_RETENTION_PURGE_ENABLED=false venv/bin/python -m app.workers.cleanup_worker
```

Only after the owner approves the windows and reviews the preview, an explicit table-only apply can use:

```bash
CLEANUP_DRY_RUN=false CLEANUP_PERSONAL_RETENTION_ENABLED=true CLEANUP_TRANSCRIPTS_DAYS=365 CLEANUP_CONVERSATIONS_DAYS=365 CLEANUP_GUARD_DECISIONS_DAYS=90 RECORDING_RETENTION_PURGE_ENABLED=false venv/bin/python -m app.workers.cleanup_worker
```

The numbers remain proposals until approved. Keep recording-object deletion on its existing independent gate, actor and durable media-deletion workflow. Missing `recordings_s3`, `tenant_recording_policy`, hold or tenant tables are logged and skipped before its data query. Storage deletion is not introduced by the new table-retention flag.

The installed scheduler is `talky-cleanup.timer` / `talky-cleanup.service`, with backend working directory `/opt/talky/backend` and interpreter `venv/bin/python`. Owner verification after deployment:

```bash
systemctl status talky-cleanup.timer talky-cleanup.service --no-pager
journalctl -u talky-cleanup.service --since today --no-pager
```

No commands in this document were executed against production by this remediation.

## Explicit remaining work

- Destructive person/tenant erasure and persisted hash-only DNC suppression with actual CallGuard matching are not implemented. No suppression-key custody or rotation decision is assumed.
- Calls themselves are never changed by this retention pass: inline transcript/summary fields remain. Drive-backed transcript rows are retained to avoid orphaning external files. The legacy/external copies require a reviewed deletion capability.
- Audit-chain pruning/redaction, immutable media and financial/inbound receipts, and append-only `legal_acceptances` from migration `0085` need an explicit retained-evidence design. Their guards are unchanged. Existing `call_events` retention uses its supported privileged DELETE path; compliance holds are still checked under the tenant hold lock.
- Full-tenant table coverage, concurrent/live voice-writer exclusion and restoration/erasure replay are not proven. Protected voice paths need an owner/Claude handoff before a live erase is offered.
- Backup expiry is required to be 30 days. Actual lifecycle configuration and a restore drill that reapplies recorded erasures before service resumes remain owner verification; documentation is not enforcement proof.
- The current cleanup worker retains its pre-existing scheduler-wide session advisory lock. The new per-tenant hold/delete locks are transaction-scoped; this change does not establish scheduler exclusivity under PgBouncer transaction pooling.

Local test evidence is recorded in the round-3 QA report. Passing fixture tests do not constitute an owner retention-window approval, a production drill, or a release approval.
