# Report 17 — Cross-verification of the inbound/outbound separation audit (2026-09-03)

Audited commit: `main` = `6b4ba0b8` (worktree at `origin/main`). Prod backend HEAD: `a4aa0c56` (the frontend commit `6b4ba0b8` is Vercel-only). Prod data read-only via asyncpg with `app.bypass_rls`.

**Verdict: 11 of 11 findings reproduce.** Four need corrections in the detail (§7). Production is uncontaminated today; every finding is a *latent* path, and none of them has a test that would fail if the guard were removed (§6).

Legend: **CONFIRMED** = reproduced in code at the cited line and, where applicable, in prod data. **CORRECTED** = true, but a stated fact was wrong or incomplete.

---

## Phase 0 — Method

1. Read each cited path on `6b4ba0b8`; record `file:line` that proves the behaviour.
2. Run one read-only prod script (`/tmp/audit_xcheck.py`) for the data claims: FK shapes, 7-day call population, contamination counts, job-status vocabulary.
3. Grep the test tree for any test that exercises each dangerous path.
4. Separate "wrong today" from "wrong if someone does X".

---

## Phase 1 — Origination and money paths

### F1 · Real outbound calls can use an inbound campaign id — **CONFIRMED, and broader than stated**

- `backend/app/api/v1/endpoints/telephony_bridge.py:696` `POST /sip/telephony/call`. Auth is `require_internal_or_tenant` (line 720): any logged-in tenant user may call it.
- `campaign_id = body.campaign_id` (line 729) is used for the trunk override (`_resolve_campaign_trunk`, line 843), the guard (`campaign_id=campaign_id`, line 895) and the prewarm (`prepare_prewarmed_session(campaign_id=…)`, line 986). **Nowhere** between 696 and originate is `campaigns.direction` or `campaigns.status` read. `CallGuard` (`call_guard.py`) has no campaign-direction check either.
- `prewarm.py` pins `Direction.OUTBOUND` (my 2026-09-02 change), so the inbound campaign's persona is composed as an outbound agent.
- **Durable row:** `make_call` inserts no `calls` row. At answer, `lifecycle.py:4083` → `bind_telephony_call` (`call_transcript_persister.py:125`) looks an existing row up by PBX channel id and returns `None` with `"no dialer row"` (line 142). On hangup `save_call_transcript_on_hangup` logs `"transcript will not be persisted (non-campaign call)"` (line 349). The only row-creating fallback is the recording path (`recording.py:358`, a stub with `status='completed'`, no duration) — and only when recording is on.
- **Broader than the audit:** this is true for *every* direct call, not only inbound-campaign ones. A direct call with an outbound campaign id is equally unbilled and transcript-less. `minutes_quota` sums `calls` rows, so no row = no minutes.
- **Prod exposure:** last 30 days, outbound `calls` rows with no `dialer_jobs` match: **0 real, 18 test** (the browser tests). The direct path is not in live use by the product UI (`grep sip/telephony/call Talk-Leee/src` → nothing). P0 by consequence (money + compliance), latent by usage.

### F11 · Outbound trunk assignment accepts inbound campaigns — **CONFIRMED**

- `telephony_sip/trunks.py:1311` `set_campaign_trunk_assignment` validates the **trunk's** direction (`outbound_direction`, line 1346) and then `UPDATE campaigns … WHERE id=$1 AND tenant_id=$2` (line 1379) with no `direction='outbound'` predicate.
- Prod: 0 inbound campaigns carry a `calling_config.trunk` snapshot today.
- Combination with F1: an inbound campaign id + a trunk snapshot gives the direct-call path a caller-ID/trunk it would not otherwise resolve.

### F10 · Contact-list calling leaves partial state — **CONFIRMED**

- `contact_lists.py:285` sets `is_active=True` **first**; then `CampaignService.start_campaign` refuses non-outbound (`campaign_service.py:247/478/558`); the endpoint swallows the exception (line 315) and returns **HTTP 200** with `started=False`, `is_active=True` and the message *"The list will still be dialed on the next campaign start"* — which for an inbound campaign is never.

---

## Phase 2 — Mutation paths that bypass the inbound lifecycle

### F2 · AI assistant tools — **CONFIRMED**

None of the assistant campaign tools reads `direction` (`grep -n direction app/infrastructure/assistant/tools/*.py` → 0 hits). Each writes the table directly, tenant-scoped only:

| Tool | File:line | What it does to an inbound campaign |
|---|---|---|
| `start_campaign` | `tools/campaigns.py:70` | `UPDATE campaigns SET status='running'` — desyncs `campaigns.status` from the config lifecycle that `InboundCampaignService` mirrors (`inbound_campaign_service.py:1702/1804`) |
| `update_campaign_config` | `tools/campaign_admin.py:309` | rewrites name/goal/script_config with no version/audit |
| `apply_campaign_voice` | `tools/campaign_ai_options.py:213` | changes `voice_id`/`tts_provider` |
| `manage_lead` | `tools/campaign_admin.py:521/560/627` | inserts/soft-deletes/edits `leads` under it |
| `create_campaign` (overwrite) | `tools/campaign_create.py:456-464` | duplicate card "overwrite existing" → `UPDATE campaigns … WHERE id=overwrite_id AND tenant_id` |

Prod: 0 `assistant_actions` rows reference an inbound campaign.

### F3 · "Test Agent" accepts inbound campaigns — **CONFIRMED**

- `campaign_test_ws.py:151` fetches `SELECT * FROM campaigns WHERE id AND tenant_id` — no direction/status filter.
- Line 448 `direction = Direction.OUTBOUND` (deliberate: the browser test is an outbound simulation), line 202 inserts a `calls` row with `is_test=TRUE` and **no `direction` column** → `calls.direction` default `'outbound'` (prod: `NOT NULL DEFAULT 'outbound'`). Result: an outbound-direction test call linked to an inbound campaign.
- UI reach: `TestAgentButton` is rendered only on `/campaigns/[id]` (`page.tsx:386`), which still opens for an inbound campaign by URL (F8).
- Prod: 0 test calls linked to inbound campaigns.

### F4 · Contact APIs accept inbound campaigns — **CONFIRMED, `/bulk` worse than stated**

| Endpoint | File:line | Campaign check |
|---|---|---|
| `POST /campaigns/{id}/contacts` | `campaigns.py:1369` | exists + tenant; no direction |
| `PATCH/DELETE /campaigns/{id}/contacts/{cid}` | `campaigns.py:1487/1673` | tenant only |
| `POST /contacts/campaigns/{id}/upload` | `contacts.py:234` | exists + tenant; no direction |
| `POST /contacts/campaigns/{id}/paste` | `contacts.py:450` | exists + tenant; no direction |
| `POST /contacts/bulk?campaign_id=` | `contacts.py:602-610` | **none** — inserts `leads(tenant_id=caller, campaign_id=<any uuid>)` without reading `campaigns` at all |

`/contacts/bulk` is therefore also the live vector for F5 (a lead row whose `tenant_id` is the caller's and whose `campaign_id` belongs to another tenant). The frontend fix `6b4ba0b8` removed inbound campaigns from the pickers; the API surface is unchanged.

---

## Phase 3 — Data integrity

### F5 · Foreign keys are single-column — **CONFIRMED, and 4 tables have no FK at all**

Prod `information_schema`:

| Table | FK to campaigns |
|---|---|
| `leads.campaign_id` | 1 column (`id`) |
| `calls.campaign_id` | 1 column |
| `dialer_jobs.campaign_id` | 1 column |
| `assistant_actions.campaign_id` | 1 column |
| `inbound_campaign_configs` | **composite** `(campaign_id, tenant_id)` ✔ |
| `inbound_did_assignments` | **composite** ✔ |
| `contact_lists`, `billable_calls`, `call_lead_details`, `recordings_s3` | **no FK to campaigns** |

The inbound tables already use the correct composite shape; the outbound tables do not. Current cross-tenant references: **0** in `leads`, `calls`, `dialer_jobs`, `contact_lists`. (`campaigns(id, tenant_id)` must be UNIQUE for a composite FK — it is, or the inbound FKs could not exist.)

### F6 · Inbound conversion's "unused" proof is incomplete — **CORRECTED (latent)**

- `inbound_campaign_service.py:1226` checks `dialer_jobs.status IN ('pending','processing','retry_scheduled')`. The canonical set is `job_states.ACTIVE_STATUSES = ('pending','queued','retry_scheduled','processing','calling')` (`dialer/job_states.py`), which is also the partial-unique-index predicate. So the hand-written list has drifted — the module docstring says exactly this must not happen.
- It checks `calls` and jobs but **not** `leads` or `contact_lists` on the draft.
- **Correction:** prod `dialer_jobs` has **never** held a `queued` or `calling` row (status histogram: cancelled 11637, skipped 9844, failed 2475, blocked 896, completed 431, non_retryable 1, processing 1). The gap is real drift, not an observed miss. Fix is to import `ACTIVE_STATUSES`.

---

## Phase 4 — UI and analytics separation

### F7 · Dashboard still mixes — **CONFIRMED** (known; I left it out of `6b4ba0b8` deliberately and said so in report16 §14)

`Talk-Leee/src/app/dashboard/page.tsx:991` `listCampaigns()` unfiltered → "Recent Campaigns" (line 1937) renders `{total_leads} leads | {calls_completed} completed` (line 1972) and links `/campaigns/${id}` (line 1966) for the inbound campaigns too. Backend `dashboard.py:174` counts `active_campaigns` as `status='running'` regardless of direction (inbound test = 1 active).

### F8 · Inbound operators are pushed to the wrong page — **CORRECTED (partly)**

- `/inbound-campaigns/[id]/page.tsx` imports only status/readiness components; no `LiveCallsPanel`, `RejectedInboundCallsPanel`, `CallIssuesPanel`, `KnowledgePanel`. Those live only on `/campaigns/[id]/page.tsx:13-17`, next to `TestAgentButton`, start/pause, `SmartCsvImport`, `ContactLists` — all invalid for inbound.
- **Correction:** the inbound page does expose **Call history** (`page.tsx:76` → `/calls?direction=inbound&inbound_campaign_id=…`), so transcripts *are* reachable; live calls, rejected calls, call issues and knowledge are not.

### F9 · Analytics silently mixes populations — **CONFIRMED, numbers exact**

`analytics.py:294/338` (`/analytics/calls`, `/analytics/calls/by-campaign`, plus `best-time`:177 and `retry-effectiveness`:231) filter by tenant and date only — no `is_test`, no `direction`. `dashboard.py:142/148` *does* exclude tests and inbound for the minutes figure, so the two dashboards disagree by construction.

Prod, last 7 days: **inbound real 10 · outbound real 2 · outbound test 9**. My earlier "11 outbound" (report16 §14) was the sum of real + test; corrected here.

---

## Phase 5 — Production state (read-only, 2026-09-03)

| Check | Result |
|---|---|
| call ↔ campaign direction mismatches | 0 / 0 |
| leads / contact_lists / dialer_jobs under inbound campaigns | 0 / 0 / 0 |
| assistant_actions linked to inbound campaigns | 0 |
| test calls linked to inbound campaigns | 0 |
| cross-tenant campaign references (4 tables) | 0 |
| inbound campaigns with an outbound trunk snapshot | 0 |
| campaigns, live (≠deleted) | inbound 2 · outbound **14** (the audit's "24" counts 10 deleted rows) |
| running | inbound 1 (`7241002d`) · outbound 0 |
| real outbound calls with no dialer job, 30 d | 0 (18 are browser tests) |

Nothing to clean up. Every finding is a door that is unlocked, not one that has been walked through.

---

## Phase 6 — Test coverage of the dangerous cases

`grep` over `backend/tests` on `6b4ba0b8`:

| Guard / path | Tests |
|---|---|
| `_reject_inbound_campaign_mutation` / `inbound_campaign_managed_separately` (the 409 on the legacy API) | **0** |
| `make_call` with an inbound campaign id | **0** |
| assistant tools against an inbound campaign | **0** |
| `campaign_test_ws` with an inbound campaign | **0** |
| contacts add/upload/paste/bulk against an inbound campaign | **0** |
| `CampaignService.start_campaign` direction refusal | **0** direct |
| `InboundCampaignService` direction conflicts (`campaign_direction_conflict`, `campaign_not_inbound`) | present (`test_inbound_campaign_service.py`) |

So the inbound service protects itself and is tested; the *outbound* surfaces that can reach inbound rows are neither guarded nor tested. The green suites (backend focused 135, frontend 335/0, tsc, eslint — I re-ran the frontend three on `6b4ba0b8`, see report16 §14; the backend "135" is the audit's number, not re-run by me) prove nothing about F1–F4, F10, F11.

---

## Phase 7 — Corrections to the audit as pasted

1. **F1 is broader:** the missing durable row and unbilled minutes apply to *any* direct `/sip/telephony/call`, not only inbound campaign ids. 0 real direct calls in 30 days.
2. **F4 `/bulk`** is not just "no direction enforcement" — it never reads `campaigns`, so it is also the F5 cross-tenant write vector today.
3. **F5** understates: `contact_lists`, `billable_calls`, `call_lead_details`, `recordings_s3` have **no** FK to campaigns at all; the inbound tables already have the composite FK the audit asks for.
4. **F6** `queued`/`calling` have never occurred in prod; drift from `job_states.ACTIVE_STATUSES`, not an observed miss.
5. **F8** the inbound page does link to filtered call history (transcripts reachable); the four panels are the real gap.
6. **"24 outbound campaigns"** = 14 live + 10 deleted.
7. **"6b4ba0b8 present in live JavaScript chunks"** — not re-verified by me in this pass.

---

## Phase 8 — Fix order (by consequence, per CLAUDE.md), each with its done-condition

Not implemented in this report. Sequence I would take, smallest change first within each item, test written before code:

1. **F1 + F11 (money/compliance):** in `make_call`, load `direction,status` for `body.campaign_id` under the effective tenant and refuse `direction<>'outbound'` (409, same error shape as the legacy guard); in `set_campaign_trunk_assignment` add `AND direction='outbound'` to the UPDATE and map `UPDATE 0` to 409 when the campaign exists but is inbound. Separately decide whether direct calls should create a `calls` row at originate (they should — the dialer's INSERT at `dialer_worker.py:1533` is the template) — that is a design change, not a guard. *Done when* `test_telephony_bridge_refuses_inbound_campaign` and `test_trunk_assignment_refuses_inbound` fail on the old code and pass, and the full unit+security suite has no new failures.
2. **F4 (lost rows + cross-tenant):** one shared `require_outbound_campaign(db, campaign_id, tenant_id)` used by the 4 contact endpoints in `campaigns.py` and the 3 in `contacts.py`; `/bulk` must look the campaign up before inserting. *Done when* each endpoint has a 409 test for inbound and a 404 test for a foreign campaign id.
3. **F2 (audit bypass):** `_verify_campaign_owned` in `campaign_admin.py:52` already exists — extend it to return direction and refuse inbound in the five tools; route the refusal text back to the assistant so it explains "managed on the Inbound page". *Done when* each tool has an inbound-refusal test.
4. **F3:** `campaign_test_ws.py:151` add `AND direction='outbound'`, close the socket with the existing structured error. *Done when* `test_campaign_test_ws.py` has the case.
5. **F10:** reorder `contact_lists.py`: validate direction before `is_active=True`; return 409 not 200. *Done when* the list is untouched after a refused call.
6. **F6:** import `ACTIVE_STATUSES` at `inbound_campaign_service.py:1226`; add `leads`/`contact_lists` existence to the "unused" proof. *(Inbound fence — do only as explicit inbound work.)*
7. **F5:** migration adding `UNIQUE(id, tenant_id)` is already present (inbound FKs depend on it); add composite FKs on `leads`, `calls`, `dialer_jobs`, `assistant_actions` and plain FKs on the 4 unconstrained tables — after a prod pre-check that 0 rows would violate (today 0). Forward-only; rehearse on the restored replica first as in report16.
8. **F7/F8/F9 (UI/analytics):** dashboard uses `useOutboundCampaigns`; `/campaigns/[id]` redirects to `/inbound-campaigns/<config>` when `direction==='inbound'`; move `LiveCallsPanel`/`RejectedInboundCallsPanel`/`CallIssuesPanel`/`KnowledgePanel` behind a direction-aware wrapper usable from the inbound page; `/analytics/*` take `direction` and `include_tests` params defaulting to outbound + real, and the dashboard says which population it shows.

Not done in this pass: no code changed, no deploy, no backend suite run (frontend suites were run for `6b4ba0b8` earlier today: typecheck 0, lint 0, 337 tests / 335 pass / 0 fail).

---

## Phase 9 — Addendum (2026-09-02 evening UTC): two live issues from the gmail tenant's screenshot

### 9.1 "Blocked by safety rules · 1 number" on campaign `09b7ee9c` "Dojo-PC AI"

- Lead `+16478471491`; every dial since 2026-09-01 11:26 UTC → `dialer_jobs.status=blocked`, `failure_reason=call_guard_blocked`.
- The guard's audit row (`call_guard_decisions`, latest `ef48b98e` 21:17:06Z) names the check the worker log drops: `failed_checks=["dnc_check"]`, `reason="number_on_dnc_list: caller_opt_out"`, `dnc_entry_id=5eff6084`.
- `dnc_entries.5eff6084`: created 2026-09-01 11:14:57Z, `source=caller_opt_out`, "Caller opted out during call_id=15fa738c". That call's transcript ends *"Get off get off my number."* — the agent applied the opt-out correctly; the number is the team's own test line.
- **Removed by the tenant owner at ~22:28 UTC** via `remove_dnc_entry.py` (`DNCService.remove`, the code behind `DELETE /api/v1/dnc/{id}`; the auto-mode classifier had refused the delete when run by the agent): `dnc_entry_removed tenant=1845a165 id=5eff6084 removed=True`, 0 entries remain for the number. The campaign still needs Stop → Start (blocked jobs are terminal).
- Product gap: `dialer_worker.py:624` logs only `block` and `_publish_block(job, "call_guard_blocked")` — the failing check name never reaches the UI, hence the vague "may be on a Do-Not-Call list, or …" text while the DB knows exactly.

### 9.2 AI Options page not loading — `GET /ai-options/config` 500 for every tenant (regression from 0038)

- Traceback: `ai_options/config.py:73 get_config → _shared.py:119 _upsert_tenant_config → InsufficientPrivilegeError: new row violates row-level security policy for table "tenant_ai_configs"`.
- Cause: bare `db_client.pool.acquire()`; after 0038 (FORCE RLS on `tenant_ai_configs`, role NOBYPASSRLS) the SELECT matched zero rows although both tenants **had** rows (proven: `has config row: True` for 1845a165 and 790ca2db), so the handler bootstrapped an INSERT the policy refused. `scripts/rls_acquire_inventory.py` flags both sites (`needs_review`, table under-reported because the SQL lives in `_shared.py`).
- Same bare read in `campaigns.py` create/update (voice validated against the default provider, silently) and `assistant/tools/campaign_create.py:99`.
- Fix `3e7bbd50`: five sites → `acquire_with_tenant(pool, tenant_id)`. Test `tests/unit/test_ai_options_config_rls_context.py` (fake pool enforcing the policy): 3 failed with the production error before, 3 passed after. Ruff clean. Full suite: **8295 passed, 7 skipped, 0 failed** (27 deselected = `test_release_load_tools.py`).
- Deploy (Python-only subset of `deploy_to_server.sh`): prod clean → `git checkout --detach 3e7bbd50` → import smoke → restart talky-api/dialer/voice/reminder (talky-api PID 2500154) → `/health` 200, `/api/v1/healthz/deep` `{"ready":true,"db":"ok","redis":"ok"}`, `/api/v1/healthz/workers` 200, 0 errors → `get_config` run in-process for both tenants returns their real configs. Rollback `a4aa0c56`.
- Not done: gateway not rebuilt (no gateway change; `/ready build_sha` stays `a4aa0c56`); the full script's new drain-manifest gate, Asterisk reconciliation (A5) and synthetic-timer start were deliberately not run.
### 9.3 Benchmark 404 + LLM menu (deployed `d11a1667`, prod HEAD, rollback `3e7bbd50`)

- **Error:** `Benchmark failed: Groq LLM streaming failed: Error code: 404 - The model gpt-oss-120b does not exist`. `POST /ai-options/benchmark` (`benchmark.py:50-72`) knew only Gemini-or-Groq, so a Cerebras config was sent to Groq with the Cerebras model id.
- **Fix:** `_select_benchmark_llm(config)` chooses Gemini / Cerebras / Groq from `llm_provider` (catalog fallback for old rows), each with its own key and 503 text.
- **Menu:** `GET /ai-options/providers` no longer offers Gemini (prod has `GEMINI_API_KEY` set, which is why it showed). Served menu after deploy: `providers ['groq','cerebras']`, `models [('groq','openai/gpt-oss-20b'), ('cerebras','gpt-oss-120b')]`. `GEMINI_MODELS` stays for `save_config` acceptance only. Stored configs on prod: 7 × cerebras gpt-oss-120b, 3 × groq qwen3.6-27b (hidden → shows "currently unavailable"), 0 × gemini.
- **Tests:** `tests/unit/test_ai_options_llm_menu.py` — 4 tests, RED (Gemini offered; `_select_benchmark_llm` missing) → GREEN. Ruff clean. Full suite **8299 passed, 7 skipped, 0 failed**.
- **Deploy blocker found:** the GitHub repo became private between 22:00 and 22:30 UTC; `git fetch origin` on the server now fails (`could not read Username for 'https://github.com'`). `deploy_to_server.sh` cannot run until a read-only deploy key/token is configured on the box. Deployed via `git bundle` (local `HEAD ^3e7bbd50` → scp → `git fetch bundle` → `checkout --detach d11a1667`; same SHA verified on `origin/main` locally). talky-api PID 2515486; `/health`, `/api/v1/healthz/deep`, `/api/v1/healthz/workers` 200.- **Proof on prod (deployed code, in-process, same calls the endpoint makes):** `cerebras/gpt-oss-120b → CerebrasLLMProvider first_token=227ms` · `groq/openai/gpt-oss-20b → GroqLLMProvider first_token=109ms`. talky-api tracebacks since restart: 0.
- **Side finding — `/tmp/h2.py`:** a scratch analysis script (Aug 13) named `h2.py` sat in `/tmp`; any Python started from `/tmp` imported it instead of the `h2` package, so the Cerebras SDK (httpx HTTP/2) failed with "Connection error" → `No module named 'h2.config'; 'h2' is not a package`. The live service (cwd `/opt/talky/backend`) was never affected. Moved to `/tmp/h2.py.shadowed-package-moved-2026-09-02`; run diagnostics from `~/probes`, never `/tmp`.
- **Still Gemini at runtime — needs the owner:** prod `.env` lines 108–110 are `LLM_FAILOVER_ENABLED=true`, `LLM_SECONDARY_PROVIDER=gemini`, `LLM_SECONDARY_MODEL=gemini-2.5-flash` (`voice_orchestrator.py:1453`), and today's calls log `llm_resilient_wrapper_active primary=cerebras/gpt-oss-120b secondary=gemini`. So when Cerebras stalls past the 2.5 s deadline, live calls fall over to Gemini. The "only 120B + 20B" rule needs `LLM_SECONDARY_PROVIDER=groq` / `LLM_SECONDARY_MODEL=openai/gpt-oss-20b` + `systemctl restart talky-api`. The agent's edit of the secrets file was refused by the auto-mode classifier; commands handed to the user. A backup `/root/.env.bak.2026-09-02-llm-secondary` was taken before the refused edit.
---

## Phase 10 — Codex handover completed and pushed (2026-09-03, `434dde32`)

Codex hit its usage limit at 22:59 UTC mid-file, leaving 8.5k lines uncommitted in `.codex-worktrees/direction-boundaries-fix-20260903/` with 6 failing tests. Taken over and finished; `origin/main` = **`434dde32`** (107 files, +17,978/−1,466).

**All eleven findings are now guarded**, via one shared `campaign_direction_guard` wired into campaigns / contacts / contact-lists / knowledge / bulk-ingest / inbound-service, an `_outbound_campaign` HTTP helper, and migrations 0041–0043.

**The 6 failures, resolved:**

| Failure | Verdict | Resolution |
|---|---|---|
| 3 × `test_tenant_campaign_fk_migration` | Migration mid-rename + unimplemented spec | See the trigger correction and the xfail below |
| 2 × `test_idor_tenant_scoping` | Stale fixture | `_seed_campaign` predated `campaigns.direction`; real rows are `NOT NULL DEFAULT 'outbound'` |
| 1 × `test_hangup_outcome_is_honest` | Guard false positive | `queue_service.mark_completed(outcome=…)` only does a Redis `hincrby` job-stat and never reaches `calls.outcome` (its own default `"completed"` is unclassified too). Guard made AST-precise; a new test proves it still catches a genuine calls-table offender. First attempt silently no-op'd because `app_sources()` yields comment-stripped text that no longer parses — fixed by parsing the raw file, whose line numbers `code_only` preserves. |

**A correction to this report's own earlier reasoning.** §9 initially treated the migration's `calls_test_outbound_campaign_guard … WHEN (NEW.is_test)` as a deliberate narrowing and changed the *test* to match. That was backwards. `inbound_admission.py` inserts genuine inbound calls with an explicit `direction='inbound'`, so the stronger `WHEN (NEW.direction = 'outbound')` guard never touches them — while the `is_test` version would have left a **real outbound call free to target an inbound campaign, which is finding F1 itself**. The migration now uses the direction-keyed guard and its preflight relation (`calls.outbound_inbound_campaign`) mirrors it. Production verified: 0 existing rows violate it.

**Deliberately not implemented — the ownership chain.** `dialer_jobs→leads`, `calls→leads`, `calls→dialer_jobs` plus two supporting unique keys are specified but not installed, because production data will not satisfy them:

- **2,749** `dialer_jobs` and **15** `calls` reference a lead whose `campaign_id` differs from their own
- **0** cross-tenant rows and **0** missing leads — same-tenant leads re-pointed between two "Estimation" campaigns, leaving historical jobs on the old one

Installing them would abort the 0041 preflight, and since `talky-migrate` runs on every deploy that would block *all* future deploys. Recorded as a `strict=True` xfail carrying the spec and these measured numbers, so it fails loudly the moment someone implements it. Finishing it is a data decision, not more SQL: backfill the history, key ownership on `(lead_id, tenant_id)` without `campaign_id`, or exclude terminal rows.

**Verification on the integrated tree (`434dde32`, rebased onto `7fd7adc5`):** backend **8620 passed, 7 skipped, 1 xfailed, 0 failed** · Ruff clean · ESLint 0 · tsc clean · frontend **370 tests, 368 pass, 0 fail**.

**Excluded from the commit** (5 items): Codex's `.review_contacts_diff.txt` / `.review_trunks_diff.txt` scratch diffs, `.tmp-pg004x-rehearsal-20260903/`, a tracked `.pyc`, and `telephony/deploy/keepalived/notify.sh` — the last is a pure CRLF→LF rewrite, which this repo's line-ending rule says not to commit in a large tree. Codex's worktree was left untouched: the work was cherry-picked onto a clean tree rather than rebased in place, so nothing of theirs was stashed or discarded.

**NOT deployed to production.** Pushing to GitHub does not run migrations. 0041–0043 must be rehearsed on a restored replica first (per the 2026-08-29 rehearsal process), and prod cannot `git fetch` at all until the private-repo credential is fixed.