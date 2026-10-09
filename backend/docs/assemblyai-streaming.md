# AssemblyAI Universal-3.6 Pro integration

This is the explicitly requested AssemblyAI exception to the production-readiness
feature freeze. It adds an STT choice to the existing cascaded pipeline. Flux,
Nova, and the separate native Realtime pipeline retain their existing roles.

## Configuration

1. Use the supported [deployment procedure](../../docs/DEPLOYMENT.md) to activate
   the reviewed code and apply `0065_assemblyai_settings` before restarting the
   application services; its parent is `0064_dnc_phone_number_default`. Running
   Alembic from the old checkout cannot apply a migration it does not contain.
2. Configure the existing tenant credential store with provider `assemblyai`, or
   supply `ASSEMBLYAI_API_KEY` through the backend's private environment.
3. In **AI Options → Speech recognition**, select **AssemblyAI Universal-3.6 Pro**.
   Choose Balanced, Fast, or Accuracy and save. Changes apply to the next call.
   The catalog displays a missing-key reason until a credential is available.

The saved identity is `stt_engine=assemblyai`, `stt_provider=assemblyai`,
`stt_model=universal-3-6-pro`, `stt_language=en`. The API and database reject a
non-English AssemblyAI selection. Every connection sends `language_codes=["en"]`;
this is the provider's monolingual steering setting, not a claim of perfect ASR.
The browser never receives the key.

AssemblyAI tenant credentials are read afresh when listing availability, saving
the selection or creating a session. Adding, rotating or revoking a tenant key
therefore does not require restarting workers. The existing environment fallback
still applies when there is no active tenant key; this does not replace credentials
already held by an in-progress call.

AssemblyAI settings are stored separately in `tenant_ai_configs.assemblyai_settings`.
Switching to Flux retains those preferences. Inbound admission pins them in the
call snapshot; outbound and test calls resolve the saved tenant configuration.
Twilio and Vonage preserve their respective input audio rates.

## Controls

| Group | Controls |
| --- | --- |
| Model behavior | `mode`: Balanced (`balanced`, default), Fast (`min_latency`), Accuracy (`max_accuracy`) |
| Region | `region`: global, US, EU streaming endpoint |
| Turn timing | `min_turn_silence`, `max_turn_silence`, `interruption_delay`, `vad_threshold` |
| Partial recognition | `include_partial_turns`: provider default, enabled, disabled |
| Vocabulary | Optional `prompt` and `keyterms_prompt`; both empty by default |
| Conversation context | `auto_agent_context`, `previous_context_n_turns` |
| Language reporting | `language_detection`, reporting only; the selected language stays English |
| Audio cleanup | `voice_focus`, `voice_focus_threshold` |
| Domain | Optional medical mode (`domain=medical-v1`) |
| Speakers | `speaker_labels`, `max_speakers`, `speaker_labels_revision_interval_ms` |
| Privacy | `redact_pii`, `redact_pii_policies`, `redact_pii_sub`, `filter_profanity` |
| Session diagnostics | `session_heartbeat`, `inactivity_timeout` |

Nullable timing overrides leave the selected mode's defaults intact. Flux tuning
is never passed to AssemblyAI. Disabled dependent controls retain their saved
values but are omitted from the provider request. Paid add-ons are off by default.
PII redaction can remove names, emails and numbers needed for lead capture; its
control explains this, and partials use the provider's redaction-aware default.

Start with Balanced and no contextual prompt or keyterms. Add a short description
of the audio domain only when observed vocabulary errors justify it; this field
is not the sales agent's system prompt. No campaign prompt, spelling harness, or
hidden campaign keyterm list is injected into AssemblyAI.

When enabled, agent context comes from successfully submitted TTS utterances,
including greetings. Failed or interrupted utterances do not send their full
unspoken text. Submission is recognition context, not evidence of caller consent
or proof that audio was heard. Provider-side context memory is session-local.

## Runtime behavior

The adapter checks the `Begin` acknowledgement for the exact model and mode before
prewarm succeeds. It buffers telephone audio into 50 ms mono PCM packets, paces
them, and substitutes silence while muted. Revised partials replace the current
turn; finals are emitted once. An empty or suppressed final releases the speaking
state without promoting an earlier partial into an answer. Speaker revisions and
heartbeats cannot replay a caller turn.

WebSocket authentication uses the Authorization header. Wire logs, exception text,
and the probe output exclude keys, transcripts and prompt-bearing URLs. Cleanup
closes sockets and releases concurrency permits. `ASSEMBLYAI_MAX_CONCURRENT`
defaults to five per process; size it against the account allowance and number of
workers. Existing opt-in STT failover can use Nova with its separate Deepgram key;
an AssemblyAI-only account does not require that key.

## Verification and release status

The implementation branch is rebased onto main `67933e1d`. Local protocol, runtime,
React interaction and real PostgreSQL checks cover the provider option, modes,
controls, English setting, persistence, tenant isolation and failure handling.
The release evidence is recorded in `docs/sessions/2026-10-10-assemblyai-integration.md`.
These checks do not establish live provider access or improved contact accuracy.

The real-service probe uses the same production adapter and all three modes:

```powershell
# Run from backend with ASSEMBLYAI_API_KEY already supplied privately.
python -m scripts.probe_assemblyai --wav tmp/assemblyai/synthetic-email.wav --expect alex.taylor@example.com
```

Use an explicitly designated synthetic mono PCM16 WAV, 8–96 kHz and at most
30 seconds. The probe sends that audio once per mode, validates the model/mode
acknowledgement, counts final turns and checks optional expected text. It emits
only hashes, counts, booleans and timings. A missing key or empty recognition
fails; it is never reported as a successful live test.

Before activation, supply the key and run the probe. Release the reviewed commit
through the supported Git/systemd deployment, which runs the matching migration
before restarting the backend. The existing candidate-bound drain manifest and
operator access requirements still apply. Frontend deployment uses Vercel from
main. Save the selection through the authenticated UI and run an English test
call. Evaluate email/name capture on representative telephone audio;
switching providers alone is not an accuracy guarantee. Downgrade refuses to erase
saved AssemblyAI selections/preferences: export and clear them deliberately first.

## Documentation used

Reviewed the official streaming API and relevant feature guides before implementation:

- [WebSocket API](https://www.assemblyai.com/docs/streaming/api-spec/streaming-websocket)
- [Model selection](https://www.assemblyai.com/docs/streaming/select-the-speech-model)
- [Accuracy and latency](https://www.assemblyai.com/docs/streaming/getting-started/optimizing-accuracy-and-latency)
- [Turn detection](https://www.assemblyai.com/docs/streaming/turn-detection)
- [Message sequence](https://www.assemblyai.com/docs/streaming/message-sequence)
- [Prompting and keyterms](https://www.assemblyai.com/docs/streaming/prompting-and-keyterms)
- [Conversation context](https://www.assemblyai.com/docs/streaming/universal-3-5-pro/context-carryover)
- [Errors and closures](https://www.assemblyai.com/docs/streaming/common-session-errors-and-closures)

The feature guides' explicit 3.6 behavior takes precedence over older 3.5-only
descriptions still present in parts of the shared API reference. Legacy
`format_turns` and `end_of_turn_confidence_threshold` are not sent. The managed
LLM Gateway, prerecorded transcription and provider-hosted agents are outside
this STT integration.
