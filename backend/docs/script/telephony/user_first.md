# `modes/user_first.py` — User-speaks-first handler

## When used

`voice_session._first_speaker == "user"`. Picked by campaigns where the
callee should drive the opening turn (e.g., the agent is responding to an
inbound greeting like "Hello?").

## Public functions

All re-exported from `telephony_bridge` for backward compatibility:

- `_user_first_open_seconds() -> float` — read from
  `TELEPHONY_USER_FIRST_OPEN_S` env var, default 5.0s, clamped to a 2.0s
  minimum.
- `_user_first_fallback_enabled() -> bool` — read from
  `TELEPHONY_USER_FIRST_FALLBACK_ENABLED`, default `false`.
- `_handle_user_first_silence(voice_session, pbx_call_id)` — async safety
  net task scheduled by `lifecycle._on_new_call` when in user-first mode.

## Flow

1. On answer, `lifecycle._on_new_call` does NOT play a greeting. Flux (STT)
   is already connected from the ringing pre-warm and listens immediately.
2. `_handle_user_first_silence` arms a graduated state machine:
   - **Phase 1 (open_s, default 5.0s):** wait silently. If the callee says
     anything, exit.
   - **Phase 2:** play the pre-synthesized fallback greeting (only if
     `_user_first_fallback_enabled()` is true). Slow path: drive the LLM
     with a `[CALLEE_SILENT_AT_PICKUP …]` cue.
   - **Phases 3..N (reprompt_s, default 8.0s × `MAX_REPROMPTS`):** prompt
     "Are you still there?" via LLM cues.
   - **Final (farewell_s, default 6.0s):** polite goodbye + adapter.hangup.
3. Any real callee speech at any phase cancels the handler.

## Knobs

- `TELEPHONY_USER_FIRST_OPEN_S` — initial silence window (default 5.0s).
- `TELEPHONY_USER_FIRST_REPROMPT_S` — reprompt interval (default 8.0s).
- `TELEPHONY_USER_FIRST_FAREWELL_S` — farewell window (default 6.0s).
- `TELEPHONY_USER_FIRST_MAX_REPROMPTS` — number of reprompts (default 2).
- `TELEPHONY_USER_FIRST_FALLBACK_ENABLED` — gates the auto-opener prompt.

## Adapter access

`_handle_user_first_silence` needs to call `adapter.hangup()` after the
farewell. The adapter lives on `telephony_bridge` as a module singleton, so
this module reaches it lazily via
`from app.api.v1.endpoints import telephony_bridge as _tb;
getattr(_tb, "_adapter", None)`.
