# Telephony package

Domain logic for the telephony bridge endpoint. The FastAPI routes live at
`backend/app/api/v1/endpoints/telephony_bridge.py` and delegate to this
package.

## Modules

- [`config.md`](config.md) — env defaults, session-config builder, greeting builder
- [`agent_first.md`](agent_first.md) — agent-speaks-first greeting flow + pre-originate prep
- [`user_first.md`](user_first.md) — user-speaks-first silence fallback
- [`lifecycle.md`](lifecycle.md) — call-lifecycle orchestration (ringing → end)
- [`recording.md`](recording.md) — stereo-WAV recording pipeline

## Mode selection

The campaign owner picks `first_speaker = "agent" | "user"` at campaign creation
(`app/api/v1/endpoints/campaigns.py`). The value travels through the dialer
worker as a `make_call?first_speaker=…` query param and is stashed on
`voice_session._first_speaker`. The dispatcher in `modes/__init__.py`
(`resolve_first_speaker`) reads it per call. Env var `TELEPHONY_FIRST_SPEAKER`
is the fallback default.

## State ownership

Module state singletons (`_adapter`, `_telephony_sessions`, `_watchdog_task`,
the ringing warmup cache, the early-audio buffer, the gateway-session map)
live on `telephony_bridge.py`, not in this package. `app/main.py` writes
`_tb._adapter = …` at startup, so the bridge has to remain the canonical
owner. Functions in `lifecycle.py` access that state lazily through a
`_bridge()` helper.
