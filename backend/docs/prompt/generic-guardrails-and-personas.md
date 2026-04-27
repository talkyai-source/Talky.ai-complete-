# Generic Guardrails + Three Personas (Provider-Agnostic Prompts)

_Date: 2026-04-24_
_Author: voice-agent backend_

## Why this change

Every outbound telephony call used a single hardcoded system prompt —
`TELEPHONY_ESTIMATION_SYSTEM_PROMPT` at
`backend/app/domain/services/telephony_session_config.py:45` — baked for
one specific client's construction-estimation script. Three problems:

1. **No reusability across clients.** Every new campaign would have
   required another copy of the prompt.
2. **Rules lived inside each prompt copy.** "Never say Certainly / never
   re-ask CAPTURED / how to say phone numbers" repeated across every
   future prompt, guaranteeing drift.
3. **Per-provider duplication risk.** Adding GPT, Claude, or another
   LLM would have invited copy-pasting those same rules into each new
   provider's prompt code path.

We also needed:

- Three starter roles (Lead Generation, Customer Support, AI
  Receptionist) — patterns the product team already uses.
- A 1-3 name rotator so a single campaign can sound like different
  agents across calls without anyone running separate campaigns.

## What shipped

A layered prompt system where the campaign creator fills in only
business-specific details. The rest is reused across every persona, every
campaign, and every LLM provider.

### Final prompt shape

```
[ 1. GENERIC_GUARDRAILS ]   stable — same for every call
[ 2. PERSONA_BLOCK ]         one of: lead_gen | customer_support | receptionist
[ 3. CAMPAIGN_BLOCK ]        filled from campaign.script_config.campaign_slots
[ 4. Additional instructions ]  optional freeform from the campaign form
[ 5. CAPTURED header ]       prepended per-turn by prompt_builder (existing)
```

Layer 5 is the existing per-turn header injected by
`app.services.scripts.prompt_builder.compose_system_prompt()`. It is
untouched — this change adds layers 1-4 underneath.

### Files created

- `backend/app/services/scripts/prompts/guardrails.py` — the one-and-only
  copy of the generic rules. Brand-free; `{agent_name}` /
  `{company_name}` are substituted at compose time.
- `backend/app/services/scripts/prompts/personas/lead_gen.py`
- `backend/app/services/scripts/prompts/personas/customer_support.py`
- `backend/app/services/scripts/prompts/personas/receptionist.py`
- `backend/app/services/scripts/prompts/personas/__init__.py` — the
  `PERSONAS` registry + `REQUIRED_SLOTS_BY_PERSONA`.
- `backend/app/services/scripts/prompts/composer.py` —
  `compose_prompt(persona_type, agent_name, company_name, campaign_slots,
  additional_instructions)` assembles the final string. Uses plain
  `str.format()` — any unfilled `{slot}` raises `KeyError`, re-raised as
  `PromptCompositionError` so bad data never silently reaches the LLM.
- `backend/app/services/scripts/prompts/agent_name_rotator.py` —
  `pick_agent_name(pool)` + `validate_pool(...)`.
- `backend/app/services/scripts/interruption_filter.py` —
  `is_backchannel()` for "hmm"/"yeah"/"uh huh" suppression before the
  LLM ever sees them.
- `Talk-Leee/src/lib/campaign-personas.ts` — frontend mirror of the
  persona registry (slot definitions, parse helpers).
- Three new test files in `backend/tests/unit/`
  (test_prompt_composer, test_interruption_filter,
  test_agent_name_rotator) — **50 tests, all green.**

### Files modified

Backend:

- `backend/app/domain/services/telephony_session_config.py` —
  `build_telephony_session_config()` now accepts `campaign` and
  `agent_name_override`. When the campaign's `script_config` has a
  `persona_type`, it routes through `compose_prompt`. Otherwise the
  legacy estimation prompt keeps working (back-compat for existing
  campaigns). `AgentConfig.business_type` / `tone` now mirror the
  persona so downstream code sees consistent values.
- `backend/app/services/scripts/__init__.py` — re-exports
  `compose_prompt`, `pick_agent_name`, `validate_pool`,
  `is_backchannel`, `PromptCompositionError`.
- `backend/app/domain/services/voice_pipeline_service.py` — imports
  `is_backchannel` and suppresses backchannel-only turns in
  `handle_turn_end` right after the existing repetitive-transcript
  guard. Prevents the LLM from generating a fresh turn in response to
  a listening sound.
- `backend/app/domain/models/dialer_job.py` — new optional
  `agent_name: Optional[str]` field; included in `to_redis_dict`.
- `backend/app/domain/services/campaign_service.py` — picks an agent
  name from the campaign's pool during `_create_job_for_lead`,
  pass-through via a new `agent_names_pool` kwarg. Mirrors the
  `first_speaker` plumbing done earlier.
- `backend/app/workers/dialer_worker.py` — `_make_call` appends
  `&agent_name=<url-encoded>` to the bridge URL when present.
- `backend/app/api/v1/endpoints/telephony_bridge.py` — `make_call`
  accepts `agent_name` query param, looks up the campaign row to get
  `script_config`, and stashes both on the pre-warm session. The
  `_build_telephony_session_config` shim now forwards
  `campaign` + `agent_name` to the real builder.
- `backend/app/api/v1/endpoints/campaigns.py` — `CampaignCreateRequest`
  gains `persona_type`, `agent_names`, `company_name`,
  `campaign_slots`. `agent_names` is validated at the API boundary
  (1-3 non-blank, deduped). The endpoint persists the new fields into
  `campaigns.script_config` JSONB — **no SQL migration** needed.

Frontend:

- `Talk-Leee/src/lib/dashboard-api.ts` — `CampaignCreate` gains
  `persona_type`, `company_name`, `agent_names`, `campaign_slots` and
  a new exported `PersonaType`.
- `Talk-Leee/src/app/campaigns/new/page.tsx` — adds a 3-card persona
  picker, company-name input, agent-names input (comma-separated, up
  to 3), and a dynamic block of persona-specific slot fields. The old
  freeform "AI Instructions" textarea is relabelled "Additional
  instructions" and appended as the 4th layer. Client-side validation
  surfaces any missing required slots before submit.

## How it stays provider-agnostic

The `LLMProvider.stream_chat(system_prompt=...)` contract at
`backend/app/domain/interfaces/llm_provider.py:19` already accepts a
single string. Composition happens exclusively in the domain layer
(`compose_prompt`), so Groq and Gemini — and any future OpenAI, Claude,
or Cohere adapter registered via `LLMFactory` — receive the same fully
composed string. **No provider class ever touches prompt content.**

Adding a new provider later is two lines:

```python
# backend/app/infrastructure/llm/factory.py
from app.infrastructure.llm.openai_provider import OpenAIProvider
LLMFactory.register("openai", OpenAIProvider)
```

Zero prompt changes needed. The guardrails, personas, and campaign slots
are automatically available to the new provider.

## Cache-friendly ordering

Guardrails are the stable prefix. Personas are stable per campaign.
Campaign slots and additional instructions are the variable tail. This
matches how Anthropic's prompt caching (`cache_control` breakpoints) and
OpenAI's auto-caching reward a stable prefix — the moment we swap in
Claude/GPT, caching buys us the expected ~10x cost reduction on repeated
calls without any further work.

## Backchannel filter

The old behaviour: "hmm" / "yeah" / "uh huh" during the agent's turn
would trigger Deepgram Flux EndOfTurn → a fresh LLM response to nothing
→ the agent would restart mid-sentence or change subject. The new
filter drops these turns **before** they reach the LLM
(`voice_pipeline_service.handle_turn_end` at the line immediately after
the repetitive-transcript check). The persona prompts also teach the
model to keep going at the language level — belt and braces.

## Verification

**Unit tests** (all in `backend/tests/unit/`):

```
./venv/bin/python3 -m pytest tests/unit/test_prompt_composer.py \
    tests/unit/test_interruption_filter.py \
    tests/unit/test_agent_name_rotator.py
# 50 passed
```

**Smoke** — `build_telephony_session_config()` exercised both paths:

- Legacy path (no campaign) → legacy estimation prompt, legacy name pool.
- New path (campaign with `script_config.persona_type="receptionist"`)
  → composer fires, `agent_config.business_type` mirrors the persona,
  no unfilled `{placeholders}` in the system prompt.

**Runtime check** on the live stack:

```
233 routes OK; DialerJob + CampaignCreateRequest persona wiring good
```

**TypeScript** — zero errors on the three touched frontend files
(`tsc --noEmit`).

## Prompt-source audit (what this system does NOT touch)

We did a full `grep` of `system_prompt\s*=` and `SYSTEM_PROMPT\s*=`
across `backend/app/` to catalogue every prompt injection point. The
campaign composer and Ask AI are intentionally separate — and there are
three other prompt sources that we deliberately leave alone:

| # | Feature | File | What it is | Should it use the composer? |
|---|---------|------|------------|-----------------------------|
| 1 | **Campaign outbound telephony** | `app/domain/services/telephony_session_config.py` | Layered composer (this sprint) OR legacy estimation fallback | **Yes — this is the composer's only call site.** |
| 2 | **Ask AI (web demo)** | `app/domain/services/ask_ai_session_config.py` → `ASK_AI_SYSTEM_PROMPT` | Fixed short prompt for the Talky.ai public demo receptionist. Has its own fixed greeting and product-info keyword injection. | **No.** Different audience (product demo, not customer calls); intentionally short. Header comment warns against mixing. |
| 3 | **Talky Assistant** (in-app LangGraph helper) | `app/infrastructure/assistant/agent.py` → `SYSTEM_PROMPT` | A tool-using admin assistant ("list my campaigns", "show stats"). Used by `assistant_ws.py`. Not a voice-call agent. | **No.** Different feature (LangGraph + tools, no telephony). |
| 4 | **AI Options test + benchmark** | `app/api/v1/endpoints/ai_options.py:912` ("helpful assistant"), `:1476` ("Be brief.") | One-shot LLM health-check / latency benchmark buttons on the AI Options page. | **No.** Utility pings; adding guardrails would distort the latency number and waste tokens. |
| 5 | **Intent detector** | `app/domain/services/intent_detector.py` → `_LLM_INTENT_PROMPT` | Internal intent-classification prompt used as a utility by the pipeline. | **No.** Classifier, not a conversation. |
| 6 | **`voice_worker.py` legacy fallback** | `app/workers/voice_worker.py:238` ("You are a helpful AI assistant.") | Default string used when the worker is run standalone (`python -m`) with no campaign context. Not on the live outbound path — that path goes `dialer_worker` → `telephony_bridge.make_call` → `build_telephony_session_config`. | **No change.** Out of scope for this sprint; the comment already says "should come from campaign" and the live path already does. |

### No-mix guardrails shipped

- `ask_ai_session_config.py` now has a header block explicitly telling
  future editors: **do not** import `compose_prompt` or the `PERSONAS`
  registry here, and **do not** import `ASK_AI_SYSTEM_PROMPT` into the
  telephony path.
- `composer.py` has the reciprocal warning: the composer's only call
  site is `telephony_session_config.build_telephony_session_config()`.
- The two features stay on separate `build_*_session_config`
  functions with separate `VoiceSessionConfig.session_type` values
  ("telephony" vs "ask_ai") — so even at the orchestrator layer they
  are visibly different sessions.

### Mental model for future additions

```
                        ┌─────────────────────────┐
                        │  LLMProvider.stream_chat│
                        │   (system_prompt: str)  │
                        └────────────▲────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         │                           │                           │
┌────────┴──────────┐   ┌────────────┴───────────┐   ┌───────────┴─────────┐
│  Campaign calls   │   │  Ask AI (web demo)     │   │ Talky Assistant     │
│  telephony_bridge │   │  ask_ai_ws             │   │ assistant_ws        │
│        │          │   │         │              │   │         │           │
│        ▼          │   │         ▼              │   │         ▼           │
│ build_telephony…  │   │ build_ask_ai_session…  │   │ assistant_graph     │
│        │          │   │         │              │   │ (LangGraph +        │
│        ▼          │   │         ▼              │   │  SYSTEM_PROMPT)     │
│  compose_prompt   │   │ ASK_AI_SYSTEM_PROMPT   │   │                     │
│  (this sprint)    │   │   (fixed, short)       │   │                     │
└───────────────────┘   └────────────────────────┘   └─────────────────────┘
```

Every new feature that needs an LLM should pick a column. Do not have
one column import another column's prompt constants — if that impulse
arrives, the feature belongs in a new column.

## Adding a fourth persona later

1. Write `backend/app/services/scripts/prompts/personas/<role>.py`
   with a `_PERSONA` string and a `REQUIRED_SLOTS` tuple.
2. Register it in `prompts/personas/__init__.py` (`PERSONAS` dict +
   `REQUIRED_SLOTS_BY_PERSONA`).
3. Add a matching entry to `Talk-Leee/src/lib/campaign-personas.ts`
   (persona card + slot fields).
4. Update the `Literal` in `CampaignCreateRequest.persona_type` and
   `dashboard-api.ts` `PersonaType`.

No other code changes — composer, rotator, dialer chain, and all LLM
providers absorb the new persona automatically.
