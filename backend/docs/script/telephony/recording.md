# `recording.py` — Stereo-WAV recording pipeline

## Public function

`_save_call_recording(voice_session, call_id)` — async. Re-exported as
`telephony_bridge._save_call_recording` for backward compatibility.

Builds a stereo WAV (caller left channel / agent right channel) from the
session's per-direction PCM buffers and persists it. Called from
`lifecycle._on_call_ended` BEFORE the voice session is destroyed, since
the gateway buffers vanish with the session.

## Persistence layers (in order)

1. **Disk:** WAV is always written to
   `backend/recordings/{call_id}.wav`. This is the source of truth.
2. **`calls` row resolution:** look up the canonical row by
   `external_call_uuid`. If missing, attempt a stub-row insert; if the
   call has no campaign and no session tenant, skip the DB step and keep
   the disk-only save.
3. **`recording_s3` row:** insert metadata (path, duration, size,
   tenant_id, call_id) for the dashboard player.
4. **S3 upload:** if configured, push the WAV and update the row with the
   S3 key.

## Known issues (out of scope for this refactor)

- Stub-row insert fails when neither campaign nor session tenant is
  available — falls back to disk-only with a warning. (Seen in production
  logs as `stub_calls_row_insert_failed` when `first_speaker=user` with
  no campaign context.)
- `recording_s3` insert can fail with `badly formed hexadecimal UUID
  string` for certain channel-name shapes. Tracked separately.

## Dependencies

Module-scope:
- `logging`, `uuid.UUID`

Function-scope (in-body imports, kept lazy because they pull large
infrastructure modules that aren't needed elsewhere):
- `app.domain.services.recording_service.{RecordingService, RecordingBuffer, mix_stereo_recording}`
- `app.core.container.get_container`

No references to telephony module state — fully self-contained.
