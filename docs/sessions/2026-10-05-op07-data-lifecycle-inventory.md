# OP07 data lifecycle: repository inventory and remaining acceptance

Date: 5 October 2026. Reviewed integration candidate: `5d091528`.
This is a source inventory within OP07/F34/G02. It is not an inventory of a
deployed host, a retention-policy approval, or evidence of completed customer
export/deletion. No customer data, storage policy, provider account or backup was
changed or accessed during this review.

## Verified storage boundaries

| Data/store | Existing identity and access boundary | Existing lifecycle and unresolved evidence |
|---|---|---|
| Recording bytes in S3-compatible storage | `RecordingService._s3_key` uses tenant/campaign/call segments. `recordings_s3` retains the actual bucket/key; permissioned endpoints select the current tenant's recording. | The service describes bucket lifecycle as external configuration. Code support does not prove AWS hosting, an installed expiry rule, noncurrent-version expiry, encryption configuration or successful retention. Permanent deletion checks versions and absence for the exact object. |
| Recording bytes on local disk | `LOCAL_RECORDINGS_DIR` and resolved-path containment; metadata identifies `s3_bucket='local'`. New private creation and symlink boundaries are documented in the [OP11 repair](2026-10-05-op11-recording-privacy.md). | Permissioned deletion removes the selected file using a durable deletion intent. No scheduled local recording expiry was found in the inspected worker/service paths. Existing filesystem permissions and production process identities remain unverified. |
| Recording metadata and deletion receipts | `recordings_s3` tenant/call ownership; `media_deletion_intents` and related state in the existing Admin/tenant deletion workflow. | Storage deletion and metadata completion are separate acknowledged steps. Legal holds, partial failure and idempotent retry must remain effective. Removing one recording does not establish deletion of all data for a caller or tenant. |
| Call transcripts | `TranscriptService._write_calls_transcript` writes tenant-scoped `calls.transcript`, `calls.transcript_json` and `transcripts`. Saved-state and retry evidence are covered by AG05. | A recording deletion does not delete these text copies. The telemetry worker deliberately excludes `calls` and `transcripts`. Full request selection, approved exceptions and retention execution remain open. |
| Leads, conversation and action records | The canonical schema includes `leads`, `conversations`, `assistant_conversations` and `assistant_actions`; later migrations add action/contact evidence. AG05/AG06 bind current tenant and original action/connector identities. | A request must enumerate the relevant records rather than assume the recording ID is the complete data boundary. Durable action/accounting evidence has preservation requirements. Approved selection and exception rules remain pending. |
| Reviewer feedback | Existing Admin media endpoints separately expose feedback audio/transcription and permissioned deletion. | Feedback copies need their own selected resource identities and outcome receipt. A recording-only completion cannot claim feedback removal. |
| Telemetry | `cleanup_worker.py` has a fixed allowlist: `call_events`, `call_legs`, `stream_events`. | Default is dry-run. Defaults are 90 days with separate environment overrides. Counts and deletes use transaction-local RLS context; batches and work are bounded. This worker neither removes recordings nor enforces transcript/lead retention. Installed timer, effective settings, exact selected rows and approved execution remain unverified. |
| Database backups | `backend/deploy/db-backup.sh` creates a logical archive plus checksum in a restricted directory, with configurable archive retention (default 30 days). | This does not prove off-host copies, encryption/key recovery, an installed timer, recoverable rows or removal from every historical copy. OP10 still requires an authorized restore and deletion reconciliation before restored data becomes accessible. |
| Downloaded/exported copies and external services | Existing recording download, connector and provider boundaries can transmit data outside these stores. | The deployed destinations, recipient copies, supplier retention, contracts and request handling must be inventoried by the accountable operator. This repository review does not establish deletion from a browser, customer-connected account, supplier or backup. |

Relevant implementation: `backend/app/domain/services/recording_service.py`,
`backend/app/api/v1/endpoints/recordings.py`,
`backend/app/api/v1/endpoints/admin/media.py`,
`backend/app/domain/services/transcript_service.py`,
`backend/app/workers/cleanup_worker.py`, `backend/database/complete_schema.sql`,
and `backend/deploy/db-backup.sh`. This table records the inspected boundaries;
runtime caches, knowledge attachments, diagnostics and any deployed auxiliary
store still require the designated operator's complete deployment inventory.

## Confirmed retention promise gap

The account Security page claims that plan retention is applied automatically,
recordings are removed after that window, and each recording displays remaining
retention. The inspected implementation does not establish those statements:

- `get_retention_config_for_plan` and `is_recording_accessible` have model/test
  references but no application callers in the repository search.
- Recording policy `retention_days` is stored/read; that alone does not schedule
  deletion or prove a matching bucket lifecycle rule.
- `RecordingUrlResponse.retention_days_remaining` defaults to `None`. The URL
  endpoint does not populate it, and the recording list does not provide a
  remaining-retention field.
- The existing cleanup worker operates only on the three telemetry tables above.

The bounded Security copy correction removes the automatic-expiry and countdown
promises and directs the customer to confirm their account policy and review
stored audio. Independent LLM-agent read review confirmed this source finding and
the replacement copy. No new retention policy, timer, export product or deletion
framework is introduced. The full retention feature remains unfinished.

The copy-only change passed the installed ESLint check for
`src/app/security/page.tsx` (exit 0). It was independently reviewed; no new test
or browser/deployed acceptance is claimed for these text changes. The preceding
whole-dashboard suite passed 774 checks with two database-dependent skips on
`d058cd23`, before this copy correction; its source snapshot remained unchanged
during that run.

## Existing privacy content still needs owner reconciliation

`Talk-Leee/src/app/privacy/privacy-content.ts` still asserts AWS S3 storage,
Supabase hosting, specific retention periods and irreversible deletion at expiry.
Its provider descriptions omit OpenAI and Cerebras from the listed inference
suppliers. Repository support for an adapter or S3-compatible endpoint is not
proof that a particular deployment uses it. The plan's earlier production
observations are historical and were not refreshed here.

The privacy owner must provide the deployed supplier/store inventory and approved
retention, disclosure, export/deletion and exception rules. Contract, transfer,
encryption, region and response-deadline claims require their own evidence; this
code review neither approves those claims nor rewrites legal obligations from
assumptions. CP01/G02 and OP07 therefore stay open.

## Bounded next acceptance

1. Assign the existing request inbox/operator and approve a designated synthetic
   tenant, recording objects and applicable retention/exception rules.
2. Enumerate exact IDs/keys and all relevant stores before any mutation; retain
   the reviewed dry-run selection and verify another tenant stays excluded.
3. Reuse the existing permissioned recording deletion workflow for audio. Record
   acknowledged completion, already-absent objects, legal holds and incomplete
   storage/metadata steps separately. Exercise retry after a partial failure.
4. Complete the requested transcript/lead/other-store operation using the existing
   authorized support process. Do not report whole-request completion from the
   audio receipt alone; unresolved stores or exceptions remain explicit.
5. Verify the installed telemetry and recording lifecycle independently. During
   OP10 restore, reconcile prior deletion requests before reopening access.

No release/scenario gate is closed by this inventory. Actual lifecycle execution,
full export/deletion acceptance and approved policy remain outstanding.
