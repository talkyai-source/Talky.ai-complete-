# Assistant Campaign Tools — Design & As-Built

**Date:** 2026-06-05
**Status:** Implemented on branch `feat/assistant-campaign-tools` (pending merge + live test)
**Supersedes:** the earlier `2026-06-05-floating-campaign-copilot-*` spec/plan, which proposed a **separate** copilot (new `/copilot/chat` endpoint, Gemini tool-loop, proposal diff-cards, a second `CampaignCopilot` widget). That approach was **discarded** once we found the product already had a dashboard assistant — see "Course correction" below.

## Summary

Give the **existing** dashboard assistant (the `FloatingAssistant` chat, backed by the LangGraph ReAct agent in `backend/app/infrastructure/assistant/`) the ability to **read** a campaign's config + knowledge tree (including a **live RAG-retrieval test**) and **edit** campaign data — campaign basics, knowledge nodes, leads (incl. phone numbers), and per-campaign voice/provider (AI options) — with **conversational confirmation** before any write.

## Course correction (why not the parallel copilot)

The first design anchored on the *voice* "Ask AI" popup and proposed building a brand-new text copilot from scratch. During implementation we discovered the product **already ships** a text chat assistant:
- Frontend: `Talk-Leee/src/components/assistant/floating-assistant.tsx` (WebSocket chat, mounted in the dashboard shell).
- Backend: `backend/app/infrastructure/assistant/agent.py` (LangGraph ReAct agent on Groq) + `assistant_ws.py` + a tool registry, with read tools (`get_campaigns`, …) and action tools (`start_campaign`, …) already wired.

So the correct, much smaller scope is to **add tools to that framework**, not build a second one. The parallel build was deleted.

## Architecture

The assistant is a LangGraph ReAct agent (Groq `llama-3.3-70b-versatile`). Tools live in a registry (`QUERY_TOOLS` / `ACTION_TOOLS` / `ALL_TOOLS`) of `{name: {function, description, input_schema}}`. Each tool is `async def fn(tenant_id, db_client, <args>) -> dict`, tenant-scoped via the `db_client.table(...).eq("tenant_id", …)` builder or `acquire_with_tenant(db_client.pool, tenant_id)` for raw SQL. The agent exposes tools to the LLM via hand-written Groq schemas and dispatches them; unrecognised names fall through to a registry-driven dispatch.

### Feature folder (every file ≤600 lines)
The 938-line `tools.py` god-file was split into a package:
```
backend/app/infrastructure/assistant/tools/
├── __init__.py          # re-export shim + QUERY_TOOLS / ACTION_TOOLS / ALL_TOOLS
├── dashboard.py · leads.py · campaigns.py · calls.py · comms.py · meetings.py · workflow.py   # existing tools, moved verbatim
├── llm_schemas.py       # GROQ_TOOL_SCHEMAS (hand-written LLM tool defs, extracted from agent.py)
├── campaign_admin.py    # NEW: campaign read + edit-with-confirm + lead management
└── campaign_ai_options.py  # NEW: per-campaign voice/provider (AI options)
```
`assistant_agent_service.py` (the action-plan executor) was also split — step execution moved to `assistant_plan_steps.py` — to stay ≤600.

## New tools

**Read (QUERY_TOOLS):**
- `get_campaign_detail(campaign_id|name)` — full config (persona, company, voice, knowledge mode, script_config).
- `get_knowledge_tree(campaign_id)` — nodes (heading, summary, enabled, hit_count).
- `retrieve_knowledge(campaign_id, query)` — runs the LIVE retriever (`bump_hits=False`); shows exactly what the agent would pull from the tree. The owner's RAG-quality test.

**Edit (ACTION_TOOLS), all confirm-gated:**
- `update_campaign_config(campaign_id, changes, confirm)` — persona_type, company_name, agent_names, additional_instructions, name, goal (script_config merged so untouched keys survive). Voice/provider deliberately excluded here (they need provider validation — use `apply_campaign_voice`).
- `update_knowledge_node(campaign_id, node_id, changes, confirm)` — heading/content/enabled/priority/summary/voice_answer; recomputes `search_text` + `search_tsv` when heading/content change (mirrors the knowledge PATCH endpoint).
- `manage_lead(campaign_id, action, …, confirm)` — `add` / `update` (edit phone_number/name/email) / `remove` (soft delete, `status="deleted"`).
- `apply_campaign_voice(campaign_ids, tts_provider, voice_id, confirm)` — per-campaign AI options; validates the voice against the provider via `_valid_voice_ids_for_provider` (mirrors `apply-tts-config`).

## Confirmation model

Conversational, enforced at the **tool level**: every edit tool takes `confirm: bool = False`.
- `confirm=False` → reads current state, returns a before→after preview, **writes nothing**.
- `confirm=True` → applies the change.
The system prompt instructs the agent to always preview first, show the user the before→after in plain language, and only re-call with `confirm=true` after explicit approval. No UI/diff-card was needed — it runs entirely in the existing chat.

## Safety

- **Tenant isolation:** tenant_id comes from the authed WebSocket session, never the LLM; every query is tenant-scoped.
- **No silent writes:** edits never apply without `confirm=true` (which the agent only sends after the user agrees).
- **Multi-tenant landmine avoided:** the global LLM-model config (`global_ai_config.set_global_config`) is a **process-global singleton shared across all tenants** — so the assistant deliberately has **no** tool to change it and tells users to use the AI Options page. (See memory `global-ai-config-process-global`.)
- Edits reuse the same DB columns/validation the typed endpoints use, so they inherit existing constraints.

## Testing

`backend/tests/unit/test_assistant_campaign_admin.py` + `test_assistant_ai_options.py` cover: confirm preview-without-write, confirm apply, tenant scoping, soft-delete remove, lead update, voice validation. Existing `test_assistant_agent.py` / `test_assistant_agent_service.py` are the characterization safety net for the refactors. Full assistant suite: **46 passing**.

## Commits (branch `feat/assistant-campaign-tools`)

1. split `tools.py` → `tools/` package (behavior-preserving)
2. `campaign_admin.py` read + edit-with-confirm tools
3. expose those to the agent (schemas extracted to `llm_schemas.py`, registry-fallback dispatch)
4. AI-options (campaign voice/provider) + lead edit (update/soft-delete)
5. expose `apply_campaign_voice` + `manage_lead` update to the agent
6. extract `assistant_plan_steps.py` (service ≤600)

## Not done / future

- Global LLM-model editing (intentionally excluded — process-global; would need a per-tenant config refactor first).
- A richer diff-card UI (we chose conversational confirm; a card would need WS-protocol + `FloatingAssistant` changes).
