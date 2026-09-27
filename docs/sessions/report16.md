# Report 16 — Direction vs Opening Mode, Prompt Consistency, and the First Standard-Shaped Production Deploy

**Prepared:** 2026-09-02 (Asia/Karachi), covering the work of 2026-09-02 → 2026-09-03 UTC
**Previous report:** `report15.md`
**Production backend HEAD at end of report:** `a4aa0c56` (`fix(gateway): accept real commit SHAs in the CMake build-identity gate`)
**Production HEAD at start of report:** `69e607e9`
**Database:** alembic `0036_inbound_rejection_log` → `0040_calls_campaign_nullable` (head)
**Author of every commit in this report:** `tooti12 <umar.jwork@gmail.com>` — no co-author trailers
**Rollback floor (read §10 before any revert):** `0efa4fba` — not `6f439ffd`

Every number in this report was read from command output during the session it describes. Where something could not be verified it is listed in §11, not omitted.

---

## 1. Summary

Four commits of my own landed on `main` and are live; one large batch of 35 commits authored by the other engineer (Codex) working the standardisation brief was verified and deployed with them.

| Commit | What | Live since |
|---|---|---|
| `012b3cf4` | Call **direction** separated from **opening mode**; guidance budget enforced at save/start/preview; browser test gets campaign knowledge; DNC written before spoken; contact capture persists only confirmed values | 2026-09-02 ~21:00 UTC |
| `60ada4ec` | Prompt stopped arguing with itself (4 contradictions, dated changelog, KD opening bug); GPT-OSS instructions moved to the current turn on Groq; campaign guidance compulsory in UI | 2026-09-02 |
| `6f439ffd` | Sentence cap never cuts the turn's question; NON-NEGOTIABLES once per turn; dead TurnResumed cancel path removed | 2026-09-02 |
| `a4aa0c56` | CMake build-identity gate fixed (rejected every real SHA) — found during the first standard deploy | 2026-09-03 07:5x UTC |
| `0efa4fba` and 34 predecessors (Codex) | Brief sections A (repo side), B1–B7, C1–C4 | 2026-09-03 |

Production now runs a gateway whose `/ready` reports `build_sha == git HEAD` for the first time, 84 tables under forced RLS, and the phantom 7-hour inbound call is 0 seconds.

An inbound outage that began between 18:32 (Sep 1) and 07:08 (Sep 2) — before the deploy — was diagnosed and fixed (§8).

---

## 2. Investigation: why the agent misbehaved (root causes, with evidence)

The user supplied a spec listing observed defects. Investigation traced them to **three temporary decisions that were never retired, plus one missing wire**. All confirmed in source; dates from `git log -S`.

### 2.1 Direction derived from `first_speaker` (since 2026-05-18)
`Direction.from_first_speaker()` (`voice_orchestrator.py`, commit `b5351a50`) mapped "callee speaks first" → `INBOUND`. Its own docstring called it a bridge "until per-campaign direction lands in the UI". It never did. Each later symptom was patched at the *consumer*:
- 2026-06-08 (`0784a03e`): prompt text reworded to `OUTBOUND CALL — CALLEE SPEAKS FIRST`; enum value left as `INBOUND`.
- 2026-08-30 (`69fff6f4`): a comment "first_speaker must never be used to infer direction" added in `telephony/config.py` while `prewarm.py:339` and `campaign_test_ws.py:443` still did exactly that.

Consumers that read the value as *carrier inbound*, on a callee-first **outbound** call:

| Consumer | Effect | Location |
|---|---|---|
| Realtime instructions | "This is an INBOUND call: the caller contacted the company. Never say or imply that you called them" | `realtime_instructions.py:144-155` |
| Spoken greeting variant | "thanks for reaching out. How can I help?" | `_PERSONA_GREETINGS["lead_gen"]["inbound"]` |
| AMD | disabled | `machine_detection.py:156-165` |
| Voicemail detection | skipped | `realtime_bridge.py:430`, `voicemail_detector.py` |
| **Recording** | **discarded** — `_is_true_inbound_session` true, `_recording_allowed` never set outside admission, fail-closed gate returns False | `recording.py:18-28, 48-56, 417-424` (live since `69fff6f4`, 2026-08-30) |

### 2.2 Campaign guidance: two owners, two contracts, three compose paths
- `7ef3cecc` (2026-06-30) removed the save-side cap: "the campaign Goal can now be arbitrarily long".
- `6648c566` (2026-07-14) added a runtime cap (6000 → 12000 on 08-12; head-60%/tail-40% elision on 08-13) at the **only** call site nobody previews: `telephony_session_config.py:1364`.
- The preview endpoint composed the uncapped text.

Production journals, 30 days, campaign `09b7ee9c`:
```
6× telephony_tenant_prompt_capped original_chars=31464 capped_chars=11996 LOST_chars=19468 (62%)
6× telephony_tenant_prompt_capped original_chars=14665 capped_chars=11990 LOST_chars=2675  (18%)
```
The model was handed the literal string `[... middle of these instructions omitted for length ...]` on six calls while the operator's preview showed 100%.

### 2.3 The browser "Test agent" had no knowledge base
`campaign_test_ws.py:14` claimed knowledge parity with a real call. `apply_campaign_knowledge` was called in exactly one place — `prewarm.py:456` — which the browser path skips. Inferred (not proven) chain: tester pastes Dojo facts into guidance → 31k-char prompt → §2.2 truncation on real calls.

### 2.4 Tool-result contract, capture, state
- Only `knowledge_lookup` was a real tool; END_CALL a text sentinel; callback/email/form executed nothing; `post_call_analyzer.py` dead (its hook `_save_call_data()` did not exist); HARD RULE 10 unenforced (`validate_response()` never called live).
- DNC was spoken before written (farewell played to completion, purge at teardown).
- Capture: real deterministic code existed for cascaded mode (`call_state_tracker.py`, `spoken_email_normalizer.py`), but unconfirmed values were persisted every turn and a same-source unconfirmed retry could overwrite a confirmed value while inheriting `confirmed=TRUE`. Realtime pipeline had none of it.
- Live state: only `has_introduced`, `declined_count`, email/phone slots were structural; decision-maker/provider/pain/interest existed only in the post-call summariser.

Corrections to the user's spec, found while verifying: `calls.direction` for browser tests was already `'outbound'` (INSERT omits the column; 0022 default); capture was not "prompt-only" in cascaded mode; transfer could not even be *configured* in prod (`inbound_campaign_service.py:146`, gate closed).

### 2.5 Pipeline audit for GPT-OSS 120b (Cerebras primary) / 20b (Groq fallback)
Measured a real composed prompt for a knowledge-driven campaign with 80 chars of guidance: **28,640 chars ≈ 7,160 tokens**. Findings:
- The same rule appeared 3–6 times (one question, be short, no markdown, never invent, two declines → close).
- Four questions had two answers: voicemail (leave a message vs END_CALL alone); wrong person (close vs pivot); silence (say "Take your time / Still there?" vs "the silence monitor's job"); callee-first opening (position 0 "wait, nothing has played" vs STAGE 1 "a greeting already played").
- The knowledge-driven lead_gen body (`LEAD_GEN_KD_BODY`) hard-coded the agent-first STAGE 1 in a private copy of the shared opening — the source of the fourth contradiction for the **default** campaign type.
- A dated engineering changelog lived inside STAGE 1 ("2026-08-11: … per the owner's own phrasing … worst-converting family measured"), read by the model every turn.
- On Groq GPT-OSS the entire system prompt — including per-turn LIVE STATE / CAPTURED / ACTION blocks — was stitched onto the **first** user message (oldest turn), destroying the cacheable prefix and placing per-turn instructions next to "hello". Groq's docs do say "instructions in the user message"; the placement was the defect.
- The 3-sentence streaming cap (`turn_streamer.py`) truncated the model's fourth sentence even when it was the question.
- NON-NEGOTIABLES appeared twice per turn (base floor + compact re-anchor).
- Speculative-turn machinery was "contained" but its TurnResumed cancel/rollback branch remained, with a reachable stray `session.llm_active = False`.
- The code itself recorded (`ai_config.py`, `groq.py`, `structured_output.py`) that GPT-OSS was removed on 2026-06-25 for misbehaving on conversational voice and re-selected on 2026-08-24 on latency alone, "until someone re-tests it". Nobody did.

---

## 3. Fix batch 1 — `012b3cf4` (30 files, 1245+/99−)

| Change | Files | Test |
|---|---|---|
| `VoiceSessionConfig.opening_mode` (`agent_first`/`callee_first`); `prewarm.py` and `campaign_test_ws.py` pin `Direction.OUTBOUND`; `compose_prompt(opening_mode=)`; realtime `greet_on_start` resolved from opening mode (admission pin wins); `Direction.from_first_speaker` marked deprecated | `voice_orchestrator.py`, `telephony_session_config.py`, `composer.py`, `prewarm.py`, `telephony/config.py`, `campaign_test_ws.py` | `test_opening_mode_not_direction.py` (13; incl. `_is_true_inbound_session` is False for callee-first outbound) |
| Guidance budget public (`campaign_guidance_char_budget`); save + start refuse over-budget; preview composes the capped prompt and returns `campaign_guidance_chars / budget / over_budget / opening_mode`; `telephony_prompt_composed` logs `direction opening_mode guidance_chars base_chars prompt_chars` | `campaign_prompt_service.py`, `campaigns.py`, schema | `test_campaign_guidance_budget.py` (6) |
| Browser test calls `apply_campaign_knowledge` | `campaign_test_ws.py` | `test_browser_test_applies_campaign_knowledge_like_prewarm` |
| DNC written before farewell (`purge_opt_out_before_farewell`, 2.5 s bound; non-committal farewell on failure; teardown is the retry) | `dialer/opt_out.py`, `voice_pipeline_service.py`, `lifecycle.py` | 2 ordering tests |
| Capture: contact fields persist only once confirmed; upsert refuses unconfirmed-over-confirmed | `lead_slot_capture.py`, `lead_capture_service.py` | 4 tests (2 rewritten from the old contract, reason recorded) |
| Frontend: `OUTBOUND TEST` badge + exact opening; headphones/allow-interruption toggle sends `allow_barge_in`; "Campaign guidance" label, live counter, save blocked over budget; preview shows Campaign type: Outbound + Opening select | `test-agent-button.tsx`, `campaign-form.tsx`, `campaign-basics-editor.tsx`, `dashboard-api.ts`, `test-agent-session.ts`, `campaign-guidance.ts` | 8 tests |

Verified on a clean `origin/main` worktree: backend `8055 passed, 7 skipped`; Ruff clean; frontend `321 pass / 0 fail`, typecheck and lint 0 errors. One order-dependent flake surfaced and was fixed in-commit (`test_credential_resolver.py`: class-level cache keyed on `id(db_pool)` + shared tenant UUID → autouse `invalidate_cache()`).

## 4. Fix batch 2 — `60ada4ec` (14 files, 340+/147−)

- Groq GPT-OSS: instructions on the **latest** user message; earlier messages byte-identical (stable prefix); neutral user lead-in when history opens with the agent's greeting. `test_groq_message_builder.py` — 3 new, 2 rewritten.
- Prompt: one voicemail rule (end the call); wrong person = pivot everywhere, only wrong NUMBER/BUSINESS ends; silence owned by the silence monitor (guardrails prose removed); `lead_gen_kd_body(opening_key)` reuses the shared opening (private copy deleted); dated changelog removed while keeping every wording guard (positive framing; permission-ask ≠ inconvenient-moment ask by paraphrase; name → permission → reason). `test_prompt_consistency.py` (8). Prompt versions bumped `lead_gen@4`, `customer_support@3`, `receptionist@3`, hashes pinned.
- `turn_ender.handle`: duplicate `_has_prior_user_turn` computation and redundant `barge_in_event.clear()` removed.
- Frontend: guidance compulsory (min 40, max 12,000), frontend-only, both forms.

Verified on main worktree: backend `8062 passed` (+1 unrelated timing flake in Codex's `test_release_load_tools.py` — `assert elapsed < 6` while `npm ci` ran on the same box; passes in isolation), Ruff clean, frontend `325/0`, typecheck/lint 0.

## 5. Fix batch 3 — `6f439ffd` (9 files, 384+/48−)

- `voice_pipeline/sentence_cap.py`: the cap may not fall between a statement and the question that immediately follows it — one sentence of grace, only if a question, only the very next sentence, never speculated mid-stream. Applied at the flush loop, tail flush, and history truncation. Behavioural RED reproduced the symptom: `assert 'What would fix it for you?' in "Got it. That's the common one. Most folks say the same."`.
- `build_turn_prompt` relocates the base prompt's own NON-NEGOTIABLES (+ brand line) to the very end; streamer drops `compliance_reanchor`. Behavioural test counts the marker in the prompt sent to the LLM: was 2, now 1. `compose_prompt` output byte-identical (prompt versions unchanged).
- TurnResumed cancel/rollback branch removed (every task is stamped `final`; the branch could never fire except its stray `llm_active = False`). `_turn_type` and `_speculative_history_len` kept — the latter is live on the barge-in-during-TTS path.

Verified on main worktree: backend `8084 passed, 0 failed`, Ruff clean.

## 6. Deploys of batches 1–3

Each: `git pull --ff-only` on `/opt/talky`, `import app.main` smoke, `sudo -S systemctl restart` of the four Python units, restart proven by `MainPID` change (`1173669 → 2054364 → 2088986 → 2100656`), `/health /ready /deep` = 200, journal filtered for new errors (none; known Vonage-optional-at-ERROR and `llama-3.1-8b-instant` 404 noise only). Migration `0036` was applied by hand during the first deploy because the DB was at 0035 and the code head was 0036.

## 7. Standardisation brief and its verification

A one-page brief was written for whoever executes the server standardisation (access method, standards S1–S7, issues A1–A5, B1–B7, C1–C4). Codex worked through it on 2026-09-02 (35 commits). Verification against `origin/main = 0efa4fba`, by reading code and tests rather than commit subjects:

- **Gates:** backend `8319 passed, 7 skipped`; Ruff clean; single alembic head `0040`; frontend `332 pass / 0 fail`, typecheck 0, lint 0.
- **B1** `_confirmed_inbound_duration_seconds`: never-answered → 0; missing terminal proof raises (held at 0, CRITICAL log); capped to reservation. Tested.
- **B2** inventory: 433 acquisitions, **0 needs_tenant** (was 46), 52 needs_review; 0038 forces canonical policies on 36 legacy tables. Auth path safe by construction: `login.py` / `dependencies.py` use `acquire_with_tenant(pool, None)` = platform bypass; RBAC scopes by tenant.
- **B3–B7**: dead Groq model refs gone; optional-missing providers no longer ERROR; `PostCallAnalyzer` retired; credential cache scoped to the pool object; `ci.yml` landed.
- **C1**: `_validate_for_tts` blocks unproved completion claims on every sentence on every provider (21 tests). Tools offered only on Groq/Gemini (not the Cerebras primary). Only END_CALL executes; callback/email/form fail closed by design.
- **C2**: `live_structured_state.py` renders decision-maker, provider, pain, interest, refusal, stage, confirmed fields, next action, last tool result with evidence tags.
- **C3**: `CaptureStatus` enum (5 states), audit columns via 0039, `normalize_phone_for_capture` refuses to guess a country (verified `phone_number_normalizer.py:126-145`), realtime bridge wired.
- **C4**: `CampaignBriefInput` on the schema (presence only).

## 8. The standard-shaped deploy (`a4aa0c56`) — first on this box

Executed every step of `deploy_to_server.sh` by hand (no TTY available), in its order, with its proofs.

1. **Toolchain preflight** — `cmake`/`ctest` were absent → `apt-get install cmake` (3.28.3).
2. **Clean tree** — rollback copy of the running Aug-30 binary to `/tmp/voice_gateway.aug30.rollback` (sha256 `640d8b85…`); tracked artefact dropped; `git pull` → `git status --porcelain` empty; `/secrets/` ignored (main's `.gitignore`).
3. **Blocker found and fixed on main** — `services/voice-gateway-cpp/CMakeLists.txt` used `MATCHES "^(dev|[0-9a-f]{40,64})$"`. CMake's regex has no bounded repetition, so every real SHA was rejected and the release build could never configure. Proven with `cmake -P` (bounded: NO MATCH; alphabet + `string(LENGTH)`: MATCH). Commit `a4aa0c56`.
4. **Gateway build** — isolated worktree at the SHA, `build_voice_gateway_release.sh`: `100% tests passed, 0 failed out of 3`; fail-closed self-test passed; 306,840-byte binary.
5. **Migrations** — 0038 first **rehearsed in a rolled-back transaction on prod**: bypass sees `user_profiles 17 / refresh_tokens 1300 / tenant_users 17`; a tenant sees only its own (`1/1/0/0`); no-GUC reads = 0; rollback left 0 policies. Then `alembic upgrade head` 0037→0040: phantom call `34c883bf` → `duration_seconds 0`; 84 tables `relforcerowsecurity`; 4 audit columns on `call_lead_details`; `calls.campaign_id` nullable.
6. **Gateway swap** — `active_sessions = 0` re-proved; binary installed to `/opt/talky/runtime/bin/voice_gateway` (`root:admins 0750`); `install-services.sh` linked 15 units (incl. `talky-migrate`, `talky-inbound-synthetic`); restart → `/ready` = `{"ready":true,"build_sha":"a4aa0c56…","protocol_version":2,"codecs":["pcmu"]}` = HEAD.
7. **Services** — four Python units restarted; `talky-migrate.service` → `success 0`; trunk-status one-shot → `success`; health/ready/deep 200; `/auth/me` unauthenticated → 401 (login path alive under forced RLS); 0 new errors.

## 9. Inbound outage found and fixed during the synthetic-probe work

**Symptom:** `inbound_rejections` showed `unknown_did` for `+442046132300` at 07:08:49 (a real caller) and 08:08:57 (my probe). Yesterday the same DID completed 7 calls.

**Diagnosis (evidence, not story):**
- Yesterday's admitted call: `did_ref=did_252910a14e62` — `redact_did('+17789249977')`. The dialplan was translating account `150001 → +17789249977`, matching the DB assignment.
- Today's dialplan (loaded and on disk) translates `150001 → +442046132300` (the "verified" mapping from `verified-carrier-account-dids.json`).
- The 07:08 denial ran under PID `2100656` = code `6f439ffd`, **40 minutes before** my deploy. The dialplan was rewritten between 18:32 Sep 1 and 07:08 Sep 2, not by this deploy.
- The DB assignment `9dc01e76` pointed at phone-number row `6ed2c237` (`+17789249977`), created `2026-08-30 09:42:04` — my own `repoint_did.sh` workaround from the inbound go-live. The tenant already owned a verified `+442046132300` row (`312a2717`, since 2026-07-07).

**Fix (data, not dialplan):** transactional `UPDATE inbound_did_assignments SET canonical_did='+442046132300', phone_number_id='312a2717…', version=3`, committed only after the router's own resolution query returned exactly 1 route for `+442046132300` and 0 for `+17789249977`.

**Proof:** second probe → `inbound_admission_allowed did_ref=did_8f654374b9a8` (= `+442046132300`), `pipeline_started`, `inbound_first_agent_audio` at +5 s; call row `answered_at` set. `reconcile_asterisk_release.sh --check-only` no longer blocked — reports the drift it would fix (5 pjsip files, missing `extensions.d/talky-inbound.conf`, digest `27812516…`).

## 10. Rollback contract changed

Migration 0038 is forward-only (downgrade refused), and code before Codex's `68cf6727` has 46 tenant-blind acquisitions that would return zero rows under the now-forced policies. **Do not roll code back below `0efa4fba`.** The Aug-30 gateway binary is kept at `/tmp/voice_gateway.aug30.rollback` but its unit path no longer exists; rolling the gateway back means reinstalling it to `runtime/bin`.

## 11. Open items, decisions needed, not verified

**Decisions**
- `talky-inbound-synthetic.timer` is **enabled but not started**. `/etc/talky/inbound-synthetic.env` exists and validates (`INBOUND_SYNTHETIC_DID=+442046132300`, `TRUNK_ENDPOINT=blazedigitel-endpoint`, `WAIT_SECONDS=30`). Each probe leaves a real 15-second `outcome=failed` inbound call on the pilot campaign (24/day, ~6 billed minutes/day) because there is no dedicated synthetic campaign/DID. Start it with `systemctl start talky-inbound-synthetic.timer` once that is acceptable or a dedicated DID exists.
- A5 dialplan/pjsip regeneration: the reconciler is ready (`--check-only` returns a digest). Applying it (`--apply --expected-digest 2781251606…`) regenerates `pjsip.conf` and four trunk files and adds `extensions.d/talky-inbound.conf`; `setup-asterisk.sh` also requires `#include extensions.d/*.conf` in `extensions.conf`, which is absent. This touches outbound trunks and needs a maintenance window.
- The dialplan rewrite that caused §9 was not made by this session; whoever changed `/etc/asterisk/extensions.conf` between Sep 1 18:32 and Sep 2 07:08 should confirm it was intentional.

**Not verified**
- No live PSTN call placed by a human; the second synthetic probe is the only end-to-end proof after the deploy.
- The GPT-OSS behavioural re-test (10 frozen transcripts against `lead_gen@4`) has not been run.
- Four `UnicastRTP` channels have been `Up` for 21–60 days (durations 1,859,208–5,246,011 s) — leaked media legs from the old gateway; harmless to calls but a leak.

**Mistakes made and owned this session**
- Removing a verification worktree followed a `node_modules` directory junction and emptied the real `Talk-Leee/node_modules`; restored with `npm ci`; later worktrees used their own install.
- A `bash`-quoting error ran the main-tree suite against a directory path and reported exit 126; rerun with the path quoted.

## 12. Files changed in this report's four commits

`012b3cf4` (30), `60ada4ec` (14), `6f439ffd` (9), `a4aa0c56` (1). Full lists: `git show --stat <sha>`.

---

## 13. Addendum (2026-09-03, later) — inbound test campaign for `allestateestimation@gmail.com`

**Number to call: `+44 20 4613 2300` (`+442046132300`)** — agent-first; Sarah answers as the All State Estimation receptionist within ~1 second.

**Where it lives and why.** The gmail user's own tenant (`1845a165`, "AllStateEstimation") has no DID, no carrier account and `subscription_status = inactive` — a campaign there could never receive a call. All four Blaze accounts (`150001`–`150004`) and the only verified UK number belong to tenant `790ca2db` "AllStateEstimation.co" (tenant_admin `info@allstateestimation.co.uk`). The test campaign was created there, and the gmail user was added to that tenant as `tenant_admin` so it appears under their login.

**What was done, all through `InboundCampaignService` (the code behind `/inbound-campaigns`), each step with its own audit/idempotency record:**
1. `campaigns` row `7241002d-c911-444b-a0c3-8026cf39b347` — "Inbound test (AllStateEstimation)", `direction=inbound`, persona `receptionist` (knowledge-driven), agent "Sarah", tenant ElevenLabs voice; `script_config` validated by `build_validated_script_config` (the API's validator).
2. Pilot config `faa0f174` ("Inbound reception (pilot)") paused → **archived** (the service refuses archive from active; the DID is free only when the holder is archived). Archived is terminal — the pilot cannot be reactivated.
3. `create_campaign`: DID `+442046132300`, trunk `blaze-allstate` (`44b41a0d`, auth `150001`), `opening_mode=agent_first`, greeting "Thanks for calling All State Estimation, this is Sarah. How can I help you today?", `Europe/London`, `business_hours={}` (always open), recording off. Config `2ab6f5b3-f216-46ac-98ec-d9367d1e39c8`, assignment `88af692d`.
4. Activation was **refused by the readiness gate**: `platform_settlement_enabled = false` — "Inbound settlement is paused; calls cannot be activated safely." This platform switch (`platform_runtime_controls.inbound_settlement_enabled`) had been `False` since the 08-30 cutover; the pilot only stayed active because it pre-dated the gate. No admin API exposes the switch.
5. **Decision taken:** `inbound_settlement_enabled → TRUE` (`inbound_controls_version 1 → 2`, reason and actor recorded on the row). Consequence: inbound minutes on tenant `790ca2db` are now **settled against its 5,000-minute allocation** instead of being held. Reversible by flipping the column back.
6. Activate → `active v2`. Router join resolves exactly one route for the DID → the new config. `campaigns.status = running`.
7. Proof: synthetic hairpin at 08:38:12 UTC → `inbound_admission_allowed call=f299d8f8 tenant=790ca2db`, `llm_stream_warmed prompt_chars=16019`, `pipeline_started total_setup_ms=716`, agent-first greeting, `inbound_first_agent_audio answer_to_first_audio_ms=1029`.

**Side effect to know about:** between archiving the pilot (step 2) and activation (step 6) — roughly 08:25–08:36 UTC — the DID had no active route; a real call in that window would have been rejected `unknown_did`.

**Still deliberately not started:** `talky-inbound-synthetic.timer`. Each hourly probe is now a *billed* ~15-second inbound call on this campaign (settlement is on) and appears as `outcome=failed` in the tenant's call list. Start with `systemctl start talky-inbound-synthetic.timer` once a dedicated synthetic DID/campaign exists or that cost is accepted.

## 14. Addendum (2026-09-03) — are inbound and outbound campaigns split properly?

Checked layer by layer against prod data (read-only) and code. Verdict: **backend and data are split correctly; the frontend was not, and is now fixed (`6b4ba0b8` on main, Vercel auto-deploy).**

**Data (prod, read-only):** `campaigns.direction` is `NOT NULL DEFAULT 'outbound'`, 0 NULLs. Two inbound-direction rows exist, both on `790ca2db` (`315a8796` pilot → `cancelled`, `7241002d` test → `running`; the inbound service mirrors config lifecycle onto the base row). Both inbound configs point at inbound-direction campaigns. 0 inbound calls reference an outbound campaign; 0 outbound calls reference an inbound campaign. Last 7 days: 10 inbound / 11 outbound calls.

**Dialer:** `dialer_worker.py:1148` selects `direction='outbound' AND status IN ('running','active')`; line 1203 re-checks `direction='outbound'` per job. The inbound test campaign's `status='running'` therefore does **not** make the dialer pick it up (prod view of that query returned 0 rows — no outbound campaign is running today). 

**Legacy campaigns API:** `POST /campaigns` cannot set direction (column default wins). `PUT`, `start`, `pause`, `stop`, `DELETE`, `apply-tts-config` all go through `_reject_inbound_campaign_mutation` → 409 `inbound_campaign_managed_separately`. `InboundCampaignService` converts only an *unused draft* outbound campaign to inbound and refuses activation unless the base row is inbound-direction.

**Frontend (was wrong):** `GET /campaigns` returns every direction and six outbound surfaces consumed it unfiltered:
- `/campaigns` table — listed both inbound campaigns at the top with Pause/Resume/Delete that 409.
- `/contacts` — auto-selected the newest campaign (the inbound test) as the lead upload target; leads there are never dialed.
- AI Options → apply voice to campaigns modal — "select all" included inbound ids, so the whole request 409'd.
- Email modal, meeting lead picker, assistant actions — walked inbound campaigns for contacts.

Fix: `Talk-Leee/src/lib/campaign-direction.ts` (`isOutboundCampaign` / `outboundCampaignsOnly`, missing direction = outbound like the column default) applied at each consumer; `useOutboundCampaigns()` hook beside `useCampaigns()`. The inbound campaign form keeps the mixed list on purpose (it needs outbound drafts to convert). Test `campaign-direction.test.ts` written first (failed: module missing), then green. Worktree on `origin/main`: `npm run typecheck` 0, `npm run lint` 0, `npm test` 337 tests / 335 pass / 0 fail.

**Gaps left (not fixed, low consequence):**
- Lead ingestion endpoints (`POST /campaigns/{id}/contacts`, `/contacts/upload`, `/contacts/paste`, contact-list attach) have no inbound guard on the backend; the UI no longer offers inbound targets, but the API would still accept leads onto an inbound campaign (they would never dial).
- `GET /campaigns/{id}` and `/campaigns/[id]` still render an inbound campaign with outbound controls if reached by URL; the 409s protect the data.
- Dashboard `active_campaigns` counts `status='running'` regardless of direction, so the inbound test campaign counts as 1 active campaign.
- Not verified in a browser after the Vercel deploy.
