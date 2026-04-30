# `lifecycle.py` — Call-lifecycle orchestration

## Responsibilities

- **Ringing phase:** `_on_ringing` pre-warms a `VoiceSession` while the
  callee's phone is still ringing, so STT/TTS handshakes are done by the
  time the callee answers. Pre-synthesized greeting (agent-first) lands
  here too, in `voice_session._presynth_greeting_audio`.
- **Answer:** `_on_new_call` drains the ringing-warmup cache, registers
  the session, calls `resolve_first_speaker`, and dispatches to either
  `agent_first._send_outbound_greeting` or
  `user_first._handle_user_first_silence`.
- **Audio:** `_on_audio_received` routes inbound audio chunks from the
  C++ gateway to the right session, with an early-audio buffer for chunks
  that arrive before `_on_new_call` has registered the mapping.
- **End:** `_on_call_ended` triggers `recording._save_call_recording`,
  ends the voice session, and removes the entry from
  `_telephony_sessions`.
- **Watchdog:** `_session_watchdog` GCs orphaned ringing-warmup entries
  (callee never answered, no terminal event) older than
  `_RINGING_MAX_AGE_S = 180s` and enforces inactivity / max-duration
  timeouts on active sessions.
- **WS bridge:** `_on_ws_session_start` wires the `mod_audio_fork`
  WebSocket session_id to the right call_id so audio routing works.

## Public symbols (all re-exported via `telephony_bridge`)

`_pop_ringing_warmup`, `_get_orchestrator`, `_session_watchdog`,
`_pipeline_done_cb`, `_on_ringing`, `_reject_overcap_call`, `_on_new_call`,
`_on_audio_received`, `_on_call_ended`, `_on_ws_session_start`.

## State ownership (NOT here)

The module state singletons live on `telephony_bridge.py`:

- `_adapter: CallControlAdapter | None`
- `_telephony_sessions: dict[call_id, VoiceSession]`
- `_watchdog_task: asyncio.Task | None`
- `_ringing_warmups: dict[call_id, (VoiceSession, connect_task)]`
- `_ringing_warmup_created_at: dict[call_id, float]`
- `_early_audio_buffers: dict[gateway_session_id, list[bytes]]`
- `_gateway_session_to_call_id: dict[gateway_session_id, call_id]`
- `_MAX_TELEPHONY_SESSIONS`, `_EARLY_AUDIO_MAX_CHUNKS`, `_RINGING_MAX_AGE_S`
  (constants)

`app/main.py` writes `_tb._adapter = …` at startup, so the bridge must
remain the canonical owner. Functions in this module reach state through
the `_bridge()` helper:

```python
def _bridge():
    from app.api.v1.endpoints import telephony_bridge
    return telephony_bridge

# Read:  sess = _bridge()._telephony_sessions.get(call_id)
# Write: _bridge()._watchdog_task = task
```

## Watchdog timeouts (defined here)

- `_SESSION_INACTIVITY_TIMEOUT_S` — `TELEPHONY_INACTIVITY_TIMEOUT_S`, default 300s.
- `_SESSION_MAX_DURATION_S` — `TELEPHONY_MAX_CALL_DURATION_S`, default 3600s.
