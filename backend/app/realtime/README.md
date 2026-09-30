# Realtime voice module

This folder owns speech-to-speech model behavior. The traditional STT/LLM/TTS
pipeline does not compose or supply its prompts.

| File | Responsibility |
| --- | --- |
| `catalog.py`, `config.py`, `session_config.py` | Model/voice catalog, validation, persisted and per-call settings |
| `personas.py`, `prompts.py`, `prompt_config.py` | Independent personas, instructions, final prompt identity |
| `campaign_config.py` | Campaign overrides and account defaults |
| `runtime.py`, `credentials.py` | Credential resolution, provider session and bridge assembly |
| `openai.py`, `xai.py` | Provider protocol adapters |
| `playout_buffer.py`, `bridge.py` | Bounded response validation, playback, interruption and tools |
| `tools.py` | Realtime function schemas for shared business actions |
| `preview.py` | Voice samples generated with the actual Realtime model |

Frontend controls are under `Talk-Leee/src/components/realtime/`. AI Options
sets account defaults in existing `tenant_ai_configs.realtime_settings` JSONB.
Campaign overrides use existing `campaigns.script_config` JSONB, including an
independent `realtime_prompt`. No database migration is needed. Switching
engines preserves the other engine's saved settings. Old campaigns without
an engine selection inherit the account setting; saving explicitly pins it.

Shared entry points remain in the orchestrator and HTTP handlers. Tenant
authorization, telephony transport, knowledge retrieval, connector actions,
transcripts and lead storage remain shared services. The existing xAI adapter
is retained for opt-in configuration; the dashboard catalog offers GPT Realtime.

A selected Realtime engine never silently falls back to traditional providers.
Audio is held until a complete matching transcript can be checked against the
shared action guardrails. This adds a generation delay before playback. The
buffer is capped at 30 seconds; incomplete or oversized responses fail explicitly.
Guardrails are a defense against known false-action claims, not a guarantee of
perfect model accuracy. Tool continuations wait for preceding playback while
the event pump remains available for caller interruptions.

Contact readbacks advance only after a transport playback acknowledgement.
Browser playback supports that acknowledgement. Telephony transports without
it retain pending contact details; live telephony confirmation is not certified
by these unit checks. Production deployment and a complete inbound/outbound
call acceptance test are separate from the isolated provider smoke check.
