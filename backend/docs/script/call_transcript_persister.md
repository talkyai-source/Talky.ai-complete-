# `call_transcript_persister.py`

**Module:** `app.services.scripts.call_transcript_persister`
**Tests:** `backend/tests/unit/test_call_transcript_persister.py`
**Line budget:** 600 — currently ≈ 280 lines.

## Purpose

Bind a telephony `VoiceSession` to the dialer's `calls` row at
answer-time, and persist the final transcript to that row at hangup-time.
Exists because `voice_session.call_id` is a newly-minted UUID — it does
not match `calls.id` inserted by the dialer worker, so
`TranscriptService.flush_to_database`'s `UPDATE calls WHERE id =
voice_session.call_id` matched zero rows and transcripts were lost.

## Public API

### `CallBinding` (dataclass, frozen)

Returned from `bind_telephony_call` on success.

- `internal_call_id: str` — `calls.id` UUID.
- `tenant_id: Optional[str]`
- `campaign_id: Optional[str]`

### `async bind_telephony_call(*, voice_session, pbx_channel_id, db_client) -> Optional[CallBinding]`

Resolves `calls.id` for a PBX channel and stashes it on the voice
session. **Non-destructive**: only sets private attributes
(`_dialer_call_id`, `_dialer_tenant_id`, `_dialer_campaign_id`). Never
rewrites `voice_session.call_id` — STT/TTS/media-gateway maps depend on
the original value.

Returns `None` and logs when:
- The `calls` lookup raises (DB outage)
- No row matches the PBX channel id (non-campaign / test call)
- The matched row has no `id`

Never raises.

### `async save_call_transcript_on_hangup(*, voice_session, transcript_service, db_pool) -> None`

Final persist. Reads the in-memory buffer keyed on the session's
original call id, writes to `calls` + `transcripts` keyed on the
dialer's id, always clears the buffer (even on write failure). Never
raises.

Skip paths (buffer cleared, no DB work):
- Missing `session_call_id`
- Empty buffer
- Missing `_dialer_call_id` (non-campaign call)
- `db_pool is None`
- Invalid UUID in `_dialer_call_id`

## Wiring

See `backend/app/api/v1/endpoints/telephony_bridge.py`:

- `bind_telephony_call` is invoked in `_on_new_call`, immediately after
  `_telephony_sessions[call_id] = voice_session` (so session registration
  happens first — audio can flow even if the dialer lookup is slow).
- `save_call_transcript_on_hangup` is invoked in `_on_call_ended`,
  *before* `_save_call_recording` (so transcript persist failures can't
  clobber recording persistence), under the existing
  `if voice_session:` branch.

## Why this exists (vs. modifying `TranscriptService`)

An earlier plan draft proposed rewriting `voice_session.call_id` to
`calls.id` so `TranscriptService.flush_to_database` would just work. That
would break:

- `deepgram_flux.py:_pre_connections[call_id]`
- TTS `connect_for_call(call_id)` pools
- media gateway session registry

all of which are keyed on the original call id and registered during the
ringing-phase warmup.

The non-destructive binding pattern leaves those maps intact and reads
the buffer with the original key at hangup time.
