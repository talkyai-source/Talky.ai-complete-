# TALKY.AI — Complete Session Record
**2026-07-20 → 2026-07-21** · Server `144.76.17.150` (Hetzner, systemd) · Frontend: Vercel (auto-deploy from `main`)

---

## 0. Executive summary

Five production deploys, all verified live. Prod HEAD moved:

```
4ca1595b → 6f768f2b → 9025332 → 54e56df2 → fe7f1e2f → 4693bf13 → 863f3032
 (start)   (batch 1)  (batch 2)  (jsonb fix) (delete fix) (ops set)  (24-fix wave)
```

| Deploy | Content | Verified |
|---|---|---|
| `6f768f2b` | Batch 1 — voice correctness + listening-path F-13/14/15/11b | health 200, 0 errors |
| `9025332` | Batch 2 — 8 listening-path increments | health 200, 0 errors, gate 3306 |
| `54e56df2` | jsonb codec hotfix ("Add contact" broken) | health 200, gate 3306 |
| `fe7f1e2f` | Campaign Delete button actually deletes | health 200, tsc clean |
| `4693bf13` | Dialer liveness (heartbeat + systemd watchdog) | SIGKILL recovery proven live |
| `863f3032` | 24-fix doc wave (6 commits, 6 workers) | 3/3 workers healthy, gate 3547 |

**Test gate progression:** 3273 → 3306 → 3325 → 3538 → **3547 passed, 0 collection errors**
(the passkeys collection error that blocked every full run since before this session is now fixed).

---

## 1. Batch 1 — voice correctness (`6f768f2b`)

Pushed + deployed at session start. Contents: recording chain + RLS context, KB freshness/cache, wrong-number determinism (Cases 1–4), proposal atomicity, listening-path hardening (F-13/F-14/F-15/F-11b).

**Verification log:**
```
=== HEAD after ===
6f768f2 fix(voice): correctness batch — recording chain, KB freshness, wrong-number determinism
=== import smoke ===
IMPORT_OK
RESTART_ISSUED
active / active / active / active
healthz/deep: HTTP 200
=== errors since restart === -- No entries --
```

**⚠️ STILL OUTSTANDING:** migration `20260717_calls_lead_id_nullable.sql` was **blocked by the Claude auto-mode safety classifier** (remote DB write). Code is backward-compatible (recording stub insert fails gracefully on the old schema), so this is a deferred benefit (non-dialer recordings won't list), not a blocker.

Apply manually when convenient:
```bash
cd /opt/talky/backend && psql "$(venv/bin/python -c 'from app.core.config import get_settings;print(get_settings().database_url.replace("+asyncpg",""))')" -f database/migrations/20260717_calls_lead_id_nullable.sql
```
Related drift: `app/infrastructure/storage/models.py:75` still declares `lead_id ... nullable=False`.

---

## 2. Batch 2 — listening-path audit, 16 open findings (`9025332`)

### 2.1 Design phase — 3 parallel design workers
Produced implementation-ready, risk-aware designs for the 16 open findings from the 20-finding listening-path audit.

**Cross-cutting discoveries:**
- **CONVERGED BUG (both STT workers found it independently):** `ResilientSTTProvider` never forwarded `mute`/`unmute` → **STT echo-suppression during TTS was silently skipped in production** whenever `STT_FAILOVER_ENABLED` is on (it is). Two independent analyses landing on the same defect = high confidence.
- **Corrected a wrong design assumption via SDK introspection on prod:**
```
deepgram-sdk version: 5.3.0
classes in socket_client: ['AsyncV1SocketClient', 'V1SocketClient', ...]
AsyncV1SocketClient | keepalive: [] | send: ['send_control', 'send_media']
ControlMessage schema: {'type': "Literal['Finalize','CloseStream','KeepAlive']"}
```
  → the designed `send_keep_alive()` **does not exist** in SDK 5.3.0. Correct call is
  `await conn.send_control(ListenV1ControlMessage(type="KeepAlive"))`. Saved a broken implementation in the (still-unbuilt) increment #9.

### 2.2 Implementation — 8 increments / 6 commits

| Commit | Fix | What it does |
|---|---|---|
| `0ac86be1` | **F-17** | Silence-monitor `TypeError` crash. Two authorities disagreed on backchannels — `turn_ender` imported the set that classifies "no"/"nope"/"nah" as backchannels (suppressing them), contradicting `voice_pipeline.backchannel`. Unified. Also `datetime.utcnow` (naive) minus an aware timestamp raised `TypeError` inside `_silence_monitor`'s un-wrapped loop → **the monitor task died silently: no more nudges, no 60s auto-hangup, for the rest of the call.** Converted to `time.monotonic` throughout + per-tick exception boundary. |
| `e63f6233` | **F-04(b)(c)(e) + mute** | **F-04(c) LIVE-HIGH:** `resilient_stt` re-decides the provider fresh per stream instead of inheriting a sticky `_active` — one transient hiccup used to pin an Ask-AI singleton to the fallback engine *for process lifetime*. Breaker health stays shared (lazy OPEN→HALF_OPEN fast-fail already covers deprioritisation). **mute-forwarding** (the converged bug). **F-04(b)** capture_mode probes `_active` before the orphaned `_primary`. **F-04(e)** `_CONFIGURE_UNSET` sentinel so an explicit `None` means "disable eager EOT" rather than being swallowed. |
| `5562806c` | **F-09** | A suppressed backchannel's *always-emitted* empty EOT marker satisfied `detect_turn_end` and cut TTS mid-sentence for a mere "yeah" → caller heard a chopped sentence then silence. New per-call `_utterance_seq` correlates the suppression with its own marker. Worker caught a flaw in my design pseudocode (the grow-case clear would fire on the empty marker itself and erase the mark) and gated it correctly. |
| `a23d2d14` | **F-08** | A distinct 2nd caller utterance during turn 1's "thinking" phase armed the barge-in event (silencing turn 1's TTS before it spoke) *and* was collapsed as a duplicate (turn 2 never dispatched) → **both turns silent.** Now: arm the stop-event only when `tts_active`; distinguish duplicate vs distinct by seq **and** content; queue depth-1 and dispatch on slot release. The 0.25s cancel-detach was deliberately untouched. |
| `332579cf` | **F-10** | Instant-opener echo immunity protected the wrong mechanism (observed live: "agent permanently silent"). Now content-aware: a bare-greeting echo within the in-flight/grace window is ignored; "stop"/real content still cancels. `CancelledError` re-raised, done-flag rolled back. **My review caught an ordering bug** — the seq bump ran before the echo return, so an ignored echo still advanced the counter and could make F-08 queue a phantom turn; fixed + regression-tested. |
| `90253329` | **F-05(ii)** | Confidence was read *live* inside the detached turn-end task, so a later transcript could overwrite it before the turn-0 floor evaluated. Captured at dispatch (like `user_text`) and threaded through with a sentinel (None is a legitimate Flux value). |

**Deploy log:**
```
=== HEAD ===  9025332
IMPORT_OK
RESTARTED / active / active / active / active
healthz/deep: HTTP 200
voice-worker: Voice Pipeline Worker started - listening on voice:calls:active
groq_warmup_ok model=llama-3.1-8b-instant warmup_ms=189
```

**Not built (designed only):** #9 F-01/F-06 STT connection rewrite, #10 F-07 concurrency guard, #11 F-02/F-04(a,d)/F-05(i) Nova parity.

---

## 3. Production bug: "Add contact" broken (`54e56df2`)

**Symptom reported:** `Failed to create contact: invalid input for query argument $8: {} (expected str, got dict)`

**Diagnosis:** `lead_data`'s 8th field is `custom_fields` (a dict). The DB adapter *deliberately* passes dicts straight through for jsonb columns, trusting a registered codec to encode them:
```python
is_jsonb_target = udt_name in {"json", "jsonb"}
if isinstance(value, dict):
    if is_jsonb_target:
        return value          # ← relies on the pool's jsonb codec
    return json.dumps(value, default=str)
```
But the pool registers that codec via `init=_register_jsonb_codecs`, while `postgres_adapter._execute_async` opens **raw `asyncpg.connect()`** connections (2 sites, lines 224 & 822) that never did. So every jsonb write through the adapter failed — not just contacts.

**Fix:** register the same codec on both ad-hoc connect sites. `FakeConn` test double gained `set_type_codec`; regression test asserts the codec is registered on the insert path.

**Deploy:** gate 3306 passed → prod `54e56df` → health 200.
*(Note: first curl after restart returned `000` — pre-bind race, not a failure. Polling gave 200 on attempt 1.)*

---

## 4. Production bug: Campaign Delete button (`fe7f1e2f`)

**Diagnosis — the button never deleted anything.** Full chain traced:
```
Delete button → setConfirmDeleteId → confirm modal → onDelete → handleDelete
  → stop.mutateAsync → dashboardApi.stopCampaign(id) → POST /campaigns/{id}/stop
```
It then filtered the row out of the **local React-Query cache** and showed "Campaign deleted". So the campaign was merely *stopped* and *hidden in the browser* — it returned on refresh. And there was **no campaign-delete endpoint on the backend at all** (only contact-delete and knowledge-source-delete existed; `rbac.py:686` was just a docstring example).

**Fix (3 parts):**
1. New `DELETE /campaigns/{id}` — soft-delete (`status='deleted'`, preserving call/lead history like contact soft-delete), stops + clears the dialer queue first so a running campaign can't keep dialing.
2. `list_campaigns` now excludes `status='deleted'` (it didn't — soft-deleted rows would still have shown).
3. Frontend `deleteCampaign` + repointed handler (renamed the misleading `stop` mutation → `removeCampaign`) + error toast. Bulk-delete loops the same handler → fixed too.

**Verified:** `tsc --noEmit` clean (so the Vercel build would pass), gate 3306, prod `fe7f1e2` health 200.
*Route-probe caveat recorded: a global auth middleware 403s **all** unauth requests before method-matching (a bogus PATCH also returned 403), so route-existence can't be probed unauthenticated; registration is proven by the clean import + restart.*

---

## 5. The external "24-fix Backend Fix Document" — verification & drive

### 5.1 Phase 0 — refuting the audio premise with production evidence

The doc's headline claim (fixes 1–3): *"Cartesia is asked for 24kHz but the gateway resamples as 16kHz — mismatch causes distortion and buzz on every call."*

**14-day log sweep — every TTS format probe, deduplicated:**
```
=== EVERY TTS_FMT_DEBUG line, 14 days, dedup by provider/fmt/rate ===
    655 TTS_FMT_DEBUG provider=elevenlabs gateway_fmt=s16le req_rate=16000

=== gateway rate/resample warnings, 14 days ===
--- (empty = none) ---

=== media gateway init ===
TelephonyMediaGateway initialized: internal=16000Hz, wire=8000Hz PCMU, 16-bit, tts_source_format=s16le
TelephonyMediaGateway: session started wire=pcmu/8000Hz internal=linear16/16000Hz (upsample on ingress, downsample on egress)
```

**Static pins on the deployed build:**
```
telephony pins: [('stt_sample_rate','16000'), ('tts_sample_rate','16000'),
                 ('gateway_sample_rate','16000'), ('gateway_input_sample_rate','16000')]
twilio pins: ['8000','8000','8000','8000']
```

**Verdict: 655/655 probes uniform, zero mismatches, zero warnings, gateway self-reports the designed topology.** The claimed mismatch does not exist in production. The buzz it describes was already root-caused and fixed earlier (`6dbfb32e` µ-law egress hygiene; `4d9695e3` async-DB migration removing playback underruns). **Fixes 1–3 deliberately NOT applied** — changing rates would be pure risk with no benefit.

Also verified: `soxr OK: 1.1.0` (fix 6 was already done), prod Python 3.12 (⇒ `audioop` unusable — removed in 3.13).

### 5.2 Fixes 4/5 — REOPENED by evidence, then fixed properly

My first assessment ("superseded by F-10") was **too broad**. The 07-20 test calls proved a live bug:
```
outbound_greeting_presynth call_id=eb97b829 text='Hi, James here from Allstate Estimation...'
presynth_greeting_barge_in_post_send call_id=eb97b829
outbound_greeting_presynth_done elapsed_ms=1107 interrupted=True   ← call 1
outbound_greeting_presynth_done elapsed_ms=906  interrupted=True   ← call 2
```
Both calls, greeting cut ~1s in — pickup-"Hello?" timing. **F-10 protected the caller-first instant-opener path; these calls used the agent-first pre-synth path, which F-10 never covered.**

Root cause proven by the worker (file:line): the fast path sets `tts_active=True`, so the echo's StartOfTurn armed the shared event via `_on_barge_in_direct` → the fast path's `barge_in_event.is_set()` check fired → `interrupted=True`.

**Fix:** `agent_first` opens the same content-aware echo window (reusing F-10's flags, so both existing gates cover it with zero changes to them). The doc's proposed **blind 3-second grace was rejected** — it would suppress *legitimate* interruptions in the first 3 seconds, the exact failure class F-17 had just eliminated.

### 5.3 The 6-worker wave (`863f3032`)

Six workers, disjoint file lanes, **goal text locked verbatim** with a mandatory proforma (goal-verbatim → done → evidence → deviations → files → residual risk → delegation attestation) to prevent goal drift down the hierarchy.

| Worker | Model | Doc fixes | Outcome |
|---|---|---|---|
| **A** | Opus | 4, 5 | Agent-first echo gate (above). 4 new tests. |
| **B** | Sonnet | 8 | voice+reminder liveness, healthwatch timer, log redaction |
| **C** | Sonnet | 16 | domain→API circular import eliminated |
| **D** | Sonnet | 11, 12 | tenant defense-in-depth |
| **E** | Sonnet | 10, 20 | prod-gate Stripe checks + subscription cache |
| **F** | Sonnet | 19 | verified already-done |
| *(+ me)* | — | 21, gate | passkeys fix, cleanup-worker audit |

**Worker B — fix 8:** found the *same latent bug* as the dialer — `voice_worker._heartbeat()` existed but was **never scheduled**. Reminder worker had no Redis at all. Both now: Redis heartbeat + sd_notify watchdog + `sys.exit(1)`; units → `Type=notify`/`WatchdogSec=180`/`Restart=always`; `/healthz/workers` covers all 3; new `talky-healthwatch.timer` (2 min) logs `HEALTHWATCH CRITICAL` at journald priority `err`; `log_redact.py` stops the Redis **password** being printed to journald by `queue_service` + `session_manager`.
*The doc's proposed Prometheus alert (`redis_string_value{...}`) was **unimplementable** — no redis-exporter exists on the box; it would have been a permanently dead alert.*

**Worker C — fix 16:** all 28 `_bridge()` sites inventoried and categorized — 19 live-adapter (→ new `adapter_registry`, registered by the API layer via a closure that tracks reassignment), 4 config constants (→ `telephony/config.py`), 0 state-dict (already migrated). Plus a permanent AST architecture test walking `app/domain` for `app.api` imports, with an honest allowlist for two pre-existing residuals. It also fixed a latent test that had been passing by accident.

**Worker D — fixes 11/12:** `Campaign.tenant_id` added as **Optional** with documented IDOR reasoning (never constructed from client input — all sites traced; a required field invites "just pass tenant_id in the JSON"). `campaign_service` gained `_resolve_tenant_id` (explicit param → RLS contextvar fallback) + explicit filters on ~8 queries as defense-in-depth *alongside* RLS. Extended `test_idor_tenant_scoping.py` (26 tests). Also fixed a pre-existing fixture leak that never cleared the tenant contextvar, poisoning later tests.
*The doc's verification command referenced `tests/integration/test_idor_tenant_scoping.py`, which does not exist.*

**Worker E — fixes 10/20:** prod_gate now raises `STRIPE_LIVE_KEY` (key set but not `sk_live_`) and `STRIPE_SDK_MISSING` (key set but SDK absent — billing would silently run mock while the operator believes it's live). `call_guard._check_subscription` cached 60s in Redis (full outcome as JSON; **any Redis error falls through to the DB, never to "allowed"**). **DNC caching explicitly REJECTED** — a freshly-added DNC number must never be dialable from a stale cache; documented in-code.

**Worker F — fix 19:** the doc pointed at a **stale root `ci.yml` that GitHub Actions never reads**. The real workflow (`.github/workflows/ci.yml`) was already flipped to blocking on 2026-07-16 (`c4915ac6`), and both scanners run green:
```
pip-audit -r requirements.txt --ignore-vuln GHSA-f4xh-w4cj-qxq8
No known vulnerabilities found, 1 ignored          (exit 0)

bandit -r app/ -lll -x app/tests
No issues identified.
Total issues (by severity): Low: 214  Medium: 60  High: 0    (exit 0)
```

**Fix 21 — cleanup worker** (K3-authored, audited + bug-fixed by me): dry-run **default ON**, hardcoded table allowlist (`calls` deliberately absent), advisory lock, batched deletes with cap + throttle, `SET LOCAL app.bypass_rls` per transaction (PgBouncer-safe). **My audit caught a real bug:** `_count_expired` lacked the RLS bypass → RLS-enabled `stream_events` would count 0 and be **silently skipped every night**.

**Passkeys gate fix (me):** the test imported the pre-2.0 `PUBKEY_CRED_PARAMS`; the module had migrated to `SUPPORTED_PUB_KEY_ALGS` (COSE enums). This collection error **aborted every full-suite run** unless `--continue-on-collection-errors` was passed. Deploy gates are now clean end-to-end.

**Fix 14 — realtime fallback:** a dropped OpenAI Realtime websocket used to kill the call into dead air. Now `realtime_bridge` distinguishes rt-death-while-call-active from cancel / clean stop / model-error, fires `on_connection_lost` exactly once, and lifecycle swaps in a cascaded pipeline **reusing the live 8 kHz media gateway** (the worker caught that a naive 16 kHz assumption would produce ghost audio). Once per call, env-gated `REALTIME_FALLBACK_ENABLED` (default true). A superseded-task guard in `_pipeline_done_cb` proves no double-teardown; normal call-end paths unchanged.
*This worker also **corrected** the previous (quota-killed) worker's claims: the docstring it reported adding didn't exist, and the constructor param it added was dropped, never stored.*

### 5.4 ⚠️ Incident: shared-working-tree `git stash`

Workers **C and D each ran `git stash`** on the shared working tree, sweeping other workers' uncommitted edits. Casualties: Worker A's `agent_first.py` edit and Worker E's `prod_gate.py` edit were reverted to HEAD; K3's passkeys fix died with its quota. Worker B was hit too (a `git reset` + stash) and recovered surgically.

**Recovery was possible only because I keep audited diffs in context** — I reapplied A's change verbatim and verified every worker's files afterwards:
```
=== A restored: echo tests ===        4 passed
=== E present ===                     STRIPE_LIVE_KEY:1  guard:subscription:2  → 13 passed
=== my passkeys fix present ===       3
=== my cleanup RLS-count fix ===      3
=== C present ===                     adapter_registry.py exists, _bridge() gone
```
**Rule now in every worker brief: no `git stash` in the shared tree.** This is exactly why every diff is audited at landing rather than trusting proformas.

---

## 6. Deploy logs — the wave (`863f3032`)

**Final gate (no bypass flag — the first genuinely clean full run):**
```
1 failed, 3547 passed, 6 skipped, 6 warnings in 152.22s
FAILED tests/unit/test_resilient_providers.py::test_stt_ask_ai_singleton_respects_recovery_window
```
The single failure is a **timing-sensitive test** (50 ms recovery window) flaking under full-suite CPU load. Proven flaky, not a regression:
```
1 passed / 1 passed / 1 passed / 1 passed / 1 passed     (5× isolated reruns)
17 passed                                                 (whole file)
```

**Six commits:**
```
863f3032 fix(tests)+feat(ops): passkeys py_webauthn-2.x import + retention cleanup worker (fix 21)
84ea52ef feat(ops): prod-gate Stripe checks + call-guard subscription cache (fixes 10/20)
20a0f101 feat(security): tenant defense-in-depth on campaign model + service queries (fixes 11/12)
5e40410c refactor(telephony)+feat(voice): adapter registry + realtime→cascaded fallback (fixes 16/14)
fe545e08 feat(ops): voice+reminder liveness, healthwatch timer, Redis-URL log redaction (fix 8)
33fee92c fix(voice): F-10 greeting-echo immunity → agent-first presynth path (fixes 4/5)
```

**systemd transition (voice + reminder → `Type=notify`) — the delicate step:**
```
UNITS_INSTALLED
active / active
Type=notify Restart=always WatchdogUSec=3min      ← voice
Type=notify Restart=always WatchdogUSec=3min      ← reminder

=== READY handshake (Started must appear AFTER init) ===
07:53:54 systemd[1]: Starting talky-reminder-worker.service...
07:53:55 talky-reminder: PostgreSQL primary pool initialized min=5 max=20
07:53:56 talky-reminder: Reminder Worker initialized successfully
07:53:56 systemd[1]: Started talky-reminder-worker.service          ← handshake OK
07:53:56 talky-reminder: heartbeat: reminders_sent=0, emails_sent=0
```

**Timers enabled:**
```
NEXT                        LEFT      LAST                  UNIT
Tue 2026-07-21 07:56:25 UTC 1min 51s  07:54:25 UTC (8s ago) talky-healthwatch.timer
Wed 2026-07-22 03:00:00 UTC 19h       -                     talky-cleanup.timer
```

**Worker liveness probe — 3/3 healthy:**
```json
{"healthy":true,"workers":[
 {"name":"dialer","last_beat_epoch":1784620464.51,"age_seconds":53.57,"healthy":true},
 {"name":"voice","last_beat_epoch":1784620494.81,"age_seconds":23.28,"healthy":true},
 {"name":"reminder","last_beat_epoch":1784620496.04,"age_seconds":22.05,"healthy":true}]}
```

**Healthwatch first run:**
```
talky-healthwatch[78710]: healthwatch: all workers healthy
systemd[1]: Finished talky-healthwatch.service - Talky.ai Worker Health Watch (heartbeat probe).
```

**Cleanup worker — manual dry-run:**
```
cleanup_start dry_run=True batch_size=5000 max_batches=200 sleep_ms=100
              targets={'call_events':'90d','call_legs':'90d','stream_events':'90d'}
cleanup table=call_events   window=90d expired=0 — nothing to do
cleanup table=call_legs     window=90d expired=0 — nothing to do
cleanup table=stream_events window=90d expired=0 — nothing to do
cleanup_complete dry_run=True elapsed_s=0.2 summary={'call_events':0,'call_legs':0,'stream_events':0}
```

**Final state:** deep health 200, all 4 services active, `-p err` since deploy → **no entries**.

---

## 7. Earlier deploy: dialer liveness (`4693bf13`)

**The find that justified the whole exercise:** `DialerWorker._heartbeat()` was **defined but never scheduled** — the dialer has had *no heartbeat at all*, which is precisely how it once "ran dead for days" unnoticed. The doc assumed the loop ran and merely lacked a Redis write.

Built three layers instead of one:
1. Redis `SETEX dialer:heartbeat_ts` (Redis outage logs + continues, never kills the loop)
2. **systemd watchdog** via a new dependency-free `sd_notify.py` (`Type=notify` + `WatchdogSec=180`), petted from the *same event loop* as the dequeue loop → a **hung** worker is restarted too, not just a dead one
3. `sys.exit(1)` on fatal errors + `Restart=always` (+ crash-loop cap in `[Unit]`)

Also: `/healthz/workers` endpoint, and G.711 µ-law codecs vectorized with numpy LUTs — **proven byte-identical to the old scalar implementation across all 65,536 encode and 256 decode inputs** (exhaustive test, not sampled), so zero audio-behaviour risk. No `audioop` (removed in Python 3.13).

**SIGKILL acceptance test (the doc's own criterion) — passed live:**
```
pid before: 54432
7:47:53 systemd[1]: talky-dialer-worker.service: Failed with result 'signal'.
7:47:57 systemd[1]: Scheduled restart job, restart counter is at 1.
7:47:57 systemd[1]: Started talky-dialer-worker.service
state after 6s: active
pid after: 56193 (restarted: YES)
{"healthy":true,"workers":[{"name":"dialer","age_seconds":6.26,"healthy":true}]}
```

---

## 8. Production data confirmations (read-only)

**allstate tenant / trunk (for the planned Cartesia + Qwen test call):**
```
user_profiles: info@allstateconstructions.us  → 45022490-... (Llama 3.1 8b + Deepgram Aura)
               info@allstateestimation.co.uk  → 790ca2db-... ← the relevant tenant

tenant 790ca2db AI config:
  llm = groq / qwen/qwen3.6-27b          ← Qwen 3.6 27B already configured ✅
  tts = elevenlabs / eleven_flash_v2_5   ← NOT Cartesia (needs switching in AI Options)
  voice = JBFqnCBsd6RMkjVDRZzb, pipeline = cascaded

TRUNK 4efa508d | tenant 790ca2db | blaze-pool-150004 | dom=sip3.blazedigitel.com
      | dir=both | active=True | reg=registered
      metadata={"pool":true,"caller_id":"+442046132301","dtmf_mode":"rfc2833",...}
```
**Correction:** the given CID `+4420461323` was one digit short — the trunk's real caller-ID is **`+442046132301`**. Destination `+442046132300` exists as a lead in this tenant.

⚠️ Qwen 3.6 27B was **pulled once before** (2026-06-27) for dodging the AI-disclosure question and hallucinating in the weakness audit — watch its answers on any test call.

**Stripe state (verified today):**
```
prod_gate_passed — all production-mandatory checks ok      ← gate IS enforcing
Stripe SDK not installed. Billing features will use mock mode.
STRIPE_SECRET_KEY present in .env: 0                        ← no key set at all
```
So billing is **structurally** mock (SDK absent + no key), not merely misconfigured. Since no key is set, adding the SDK cannot block boot.

---

## 9. Strict checklist — the 24 fixes

Marked ✅ only at 100% verified.

| # | Fix | Status |
|---|---|---|
| 1 | Sample rates → 8k | ✅ **Verified consistent** — no mismatch exists (655/655 probes); correctly NOT applied |
| 2 | Fallback rate → 8k | ✅ same |
| 3 | Gateway default → 8k | ✅ same |
| 4 | Greeting grace | ✅ **Fixed properly** (content-aware, not the doc's blind 3s timer) |
| 5 | `_opening_grace_until` | ✅ same |
| 6 | soxr | ✅ already installed (1.1.0) |
| 7 | Dialer heartbeat | ✅ deployed (+ found it was never scheduled) |
| 8 | Worker-silent alert | ✅ `/healthz/workers` + healthwatch timer, all 3 workers |
| 9 | Stripe live key | ⏳ **awaiting your key** — plan ready |
| 10 | prod_gate Stripe check | ✅ deployed |
| 11 | tenant_id field | ✅ deployed (IDOR-safe) |
| 12 | Tenant filters | ✅ deployed |
| 13 | PgBouncer | ⛔ **blocked** — RLS audit required first (below) |
| 14 | Realtime fallback | ✅ deployed |
| 15 | Twilio state | ⛔ needs its own registry design (doc's approach can't work) |
| 16 | Circular import | ✅ deployed + architecture test locks it |
| 17 | systemd restart | ✅ deployed, SIGKILL-verified |
| 18 | `sys.exit(1)` | ✅ deployed |
| 19 | CI blocking | ✅ already done since 07-16 (doc pointed at a dead file) |
| 20 | Guard caching | ✅ subscription cached; **DNC rejected** (compliance) |
| 21 | Cleanup worker | ✅ deployed **in dry-run** — flip after you sign off windows |
| 22 | Split lifecycle.py | ⏳ sequenced after batch-2 #9–#11 |
| 23 | Split voice_orchestrator.py | ⏳ same (hard dependency) |
| 24 | Split call_guard.py | ⏳ same |

**19 ✅ · 1 awaiting your key · 2 blocked/redesign · 3 sequenced**

---

## 10. What remains

### Needs a decision from you
1. **Stripe key** (`sk_live_`) — everything else is automatable. Plan: pin SDK → local test-mode rehearsal → live webhook endpoint created via API (signing secret captured automatically) → live Products/Prices → one real checkout + refund to verify. `STRIPE_BILLING_DISABLED` is the kill-switch; prod_gate makes live-mode a boot invariant.
2. **Retention windows** — review one night of dry-run counts, then `CLEANUP_DRY_RUN=false`.
3. **Batch-1 migration** — one command (§1), or approve my retry.

### Blocked with a concrete reason
- **Fix 13 (PgBouncer)** — `dialer_worker.py:866` uses a **session-level** `SET app.bypass_rls = 'on'`, with a comment explicitly relying on it surviving on the pooled connection. Under PgBouncer *transaction* mode that breaks — and worse, RLS-**bypass** state could bleed between logical sessions. Must be rewritten to `SET LOCAL`-in-transaction (the adapter paths already do this correctly) and the remaining SET sites audited. Also: prod is systemd, not docker-compose — the doc's compose work is dev-parity only.
- **Fix 15 (Twilio)** — analysis proved the doc's approach can't work: Twilio's media WebSocket terminates *in this process*, so nothing external survives a restart to reconnect to. Needs a `TwilioSessionRegistry` whose recovery marks orphaned DB rows ended.

### My roadmap (larger than the doc)
- **#9 F-01/F-06 STT connection rewrite** — biggest remaining reliability item (a mid-call Deepgram drop currently ends the call silently). Designed; needs its own focused cycle. **Must land before fixes 22–24** or the splits invalidate the designs.
- **#10 F-07** concurrency guard · **#11 F-02/F-04(a,d)/F-05(i)** Nova parity (includes a real email-truncation bug in `turn_text`).

### Smaller queued debt
- Delete the stale root `ci.yml` (the decoy that misled the doc)
- `vonage_bridge._vonage_sessions` — same class as Twilio but **worse** (its `/event` handler reads the dict cross-request)
- A-law codec vectorization (same LUT treatment µ-law got)
- Bandit 60 Medium/214 Low ratchet backlog
- Point an external uptime monitor at `/healthz/workers` (journald is covered; this closes the "whole box died" case)
- Reminder worker's Redis client doesn't retry if Redis is down at boot → probe correctly flags it unhealthy while DB work continues (right fail-direction, worth knowing)
- Flaky test `test_stt_ask_ai_singleton_respects_recovery_window` (50 ms window) — widen or mark

### Recommended next
**Live validation calls** on `+442046132300` from `blaze-pool-150004` (CID `+442046132301`) — to hear the greeting fix (no more ~1s truncation), the batch-2 turn-taking, and Qwen's behaviour. Switch TTS → Cartesia in AI Options first if you still want that trial. The analysis of your earlier 07-20 test calls is also still pending.

---

## 11. Stripe SDK Readiness Deploy (`80f6cdab`) — 2026-07-21

**Prod HEAD moved:** `863f3032` → `80f6cdab`

### What was done

| Step | Result |
|------|--------|
| Researched latest Stripe Python SDK | v15.3.0 is latest, but v15 breaks `StripeObject.get()` |
| Audited ALL Stripe API calls in `billing_service.py` | 10 call sites — all safe on v12–v14, one `.get()` breaks on v15 (L346) |
| Chose version range `>=12.0.0,<15.0.0` | v14.3.0 resolved locally, v14.4.1 on server |
| Added to `requirements.txt` with detailed comment | One-line diff, well-documented |
| Ran prod-gate Stripe tests | 5/5 passed (previously 1 was skipping — now runs because SDK is present) |
| Ran full test suite | **3548 passed** (baseline +1), 0 errors |
| Verified `STRIPE_AVAILABLE=True` | `billing_service.py` now detects the SDK |
| Production audit of all billing code paths | 3 pre-existing issues found (none blocking) |
| Created documentation | `backend/docs/billing/stripe_sdk_readiness.md` |
| Deployed to server | `pip install` → import check → restart → health 200 |
| Verified all workers healthy post-deploy | 3/3 workers healthy, all 4 services active |

### Pre-existing bugs found during audit (not introduced by this change)

1. **`_claim_webhook_event` (L384-386)** — uses session-level `SET app.bypass_rls = 'on'`. Same RLS-bleed bug as `dialer_worker.py:866`. Low risk without PgBouncer; deferred to Fix #13 audit.
2. **`datetime.now()` without timezone (L316, L464, L493)** — produces naive datetimes. Works by accident on a UTC server, but fragile. Should be `datetime.now(timezone.utc)`.
3. **`_handle_invoice_paid` (L486)** — inserts invoice with `tenant_id=None` if Stripe metadata is missing. All app-created checkouts set metadata, but a manually-created Stripe subscription would hit this.

### Deploy verification
```
Server HEAD: 80f6cdab
stripe==14.4.1 in prod venv
healthz/deep: HTTP 200
Workers: 3/3 healthy (dialer 42s, voice 41s, reminder 41s)
Services: 4/4 active
Gate: 3548 passed, 0 errors (baseline +1)
journalctl -p err: no entries
```

### Files changed

| File | Change |
|------|--------|
| `backend/requirements.txt` | Added `stripe>=12.0.0,<15.0.0` (L62-67) |
| `backend/docs/billing/stripe_sdk_readiness.md` | **[NEW]** Full audit, go-live checklist, v15 upgrade path |

---

## 12. Updated 24-fix checklist (post Stripe SDK deploy)

| # | Fix | Status | Update |
|---|---|---|---|
| 1,2,3 | Sample rates | ✅ Verified consistent — NOT changed | — |
| 4,5 | Greeting barge-in | ✅ Content-aware echo immunity | — |
| 6 | soxr | ✅ Already installed (1.1.0) | — |
| 7 | Dialer heartbeat | ✅ Deployed | — |
| 8 | Worker alert | ✅ healthwatch timer + /healthz/workers | — |
| **9** | **Stripe live** | 🟡 **SDK installed + deployed** | Was "structurally mock" (no SDK + no key). Now SDK is live (`STRIPE_AVAILABLE=True`), billing exits mock mode when key is set. **Only the `sk_live_` key is missing.** |
| 10 | prod_gate Stripe | ✅ Deployed | — |
| 11,12 | Tenant isolation | ✅ Deployed | — |
| **13** | **PgBouncer** | ⛔ Blocked | Same `SET` bug also found in `_claim_webhook_event` during today's audit (adds to the audit scope) |
| 14 | Realtime fallback | ✅ Deployed | — |
| **15** | **Twilio state** | ⛔ Blocked | Needs `TwilioSessionRegistry` design |
| 16 | Circular import | ✅ Deployed | — |
| 17,18 | systemd restart | ✅ SIGKILL-verified | — |
| 19 | CI blocking | ✅ Already done | — |
| 20 | Guard caching | ✅ Subscription cached; DNC rejected | — |
| 21 | Cleanup worker | ✅ Deployed in dry-run | — |
| **22,23,24** | **File splits** | ⏳ Waiting for batch-2 #9–#11 | STT connection rewrite must land first |

**20 ✅ · 1 awaiting key · 2 blocked/redesign · 3 sequenced**

### What Fix #9 still needs to go fully live:
1. You provide `sk_live_...` key → set `STRIPE_SECRET_KEY` in server `.env`
2. Create Products/Prices in Stripe Dashboard → update `plans` DB table
3. Create webhook endpoint in Stripe Dashboard → set `STRIPE_WEBHOOK_SECRET`
4. Restart → verify no "mock mode" in logs → one test checkout → refund
5. Kill-switch: `STRIPE_BILLING_DISABLED=1`

---

*Record updated 2026-07-21 14:11 PKT. Prod HEAD `80f6cdab`. Rollback target `863f3032`. Gate: 3548 passed, 0 errors.*

