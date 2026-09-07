# 2026-09-08 — Floating assistant dead, inbound campaign creation blocked, agent pressing for email

Owner report: "floating assistant is not working", "not able to create a new inbound
campaign — it asks to select an already created one", "it insists on email, keeps
asking; if the user is not comfortable sharing it must not resist".

Method: reproduce from prod evidence first (journal, DB, transcripts), then fix at the
source. Each item: root cause → evidence → change → test.

## 1. Floating assistant — every socket closed the second it opened

| | |
|---|---|
| Symptom | Chat panel connects, then reconnects in a loop; voice never starts. |
| Evidence | `talky-api` journal, every attempt Sep 6–8: `WebSocket connected: <id>` and `WebSocket disconnected: <id>` **in the same second**, no error line between them. Nginx never saw a chat frame. |
| Root cause | `assistant_ws.py` and `assistant_voice_ws.py` resolved the JWT subject's tenant with `db_client.table("user_profiles")…single()`. A WebSocket never passes `TenantMiddleware`, so the contextvar is empty and the adapter installs the **nil tenant**. Migration 0038 (deployed 2026-08-30) put `user_profiles` under **forced RLS** and the app role has no `BYPASSRLS` (prod: `rolsuper=f, rolbypassrls=f`, policy `tenant_id = current_setting('app.current_tenant_id')`). `SELECT count(*) FROM user_profiles` as the app role without a tenant returns **0**. So the lookup returned nothing, the handler sent `User profile not found.` and `return`ed without closing or logging. The client treats any close as transient and reconnects with backoff, eight times. |
| Why it was invisible | The miss path had no log line; the only lines were connect/disconnect. The unit test faked `table("user_profiles")` to return a tenant, so it passed while prod failed. |
| Change | New `app/api/v1/ws_tenant.py::resolve_user_tenant(pool, user_id)` — the pooled user-scoped path REST auth uses (`acquire_with_tenant(pool, None, user_id=…)` + explicit subject predicate), the same shape `campaign_test_ws` already used. Both assistant sockets call it. A missing profile now logs a WARNING and closes 1008; a DB failure is distinguished (1011, "temporarily unavailable") instead of being reported as a missing profile. |
| Tests | `test_ws_tenant_bootstrap.py` (4): bypass form + subject predicate asserted, None on miss, failure propagates, voice socket uses the same path. `test_assistant_ws.py`: the fake table now **raises** on any `user_profiles` query (encodes the invariant); +2 tests for the close codes. |

## 2. New inbound campaign — "choose an existing inbound campaign"

| | |
|---|---|
| Symptom | `/inbound-campaigns/new` lists the tenant's campaigns with every option `· unavailable`, the select and Save are disabled. |
| Evidence | Prod `campaigns` for the owner's tenant `1845a165`: exactly one row, `dojo · outbound · running`. `isEligibleInboundBaseCampaign` accepts only `direction='inbound'` or an **unused outbound draft**; the server (`inbound_campaign_service.py:1226`) converts only a draft ("Only a draft outbound campaign can be converted to inbound"). `InboundCampaignCreateRequest.campaign_id` is required — there is no way to create the base AI campaign from the inbound flow, and `/campaigns/new` always lands on the outbound campaign page. |
| Root cause | A required dependency (an inbound-ready AI campaign) with no path to create it from where it is required. Correct behaviour for a tenant whose campaigns have all dialled out is "create a new one", and the UI offered no such step. |
| Change | Inbound form (create mode) shows a status box: when nothing is eligible it says why (a campaign that has dialled out keeps its history and cannot switch direction) and always offers **Create a new AI campaign** → `/campaigns/new?for=inbound`. The campaign creator (wizard and classic form) honours `?for=inbound` by returning to `/inbound-campaigns/new?campaign_id=<new draft>`; the inbound form pre-selects it (`initialInboundCampaignInput(value, { campaignId })`). Both pages read the query through `useSearchParams` inside a `Suspense` boundary (Next 16 static prerender). Server contract untouched: the draft is converted atomically by the existing service on save. |
| Tests | `campaign-create-return.test.ts` (3), `inbound-campaign-form.test.ts` (+3: preselect seeds a new form, a saved value wins, the create link is present). |

## 3. Agent kept asking for the email

| | |
|---|---|
| Evidence | Prod call `c63cdaff` (2026-09-07 19:29, tenant `1845a165`): agent "What's the best email to send you a brief overview?" → caller "…I know if I should give you my email" → agent "**Sorry — could you share the email** where I can send the details?" → caller "I just told you that I'm not very comfortable giving you the email". Campaign field config: `email` is **not** required, so this was not the lead-slot capture. |
| Root cause | Prompt. Two shared blocks scripted the email ask as the reply to **any** fact the agent does not have: hard rule 6 (`"…what's the best email for it?"`) and the composer's knowledge fallback (`"I'll get you that exact figure. What's the best email for it?"`). And nothing named hesitation as a refusal — rule 7 counted only two clear "no"s, so "I'm not sure I should" read as an unanswered question to re-ask. |
| Change | Rule 6 / composer fallback: "what's the best way to get it to you?", and if contact details were already declined the follow-up is the website or a callback. Rule 7 gains a hard cap: any contact detail (email, phone, address) is asked for **at most once per call**; hesitation IS a no; say "no problem", never re-ask, never explain why it was wanted, carry on with a step that needs nothing from them. Applies to every persona (shared guardrails). |
| Versions | `lead_gen@6` `2f9dc73448dcba14`, `customer_support@5` `e466ec4d2443d75f`, `receptionist@5` `821091476cbc551a` (pinned in `test_prompt_versions.py`; previous versions untouched). |

## Beyond brief — found while tracing, fixed because it broke my own gate

`transcript_json` stores every Deepgram **interim** as its own `user` row (`is_final: false`): call `c63cdaff` has **253** caller rows for a 212 s call, one sentence appearing ~15 times as it was recognised. The lead-qualification gate shipped 2026-09-07 counts caller turns from that array, so a single sentence satisfied the ≥3-turn substance check. `_caller_turns` now skips `is_final is False` rows (`test_caller_turns_ignore_stt_interims`). The storage bloat itself is not changed here.

Also seen, not fixed: `call_summarizer` 400 `json_validate_failed` — the model returned `action_items` as objects where the schema wants strings (Sep 6 18:47). One occurrence; summary for that call was lost.

## Verification

```text
cd Talk-Leee && npm run typecheck && npm run lint && npm test && npm run build
typecheck exit=0 · lint exit=0 · tests 465, pass 463, fail 0, skipped 2 · build exit=0

backend: ruff check app/ --select F --extend-ignore F401,F841 → All checks passed!
targeted: test_assistant_ws + test_ws_tenant_bootstrap + test_assistant_knowledge_authorization → 32 passed
          test_prompt_versions + identity_persist + rollback → 34 passed · call_summary/test_store → 20 passed
```

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q
8893 passed, 8 skipped in 774.62s (0:12:54)
```
