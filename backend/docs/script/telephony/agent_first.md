# `modes/agent_first.py` — Agent-speaks-first handler

## When used

`voice_session._first_speaker == "agent"` (the default, and the value picked
by most campaigns).

## Public functions

- `prepare_pre_originate_greeting(pre_warm_session, effective_first_speaker)`
  Called from `make_call` during the ringing phase. Pre-synthesizes the
  greeting audio so it's already in memory by the time the callee answers.
  Skipped automatically when `first_speaker == "user"` and the user-first
  fallback is disabled.

- `_send_outbound_greeting(voice_session)` (re-exported as
  `telephony_bridge._send_outbound_greeting` for backward compat)
  Called from `lifecycle._on_new_call` after the callee answers.

## Flow

1. **Ringing phase:** `prepare_pre_originate_greeting` synthesizes audio into
   `voice_session._presynth_greeting_audio` and `_presynth_greeting_text`.
2. **On answer:** `_send_outbound_greeting` runs.
   - **Fast path:** if pre-synth chunks are populated, pump them into the
     media gateway directly. First audio reaches the callee within ~5ms.
   - **Slow path:** if pre-synth is unavailable, fall back to realtime TTS
     via `voice_session.pipeline.synthesize_and_send_audio`.
3. **Barge-in:** if the callee speaks during playback, only the spoken
   portion of the greeting is persisted to `conversation_history` so the
   LLM doesn't echo the unspoken tail on the next turn.

## Knobs

- `TELEPHONY_FIRST_SPEAKER=agent` — global default (campaigns override).
- Greeting text comes from
  `telephony_session_config.build_telephony_greeting` using the agent name and
  tenant business name.
