# `config.py` — Telephony config helpers

Leaf module — no dependencies on other telephony submodules.

## Functions

- `_outbound_first_speaker() -> str`
  Reads `TELEPHONY_FIRST_SPEAKER` env var, defaults to `"agent"`. Used as the
  fallback when a call has no per-call override.

- `_build_telephony_session_config(gateway_type, campaign, agent_name)`
  Thin shim that delegates to
  `app.domain.services.telephony_session_config.build_telephony_session_config`.

- `_build_outbound_greeting(session) -> str`
  Wraps `telephony_session_config.build_telephony_greeting` so the greeting
  text and the system prompt stay in sync (same `agent_name`, same `company`
  value).

## Used by

- `modes.agent_first.send_outbound_greeting` (greeting text)
- `modes.agent_first.prepare_pre_originate_greeting` (greeting text + TTS pre-synth)
- `lifecycle._on_ringing` and `lifecycle._on_new_call` (session config)
- `modes/__init__.resolve_first_speaker` (env fallback)
