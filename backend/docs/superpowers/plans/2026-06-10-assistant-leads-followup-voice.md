# Plan — Assistant upgrade: follow-up tips, lead access, email-to-lead, voice-by-name, diff accept/reject

**Created:** 2026-06-10
**Owner:** Uzair
**Status:** PLANNED (phased, not started)

This is a large, multi-part assistant upgrade. It is broken into **5 independently
shippable phases** so we never lose context and can deploy + verify each on prod
before starting the next. Each phase has its own commit + deploy.

> Key finding from the code audit: **most of the plumbing already exists.** The
> assistant already has `get_leads`, `send_email` (Gmail + SMTP fallback),
> `apply_campaign_voice` (which already resolves a voice NAME → id), and
> `list_voices`. This update is mostly **surfacing, hardening, and building the
> diff-accept/reject UI** — not building from scratch.

---

## Architecture references (verified 2026-06-10)

**Assistant core**
- ReAct streaming loop: `backend/app/infrastructure/assistant/streaming.py` (loop 67–179, `dispatch_tool` at 155)
- LangGraph fallback path: `backend/app/infrastructure/assistant/agent.py`
- Tool dispatch: `backend/app/infrastructure/assistant/tools/dispatch.py` (`_CONVO_AWARE` set line 21, dispatch 24–56)
- Tool registry: `backend/app/infrastructure/assistant/tools/__init__.py` (QUERY_TOOLS / ACTION_TOOLS / ALL_TOOLS)
- LLM tool schemas: `backend/app/infrastructure/assistant/tools/llm_schemas.py`
- WS endpoint + frames: `backend/app/api/v1/endpoints/assistant_ws.py` (auth 213–301, streaming bubble 376–459, persistence 474–509)
- Conversation persistence: `assistant_conversations` table; `context` JSONB column exists but is currently unused (good place to stash a pending proposal)
- System prompt: `agent.py` (~line 80 documents the tools + the "confirm=false first" rule)

**Tools that matter here**
- `get_leads` — `tools/leads.py` 19–49 (returns id, phone, name, email, status, priority, call_attempts, last_call_result — **does NOT yet return is_lead/follow_up_note/qualified_at**)
- `send_email` — `tools/comms.py` 30–112 (Gmail if connected → SMTP fallback → `EmailNotConnectedError`); conversation-aware
- `apply_campaign_voice` — `tools/campaign_ai_options.py` 85–191; `_resolve_voice` 61–82 already does: exact id → exact name → unique substring of name → unique substring of id
- `list_voices` — `tools/campaign_ai_options.py` 194–210
- Edit tools returning the standard diff (`{preview, changes:[{field,before,after}]}` when `confirm=false`): `update_campaign_config`, `update_knowledge_node`, `manage_lead`, `apply_campaign_voice` (all in `tools/campaign_admin.py` + `campaign_ai_options.py`); `_build_diff` at `campaign_admin.py` 66–76

**Summary**
- Generator + prompt + schema: `backend/app/domain/services/call_summary/summarizer.py` (prompt 30–53, `EMPTY_SUMMARY` 59–70, `_STR_KEYS`/`_LIST_KEYS` 72–78)
- Store + lead marking: `backend/app/domain/services/call_summary/store.py` (`mark_lead_from_summary` 146–183; `follow_up_note` = `next_step` → `headline` → default, line 159)
- API: `GET /calls/{id}/summary` — `backend/app/api/v1/endpoints/calls.py` 514–533 (lazy-generate + idempotent; returns full dict)
- Frontend: `Talk-Leee/src/components/calls/CallSummaryCard.tsx`; types in `Talk-Leee/src/lib/dashboard-api.ts` (`CallSummaryObj` 100–111)

**Voice catalog**
- Per-provider catalogs: ElevenLabs `backend/app/infrastructure/tts/elevenlabs_catalog.py`; Google/Deepgram/Cartesia `backend/app/api/v1/endpoints/ai_options/_catalog.py`
- Voice config get/save: `backend/app/api/v1/endpoints/ai_options/config.py`
- Voice model: `backend/app/domain/models/ai_config.py` (`VoiceInfo` 95–109)
- Campaign storage: `campaigns.voice_id` + `campaigns.tts_provider`

**Email**
- OAuth/primary: `backend/app/services/email_service.py` (`send_email` 224–332)
- SMTP fallback: `backend/app/domain/services/email_service.py` + `backend/app/infrastructure/connectors/email/smtp.py`
- Config: `.env` SMTP_* (Office365, port 587 STARTTLS). **Known issue: 535 auth failure** — needs SMTP AUTH enabled in M365 / app password, or use Gmail OAuth connector.
- Lead email column: `leads.email` (nullable)

---

## PHASE 1 — Follow-up tips in the call summary  ← smallest, ship first

**Goal:** the post-call AI summary produces an explicit, actionable
`follow_up_tips` list (distinct from the existing `next_step`), it's stored, used
as the lead's `follow_up_note`, and rendered on the call summary card.

**Backend**
1. `summarizer.py`: add `"follow_up_tips": ["<actionable suggestion to follow up effectively — what to say/do, best timing, what to send>"]` to the prompt JSON (30–53) and to the Rules block.
2. `summarizer.py`: add `"follow_up_tips": []` to `EMPTY_SUMMARY` (59–70) and `"follow_up_tips"` to `_LIST_KEYS` (72–78).
3. `store.py` `mark_lead_from_summary`: prefer `follow_up_tips[0]` for `follow_up_note`, then fall back to `next_step` → `headline` → default.
4. No DB migration (lives in `summary_json` JSONB). No API change (endpoint returns the full dict).

**Frontend**
5. `dashboard-api.ts`: add `follow_up_tips?: string[]` to `CallSummaryObj`.
6. `CallSummaryCard.tsx`: render a "Follow-up tips" section (after Action Items / Next Step), bulleted, with a distinct accent so it reads as guidance.

**Deploy:** backend git pull + `systemctl restart talky-api` (--workers 1); frontend Vercel from repo root.
**Verify:** open a call with a transcript → summary shows follow-up tips; a newly-qualified lead's `follow_up_note` reflects the first tip. Existing summaries are unaffected (re-generate one to see tips).
**Risk:** very low (additive JSON field).

---

## PHASE 2 — Diff accept/reject UI (task #21, the "edit-with-confirm" proposal flow)

**Goal:** when the assistant proposes an edit, the user gets a **field-by-field
diff card with Apply / Reject buttons** instead of the fragile "type yes" loop.
This is the safety layer for ALL edit tools (campaign config, knowledge node,
lead manage, voice apply), so doing it before the lead/voice/email phases makes
those edits land through a clean UX.

**Backend**
1. New `backend/app/infrastructure/assistant/proposals.py`: create/load/clear a pending proposal persisted in `assistant_conversations.context` (survives WS reconnects). Shape: `{proposal_id, tool, args, changes:[{field,before,after}], created_at}`.
2. Streaming loop (`streaming.py`) + WS (`assistant_ws.py`): when an edit tool returns `{preview:True, changes:[...]}` (confirm=false), instead of feeding the diff back as tool text, **persist a proposal + emit a new `edit_proposal` frame** `{proposal_id, tool, summary, changes}`.
3. New client→server messages handled in `assistant_ws.py`:
   - `apply_proposal {proposal_id}` → re-validate tenant ownership, re-run the same tool with `confirm=true`, `audit_logger.log_admin_action`, emit `proposal_result {proposal_id, applied:true, changes}`.
   - `reject_proposal {proposal_id}` → clear it, emit `proposal_result {proposal_id, applied:false}`.
4. System prompt: stop instructing the "type yes" confirmation; tell the model the UI drives `confirm=true` via the buttons. Keep "call with confirm=false first."

**Frontend**
5. New `Talk-Leee/src/components/assistant/edit-proposal-card.tsx` + `diff-view.tsx`: render `changes` (before red → after green; word-level via `jsdiff` for long text) with Apply / Reject. Wire to the assistant WS client to send `apply_proposal` / `reject_proposal` and react to `proposal_result`.
6. Assistant message list: render an `edit_proposal` frame as the card; lock the buttons after a `proposal_result`.

**Deploy:** backend + frontend.
**Verify:** ask the assistant to rename a campaign / change its goal → diff card appears → Apply writes + shows applied; Reject discards; reconnect mid-proposal still resolves (context-persisted).
**Risk:** medium (new protocol frames + state). Mitigate: keep the old `assistant_message` path as fallback if `edit_proposal` unhandled.

---

## PHASE 3 — Assistant lead intelligence (read all leads + follow-up on request)

**Goal:** the assistant can read across all the tenant's leads and, when asked
"show me the follow-up for <lead>", return that lead's follow-up summary/tips.

**Backend**
1. Extend `get_leads` (`tools/leads.py`): add `is_lead`, `follow_up_note`, `qualified_at` to the returned fields; add an `only_leads: bool` filter (is_lead=true) and raise the default `limit` (e.g. 25, capped 100) so "all my leads" works. Keep tenant scoping (RLS + explicit `tenant_id`).
2. New tool `get_lead_followup` (`tools/leads.py` + register in `__init__.py` + schema in `llm_schemas.py`):
   - Params: `lead_id?` OR `phone_number?` OR `name?` (resolve within tenant; if ambiguous return the candidates).
   - Returns: the lead's `is_lead`, `follow_up_note`, `qualified_at`, and the **qualified call's summary** (`headline`, `outcome`, `next_step`, `follow_up_tips`, `action_items`) by joining `leads.qualified_call_id → calls.summary_json`.
3. System prompt: document the two read tools + when to use `get_lead_followup` ("when the user asks for follow-up / next steps on a specific person").

**Frontend:** none required (assistant renders markdown). Optional: a "Ask assistant about this lead" affordance on the contacts row later.

**Deploy:** backend only (+ restart).
**Verify:** "list my qualified leads" → returns the flagged leads with notes; "what's the follow-up for <phone>" → returns tips + next step from that lead's qualified call.
**Risk:** low (read-only, tenant-scoped). Depends on Phase 1 (follow_up_tips exist) for richest output.

---

## PHASE 4 — Assistant email to a lead's provided address

**Goal:** "send the follow-up email to this lead" works — the assistant resolves
the lead's stored email and sends, gated by the Phase-2 diff/confirm UX.

**Prereq (blocking): fix outbound email. DECIDED 2026-06-10 → fix Office365 SMTP**
(option a). Enable SMTP AUTH for `noreply@talkleeai.com` in the M365 admin center
and use an app password if MFA is on; keep the `noreply@talkleeai.com` sender.
Verify with `backend/send_test_email.py` before wiring the assistant path.
(Gmail OAuth was the alternative, not chosen.)

**Backend**
1. `send_email` tool (`tools/comms.py`): accept `lead_id?` / `phone_number?` and, when present and `to` is omitted, look up `leads.email` (tenant-scoped) to populate the recipient. Error clearly if the lead has no email on file.
2. Route the send through the **confirm/proposal** flow from Phase 2 (preview: to / subject / body) so nothing is sent without an explicit Apply. (send_email is already conversation-aware + audit-logged via `assistant_actions`.)
3. Optional: a `follow_up` email template seeded from the lead's `follow_up_tips` so "email them the next steps" drafts itself.

**Deploy:** backend (+ restart) after email is verified working.
**Verify:** "email <lead> the follow-up" → preview card with the resolved address + drafted body → Apply → row in `assistant_actions` with `status` set + message id; recipient receives it.
**Risk:** medium — **sends real email**. Hard-gate behind confirm; never auto-send. Watch for accidental sends to wrong leads (show the resolved address in the preview).

---

## PHASE 5 — Voice by name (assistant hardening + visible catalog)

**Goal:** changing a voice never requires the user to know an exact id — they can
say a name ("use Sarah" / "the deep male one") and can ask "what voices are
available."

**Backend**
1. Confirm/keep `apply_campaign_voice` name→id resolution (`_resolve_voice` 61–82). Improve the ambiguous/not-found return to always include the candidate `{name,id}` list so the model can disambiguate in one turn.
2. Ensure `list_voices` returns name + id + (gender/accent/preview where available) for all four providers, and that the system prompt tells the model to call it when the user references a voice by name or asks what's available.
3. Route voice changes through the Phase-2 proposal UI (it already returns the standard diff), so the user sees "voice: <old name> → <new name>" before Apply.

**Frontend (AI Options page)**
4. Audit the voice picker: ensure it shows human **names** (and plays previews) and never asks the user to paste a raw id. If any field still shows/needs a raw id, replace with the name-based picker fed by the providers endpoint.

**Deploy:** backend + frontend.
**Verify:** "change DOJO's voice to Sarah" → diff card (name→name) → Apply updates `campaigns.voice_id`; "what voices can I use?" → assistant lists names; AI Options page shows names only.
**Risk:** low.

---

## Cross-cutting notes
- **Tenant safety:** every new/extended tool keeps explicit `tenant_id` filtering + RLS contextvar; edit/apply paths re-validate ownership at apply time (Phase 2).
- **No global config mutation:** voice changes are per-campaign (safe). Never touch `global_ai_config` (see [[global_ai_config_process_global]]).
- **Deploy discipline:** telephony stays `--workers 1`; backend via git pull + restart; frontend via Vercel from repo root ([[vercel_frontend_deploy]]); Uzair sole commit author.
- **Order rationale:** 1 (tips) is foundational + tiny; 2 (diff UI) is the safety layer every later edit flows through; 3 (read) is low-risk and uses tips; 4 (email) is gated on SMTP + uses the diff UI; 5 (voice) is mostly hardening + UX.

## Suggested sequencing
Ship 1 → 2 → 3 → 4 → 5, each its own commit + prod verify. Phases 3/4/5 can be
re-ordered if email is blocked on the M365/Gmail credential — do 5 before 4 in
that case.
