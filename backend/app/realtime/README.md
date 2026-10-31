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
| `playout_buffer.py`, `bridge.py` | Audio/transcript ownership, playback, interruption and tools |
| `tools.py` | Realtime function schemas for shared business actions |
| `preview.py` | Voice samples generated with the actual Realtime model |

Frontend controls are under `Talk-Leee/src/components/realtime/`. AI Options
sets account defaults in existing `tenant_ai_configs.realtime_settings` JSONB.
Campaign overrides use existing `campaigns.script_config` JSONB, including an
independent `realtime_prompt`. No database migration is needed. Switching
engines preserves the other engine's saved settings. Old campaigns without
an engine selection inherit the account setting; saving explicitly pins it.

## Prompt policy (`realtime@10`)

The essential instruction is: represent the configured company, pursue the
campaign goal with short natural turns, verify company claims through campaign
knowledge, confirm exact contact details, and report actions only after backend
success. Campaign guidance can change the task and style, not these boundaries.

The base prompt is a conversation guide, without a prescribed sequence or
regex-derived sales labels. The model chooses exact sections from its campaign
catalog through `knowledge_lookup`; the backend returns complete authored text
and ancestors from the call's scoped snapshot. Headings guide navigation and
do not establish facts. `record_contact` lets the model interpret caller email
and phone corrections; the backend validates source, currentness and saving.
Read-only lookups need no permission. Consequential actions still use the
existing authorization and execution contracts.

The native guide remains separate from traditional prompt wording. Knowledge,
contact and action tool contracts share the existing backend implementations.
No new service, model upgrade or separate extraction model is introduced.

Reference: [OpenAI Realtime prompting guidance](https://developers.openai.com/api/docs/guides/voice-prompting).
Offline checks verify prompt composition and integration, not guaranteed model
behavior. Before release, evaluate actual campaign questions, unknown prices,
unclear/corrected contacts, interruptions and unavailable actions on real calls.

Shared entry points remain in the orchestrator and HTTP handlers. Tenant
authorization, telephony transport, knowledge retrieval, connector actions,
transcripts and lead storage remain shared services. The existing xAI adapter
is retained for opt-in configuration; the dashboard catalog offers GPT Realtime.

A selected Realtime engine never silently falls back to traditional providers.
Audio is held until a complete matching transcript establishes response ownership.
This still adds a generation delay before playback. The
buffer is capped at 30 seconds; incomplete or oversized responses fail explicitly.
Normal prose is no longer judged or rewritten by price, relationship, contact
or action-completion regexes. The model must follow its guide and actual tool
results; real-model evaluation is needed to assess factual and action claims.
Tool continuations wait for preceding playback while
the event pump remains available for caller interruptions.

Contacts begin pending and become confirmed through the model's interpretation
of a later caller turn. Source revisions revoke stale contributions. A saved
result requires a persistence acknowledgement; confirmation is not proof of
transcription correctness or an external action. No prescribed readback format
or separate confirmation classifier runs. Transport acknowledgements remain
relevant to delivered history and consequential actions. Production deployment
and full inbound/outbound acceptance remain separate from offline checks.
