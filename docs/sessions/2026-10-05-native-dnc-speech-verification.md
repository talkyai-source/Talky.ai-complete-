# Native DNC speech receipt boundary

This OP07 follow-up contains a reproduced acknowledgement defect in the native voice path. The preceding durability repair is required: its successful return means the DNC suppression row was acknowledged, independently of whether every queue/lead cleanup step succeeded. The runtime also requires the reviewed `0061_dnc_runtime_contract` schema. This document does not mark the conversation-quality or production-release gates complete.

The preserved [original probe](artifacts/op07-dnc/speech-boundary-observed.json) drove the actual event pump, shared response guards and playback using synthetic persistence/provider/media ports. A failed DNC result still allowed a generated removal claim to reach audio submission and the call-ending callback. The result was a wiring failure, not a measurement of how often a model produces that claim.

## Admission and recovery

The bridge now waits for the existing bounded, coalesced DNC task inside the detached playback task, before submitting a known-DNC response's text control or audio. The event pump remains able to process caller activity. A true acknowledgement allows the otherwise validated normal response. False, unknown, cancelled or timed-out results do not allow unrestricted generated speech.

One existing per-turn repair budget can request a short exact replacement through the same native provider. The local gate checks its complete text; a prompt alone never grants permission to play it. An ending caller gets the existing unconfirmed farewell. A continuing caller gets: “I cannot confirm that your do-not-call request was saved.” The latter does not authorize hangup. A wrong replacement stays withheld, records the existing failure diagnostic and does not start a repair loop.

OpenAI's documented per-response metadata binds this repair to a random identifier. The adapter checks its epoch and provider response ID, and the bridge checks the current caller revision. Delayed repair creation cannot take ownership of a newer response buffer; a stale repair cannot cancel or become the newer reply after persistence recovers. This uses the existing `response.create` path and does not change a model, voice or default. [Official OpenAI conversation documentation](https://developers.openai.com/api/docs/guides/realtime-conversations).

xAI's fetched documentation establishes per-response instructions but did not establish metadata echo. Its capability is explicitly disabled, and no unsupported metadata field is sent. An interrupted repair whose ownership cannot be resolved remains speech-unavailable with a diagnostic. This is a disclosed containment limit, not verified xAI recovery or full conversation acceptance. [Official xAI speech-to-speech documentation](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech).

On a new accepted provider-final caller turn, an existing opt-out whose previous persistence task finished unsuccessfully can make one coalesced idempotent retry using the original trusted call identity. Deltas, duplicate finals and generated replies do not create a retry loop. A later acknowledged result restores normal response admission when no unresolved unsupported-provider repair prevents ownership proof.

## Delayed transcription and partial output

Independent review identified a second timing case: the provider reply can start before final caller ASR identifies DNC, with the gateway awaiting begin or send. Two actual-method barrier controls reproduced the leak. Admission is now checked across the transport await boundaries. If no bytes have been submitted, the safe-repair path remains available. If submission has begun, the existing cancellation/clear/truncate path stops the remaining output and preserves interrupted, unknown partial delivery. The original reply is not deleted and replayed as if it had been wholly unheard.

Root review found the same early-return gap at the legacy `tts_audio_complete` control await. Its held-control reproduction also failed before the correction; that boundary now runs admission before returning on changed ownership. The independent reviewer reread this final delta and found no additional material issue. Review did not include a separately executed test suite.

Previously submitted bytes or controls cannot be recalled. These tests prove local submission and receipt ownership, not caller hearing or provider acoustic fidelity. Ordinary help is intentionally degraded while DNC is unacknowledged; this is not a fully functional conversation-quality acceptance result.

## Reproducible evidence

- [Initial speech controls](artifacts/op07-dnc-speech/speech-initial.txt): 11 failed, 4 passed before the repair.
- [Late-ASR barriers](artifacts/op07-dnc-speech/late-asr-initial.txt): 2 failed before the boundary rechecks.
- [First related run](artifacts/op07-dnc-speech/related-initial.txt): 303 passed, 1 failed. The old close-ownership fixture omitted acknowledged persistence and a repair provider; its intended successful-write prerequisite is now explicit.
- [Interim related regression](artifacts/op07-dnc-speech/related-interim.txt): 309 passed, no skips, 254 existing warnings.
- [Owned controls before the last legacy-boundary case](artifacts/op07-dnc-speech/speech-final.txt): 30 passed, no skips, including an actual parser-to-bridge control.
- [Legacy completion-control reproduction](artifacts/op07-dnc-speech/legacy-completion-initial.txt): 1 failed before the final admission-first correction.
- [Final exact-source regression](artifacts/op07-dnc-speech/related-final.txt): 311 passed, no skips, 254 existing warnings, including all 31 owned speech controls.
- [Scoped CI Ruff](artifacts/op07-dnc-speech/ruff-final.txt) and `git diff --check` passed.

The [machine-readable manifest](artifacts/op07-dnc-speech/verification.json) contains exact commands, module inventory, source hashes, dependency references and review attribution. All persistence, WebSocket and media ports in this follow-up are synthetic. No provider request, PostgreSQL operation, email, handset call or customer-data operation was performed. Earlier real migrated-PostgreSQL DNC evidence is separately recorded in the durability and schema reports.

Persistent write failure and process-death recovery remain unresolved operational boundaries. Recording expiry/export/delete acceptance, human-reviewed semantic cases, supported-profile qualification and real-call proof remain open. No unrelated deferred package is completed by this repair.
