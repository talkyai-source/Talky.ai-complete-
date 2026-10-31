# Call feedback audio: durable-first synchronous transcription

Status: implemented 2026-08-22  
Decision: Approach A — commit the recording, then transcribe in the same request

## Outcome

`POST /api/v1/calls/{call_id}/feedback` accepts one short reviewer audio note.
It stores the audio object and commits a tenant-scoped `call_feedback` row before
making any Deepgram request. Deepgram success changes the row to `done`; timeout,
rate limit, configuration, or provider failure changes it to `failed`. In either
case the endpoint returns the durable note.

The core invariant is:

> If the API returns a feedback object, the recording and its database row are
> already durable. Transcription is useful derived data, not the source record.

Production therefore requires S3-compatible storage. The local-file path is a
development fallback only; acknowledging container-local data as durable in
production would make the invariant untrue after a deployment or restart.

## Scope and non-goals

Included:

- One audio note per reviewed call.
- Tenant ownership and RBAC enforcement.
- S3-compatible storage under a separate `feedback/` key prefix.
- Deepgram Nova-3 prerecorded transcription in the upload request.
- Explicit `pending`, `done`, and `failed` states.
- Idempotent replay of the same audio and a retry endpoint.
- Retrieval of metadata and authenticated audio playback.

Not included:

- A job queue, worker, webhook, or UI polling loop.
- Multiple notes or threaded reviewer comments on one call.
- Editing/replacing an existing note. A different second blob gets `409`.
- Transcribing call recordings; this feature only transcribes the reviewer's
  newly uploaded note.

## Request sequence

```text
Browser                    API                   Storage          PostgreSQL       Deepgram
   | POST multipart         |                       |                  |               |
   |----------------------->| validate/auth         |                  |               |
   |                        | verify call+tenant --------------------->|               |
   |                        | upload audio -------->|                  |               |
   |                        |<------ object key ----|                  |               |
   |                        | INSERT pending ------------------------->|               |
   |                        |<============= COMMIT ====================|               |
   |                        | claim attempt -------------------------->|               |
   |                        | POST /v1/listen ---------------------------------------->|
   |                        |<---------------- transcript or error -------------------|
   |                        | UPDATE done/failed --------------------->|               |
   |<----- 200 + row -------|                       |                  |               |
```

The database transaction is not held open during the Deepgram call. This keeps
pool use bounded and makes the commit boundary unambiguous.

Object storage and PostgreSQL cannot share a transaction. If object upload
succeeds but row insertion fails, the API deletes that just-uploaded object as
a compensating action. Object keys contain a generated feedback UUID, so an
unusual failed cleanup is safe for a later orphan reconciliation job to identify.

## API contract

### Submit

```http
POST /api/v1/calls/{call_id}/feedback
Content-Type: multipart/form-data

audio=<blob>                    required
duration_seconds=12.4           optional, 0..300
```

Accepted MediaRecorder/container types:

- `audio/webm` and `video/webm`
- `audio/ogg` and `application/ogg`
- `audio/mp4` and `video/mp4`
- `audio/mpeg`
- `audio/wav` and `audio/x-wav`

Codec parameters such as `audio/webm;codecs=opus` are stripped before
validation. The default maximum is 10 MiB and is configurable through
`CALL_FEEDBACK_MAX_AUDIO_BYTES`.

Example successful transcription:

```json
{
  "id": "805c8b3b-600d-4a70-a7d4-15dd3d7deca1",
  "call_id": "00d07b52-87ca-430d-8e71-dcf42a0a9f3c",
  "audio_url": "/api/v1/calls/00d07b52-87ca-430d-8e71-dcf42a0a9f3c/feedback/audio",
  "audio_mime_type": "audio/webm",
  "audio_size_bytes": 184220,
  "duration_seconds": 29.8,
  "transcript": "The customer wants a callback on Monday morning.",
  "transcript_status": "done",
  "transcript_error": null,
  "transcription_attempts": 1,
  "retryable": false,
  "created_at": "2026-08-22T10:15:30Z",
  "updated_at": "2026-08-22T10:15:31Z"
}
```

Example provider failure — still HTTP `200` because the note is saved:

```json
{
  "transcript": null,
  "transcript_status": "failed",
  "transcript_error": "Deepgram timed out after 10 seconds",
  "transcription_attempts": 1,
  "retryable": true
}
```

The abbreviated example above omits unchanged metadata fields.

HTTP errors mean the note itself was not accepted or the action is not allowed:

| Status | Meaning |
|---|---|
| `400` | Empty audio |
| `403` | Missing tenant context or insufficient call permission |
| `404` | Call does not exist in this tenant |
| `409` | A different note already exists, or a retry is already running |
| `413` | Audio exceeds the configured limit |
| `415` | Unsupported container/MIME type |
| `503` | Durable object storage is unavailable |

### Read metadata

```http
GET /api/v1/calls/{call_id}/feedback
```

Returns the same response model. The endpoint does not start transcription and
there is no polling requirement.

### Read audio

```http
GET /api/v1/calls/{call_id}/feedback/audio
```

The API tenant-checks the call and note, then redirects to a short-lived S3 URL.
Development-local audio is returned directly.

### Retry transcription

```http
POST /api/v1/calls/{call_id}/feedback/transcription/retry
```

The retry claims a `failed` row atomically, reloads the original audio, and calls
the exact same prerecorded transcriber. It also recovers a `pending` row whose
attempt is older than `CALL_FEEDBACK_PENDING_RETRY_AFTER_SECONDS` (30 seconds by
default), covering process termination or a failed final status update. A fresh
pending attempt gets `409`; an already-done note is returned unchanged.

## Idempotency and concurrency

The table has `UNIQUE(call_id)`, because the product constraint is one reviewer
note per call. The service also stores the blob's SHA-256 digest:

- Replaying the same blob returns the existing row without another upload or
  Deepgram charge.
- Sending a different blob for the same call returns `409` and leaves the first
  note untouched.
- Two simultaneous first submissions may both upload, but only one row can win.
  The loser deletes its unlinked object and returns the winning row if the hashes
  match; otherwise it returns `409`.

This makes browser/network retries safe without introducing a separate
idempotency-key lifecycle.

## Data model

Alembic revision `0012_call_feedback` adds `call_feedback` with:

- Ownership: `tenant_id`, `call_id`, `created_by`.
- Object identity: provider, bucket, key, MIME type, byte count, SHA-256.
- Derived data: transcript, provider request ID, and detected duration.
- State: status, safe error text, attempts, started/completed timestamps.
- Audit timestamps: `created_at`, `updated_at`.

Rows cascade with the call/tenant; deleting a user only nulls `created_by`.
Tenant RLS applies to reads and writes. The API additionally checks the call with
both `id` and `tenant_id` and intentionally returns `404`, rather than revealing
whether a cross-tenant call ID exists.

Apply the schema change before deploying the API:

```bash
cd backend
alembic upgrade head
```

The migration downgrade removes metadata only. It deliberately does not delete
objects; destructive storage cleanup must be a separately reviewed operation.

## Deepgram behavior

The implementation sends the original containerized blob to
`POST https://api.deepgram.com/v1/listen` with:

- `model=nova-3`
- `smart_format=true`
- `mip_opt_out=true`
- the configured language (default `en`)
- the blob's base MIME type as `Content-Type`
- `Authorization: Token ...` using the tenant credential first, environment
  fallback second

The total provider timeout defaults to 10 seconds. API keys are never stored in
the feedback row or included in errors/logs. A valid Deepgram result with an
empty transcript is `done`—silence is a valid result, not a transport failure.

References:

- [Deepgram prerecorded audio guide](https://developers.deepgram.com/docs/pre-recorded-audio)
- [Deepgram prerecorded API reference](https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded)
- [Deepgram Flux vs Nova-3](https://developers.deepgram.com/docs/flux/flux-nova-3-comparison) — prerecorded input is a Nova-3 use case

## Observability and rollout

Log events do not include audio or transcript text. Useful events include:

- `feedback_transcription_provider_succeeded`
- `feedback_transcription_failed`
- `feedback_transcription_claim_failed`
- `feedback_transcription_result_persist_failed`
- `feedback_audio_store_failed`
- `feedback_orphan_cleanup_failed`

Recommended initial dashboard signals:

- Submit latency p50/p95/p99.
- Deepgram attempt latency and timeout rate.
- Ratio of `failed` to all attempted transcripts.
- Count and oldest age of `pending` rows.
- Storage upload/cleanup failures.
- Retry success rate and attempts per note.

Useful operational query:

```sql
SELECT transcript_status,
       COUNT(*) AS notes,
       MAX(NOW() - updated_at) AS oldest
FROM call_feedback
WHERE created_at >= NOW() - INTERVAL '24 hours'
GROUP BY transcript_status;
```

Start considering Approach B only when measurements show that the synchronous
boundary is the problem—for example, sustained submit p95 above 3 seconds,
provider timeouts above 1%, notes routinely approaching several minutes, or API
worker capacity becoming coupled to transcription concurrency. The table and
state machine already support moving the attempt behind a queue later; the UI
and stored data do not need a redesign.

## Pre-mortem

Assume this launch failed. The most likely causes and countermeasures are:

| Failure | Early warning | Current prevention/response |
|---|---|---|
| Deepgram is slow or rate-limited | Higher submit p95; `429`/timeout failures | Hard 10-second bound; note returns as `failed`; explicit retry |
| API dies after commit, before final update | Old `pending` row | Retry atomically reclaims stale pending rows after 30 seconds |
| Browser retries after losing the response | Duplicate POSTs | Unique call constraint plus SHA-256 idempotency; no second STT charge |
| S3 succeeds but PostgreSQL insert fails | Orphan object log | Immediate compensating delete; UUID key makes reconciliation safe |
| Production silently falls back to ephemeral disk | Notes vanish after rollout | Production returns `503` unless S3-compatible storage is configured |
| Cross-tenant call ID is submitted | IDOR/security alerts | Call ownership check, RLS, tenant-scoped queries, non-revealing `404` |
| Huge or malformed upload consumes resources | `413`/`415` counts, memory pressure | MIME allowlist, 10 MiB bounded read, five-minute declared duration cap |
| Two retries transcribe the same note | Attempts/charges exceed notes | Atomic status claim; concurrent fresh retry receives `409` |
| Deepgram succeeds but DB result update fails | Stale pending plus persist-failure log | Audio and row remain; stale-pending retry regenerates the transcript |
| New feature inherits call-recording lifecycle accidentally | Missing notes at recording expiry | Dedicated table and `feedback/` object prefix; lifecycle policy can be scoped |

Two follow-up controls are operational rather than request-path code:

1. Configure an object-store lifecycle/retention rule for the `feedback/`
   prefix that matches the product's privacy policy.
2. Add a scheduled reconciliation report for old pending rows and unreferenced
   `feedback/` keys if volume grows enough to justify it.
