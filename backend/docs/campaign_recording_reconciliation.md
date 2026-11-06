# Campaign counters and recording discrepancies — F14-04

Both tools are local operator commands. Supply `DATABASE_URL` through the process environment; they do not choose a production host, send calls or invoke a storage provider. Counter apply also uses the existing audit-signing settings loader. No production repair or file scan was run during this remediation.

## Counter contract and safe repair

Campaign `calls_completed` and `calls_failed` count **leads reaching a final outcome**, not call attempts. The repair counts each lead at most once. It does not change `total_leads`, lead/job/call rows or outcome policy.

The current CallService contract routes final `spam`, `invalid`, `unavailable`, `disconnected` and `rejected` to `calls_failed`. Other recognised final outcomes route to `calls_completed`, including `busy`, `no_answer`, `timeout` and `failed` after retry exhaustion. A separate dialer path counts terminal **pre-answer** failures as failed. `called` and `contacted` alone do not prove finality.

The repair uses each lead's latest tenant/campaign-owned job and call evidence. Active/retry work is not counted. Deleted leads and unattempted cancelled/skipped/blocked work are excluded. Terminal jobs with clear outcomes and jobless settled calls can be classified. Missing/unknown/conflicting historical evidence is reported by lead UUID and reason, without phone/audio/note content; any such row blocks that campaign's apply. In particular, mixed newer pre-answer errors with an older job outcome are not guessed. A missing terminal-settlement marker can also block apply; the tool does not fabricate it.

`after` and `delta` in a blocked preview describe only proven buckets and **must not be applied manually as a complete total**. A campaign in a running/active/starting state, with active jobs, or with unsettled calls cannot be applied. The operator must stop affected writers and confirm quiescence before using apply; status checks alone do not quiesce external writers. Serializable transactions, campaign/lead row locks and bounded query/lock timeouts detect or reject conflicting work. A conflict exits visibly; the tool does not automatically replay writes with an uncertain commit result.

Each changed campaign commits its counter values and a durable audit receipt together. Audit failure rolls back that campaign. Previously committed campaigns remain committed if a later campaign fails. Repeating a completed repair does not create another change or audit receipt. The database operator role must be able to make an unfiltered inventory; `row_security=off` fails visibly if its privileges would produce a misleading filtered result. Every business query independently binds the supplied tenant and campaign.

Run from the deployed backend directory with owner-provisioned process environment, replacing the UUID placeholders:

```bash
cd /opt/talky/backend
TENANT_ID='replace-with-tenant-uuid'
CAMPAIGN_ID='replace-with-campaign-uuid'
venv/bin/python scripts/repair_campaign_counters.py --tenant "$TENANT_ID" --campaign "$CAMPAIGN_ID"
```

Review the before/after/delta and `blocked` field. For a quiesced campaign with complete evidence, the explicit audited write is:

```bash
venv/bin/python scripts/repair_campaign_counters.py --tenant "$TENANT_ID" --campaign "$CAMPAIGN_ID" --apply
venv/bin/python scripts/repair_campaign_counters.py --tenant "$TENANT_ID" --campaign "$CAMPAIGN_ID" --apply
```

The second command must report zero deltas and `applied: false`. Omit `--campaign` to iterate that one tenant's campaigns in bounded pages; there is deliberately no implicit cross-tenant counter apply. Exit 0 means the selected campaigns were processed without a blocked result, 2 means at least one was blocked, and 1 means an exception interrupted processing. On a connection/commit error, inspect receipts/current values before rerunning.

## Nightly local recording report

`report_recording_discrepancies.py` compares regular files beneath `LOCAL_RECORDINGS_DIR` with local `recordings_s3` rows and optional legacy `recordings.storage_path` rows across all tenants, using explicit tenant predicates for each row page. Legacy ownership is bound through the call; NULL legacy tenant IDs are accepted only through that parent. A conflicting explicit tenant ID is reported as an ownership conflict: the inventory is incomplete and orphan classification is withheld, because silently excluding that row could falsely classify its file. The report never opens audio contents, modifies a row, attaches a file, deletes a file or invokes S3. All row statuses establish ownership of an existing file; only `uploaded` rows are expected to have a playable file. Failed/pending rows are counted separately. Remote object references are excluded and legacy URL references are counted separately; cloud/Drive existence is not checked.

The JSON includes counts and bounded samples of files without rows, uploaded rows without files, invalid paths and scan issues. It rejects outside-root row paths, skips symlinks/junctions and does not follow directory links. A missing root is an incomplete report, not a mass missing-file assertion. If row inventory is truncated, it does not label files orphaned; if directory scanning is incomplete, it does not label unobserved files missing. Default caps are 100,000 rows, 100,000 files, 10,000 tenants, 500-row pages and 1,000 samples per discrepancy category. `samples_truncated` distinguishes complete counts from shortened lists.

The DB and filesystem are not one atomic snapshot. `snapshot_consistent` is therefore false even when scanning completes. A newly written but not-yet-registered file or a concurrent retention deletion can appear transiently. Investigate and repeat against a quiesced storage writer before deciding a difference is a durable orphan. No result is a deletion recommendation.

```bash
cd /opt/talky/backend
venv/bin/python scripts/report_recording_discrepancies.py
```

Exit 0 means a complete report with no differences; 1 means differences were found; 2 means a scan/configuration/database failure or incomplete inventory. Logs omit raw driver error text and outside-root paths. In-root filenames/UUIDs remain operator metadata; restrict journal access appropriately.

The installer links the new service/timer and enables **and starts** `talky-recording-discrepancy.timer`. It triggers the read-only service nightly at 03:30 server-local time with `Persistent=true`. The service runs as `talky` with a read-only filesystem sandbox and a ten-minute deadline. A custom recording directory must already be readable by that runtime identity. The owner verifies installation and a local rehearsal after deployment:

```bash
systemctl status talky-recording-discrepancy.timer --no-pager
systemctl start talky-recording-discrepancy.service
journalctl -u talky-recording-discrepancy.service --since today --no-pager
```

A nonzero service exit is intentional when differences or incomplete evidence exist. Alert delivery is the separate item-16 configuration. No real Linux/systemd, PostgreSQL or production filesystem run is claimed by the fixture tests.

## Remaining owner verification

- The original 22 campaigns and six files were not inspected or repaired. Run the owner tools on the approved host, review ambiguities and audit receipts, and retain before/after evidence.
- Resolve missing/conflicting history from authoritative evidence before any blocked campaign is changed. Do not turn the preview's proven-only counts into an unguarded SQL update.
- Verify the deployed role's inventory permissions, actual recording-root visibility, timer activation and journal retention. The new job includes optional legacy local paths, but reports local files only; cloud/Drive object reconciliation is not implemented here.
- No file attachment/deletion or automatic counter repair is scheduled. The latest user prompt explicitly overrides the older QA suggestion to delete unmatched files.
