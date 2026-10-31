# HANDOFF — Talky.ai backend · next agent brief
**Written 2026-07-21.** Companion doc: `SESSION-RECORD-2026-07-20_21.md` (full history + logs). Read that for *why* decisions were made; this file is *what to do next*.

> ⚠️ **DO NOT COMMIT THIS FILE OR THE SESSION RECORD TO A PUBLIC REMOTE** without review — they contain infrastructure detail. No passwords are written in either file, by design (see §2).

---

## 1. Current state — one paragraph

Production is healthy at **`863f3032`**. Seven deploys landed over two days: batch-1 voice correctness, batch-2 listening-path (8 increments), a jsonb DB hotfix, the campaign-delete fix, dialer liveness, and a 6-worker wave against an external "24-fix" document. **19 of the 24 doc fixes are done and verified.** The full test gate is **3547 passing with zero collection errors** for the first time (a long-standing passkeys import error that aborted every full run is fixed). All 3 background workers now have Redis heartbeats + systemd watchdogs and report at `/api/v1/healthz/workers`.

---

## 2. Server access + how to verify what's live

**SSH (from the user's Windows machine, Git-Bash syntax):**
```bash
ssh -i "/c/Users/AL AZIZ TECH/Desktop/Talky.ai-complete-/id_rsa_openssh" admins@144.76.17.150
```
- Host: `144.76.17.150` (Hetzner, hostname `Blaze-VoIP-API`)
- User: `admins` · Repo on server: `/opt/talky` · Backend: `/opt/talky/backend`, venv at `/opt/talky/backend/venv`
- Prod Python is **3.12** (local dev is 3.11 — don't rely on `audioop`, removed in 3.13)
- **sudo password: ASK THE USER.** It is deliberately not written into any repo file. Most `systemctl`/`journalctl -u` commands need it (pattern used: `echo "<pw>" | sudo -S -p "" <cmd>`).

**Services (systemd, NOT docker-compose — the fix doc is wrong about this):**
`talky-api`, `talky-dialer-worker`, `talky-voice-worker`, `talky-reminder-worker`
Timers: `talky-healthwatch.timer` (2 min), `talky-cleanup.timer` (03:00 nightly)

**Verify the live branch matches origin:**
```bash
# on the server
cd /opt/talky && git log -1 --format='%h %s'      # expect 863f3032 (or newer)
git status --porcelain                            # expect only: M services/voice-gateway-cpp/build/voice_gateway, ?? secrets/
# locally
git rev-parse --short origin/main
```
Expected drift on the server working tree is **only** the compiled C++ binary and the untracked `secrets/` dir. Anything else means someone edited on the box — investigate before deploying.

**Health verification (all read-only):**
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/healthz/deep   # want 200
curl -s http://localhost:8000/api/v1/healthz/workers                                  # want healthy:true, 3 workers
systemctl is-active talky-api talky-dialer-worker talky-voice-worker talky-reminder-worker
```
⚠️ The **first** curl right after a restart can return `000` — that's a pre-bind race, not a failure. Poll a few times.
⚠️ `/openapi.json` is disabled in prod, and a global auth middleware returns **403 for every unauthenticated request before method-matching** — so you cannot probe route existence unauthenticated (a bogus PATCH also 403s). Prove routes exist via a clean import + restart instead.

**Standard deploy sequence (the one that has worked 7×):**
```bash
# local: commit → push
git push origin main
# server:
cd /opt/talky && git pull --ff-only origin main
cd backend && venv/bin/python -c 'import app.main; print("IMPORT_OK")'
# if systemd unit files changed: cp them to /etc/systemd/system/ then daemon-reload  (see §3 safety)
sudo systemctl restart talky-api talky-dialer-worker talky-voice-worker talky-reminder-worker
# verify: healthz/deep 200, healthz/workers 3/3, journalctl -p err → no entries
```
**Deploy gate before any push:** `cd backend && python -m pytest tests/unit tests/security -q` → must be ~3547 passed, **0 errors**. (The `--continue-on-collection-errors` flag is no longer needed — if you find yourself needing it, something regressed.)

**Rollback:** previous good HEAD is **`4693bf13`**. `git checkout <hash>` on the server + restart. If systemd units changed in the bad deploy, restore the previous unit files and `daemon-reload` too.

---

## 3. 🚨 SAFETY RULES — read before touching the server

This is a **live system placing real phone calls to real people**, with real customer data.

**Never do these:**
1. **No deletions.** Never `rm -rf`, never drop/truncate a table, never delete rows, recordings, campaigns, leads, or log files. If something looks like it needs deleting, report it and stop.
2. **No destructive git on the server.** No `git reset --hard`, no `git clean`, no `git stash`, no force-push. Only `git pull --ff-only`. *(In this session two agents ran `git stash` on the shared **local** tree and wiped other workers' uncommitted work — recovered only because diffs were still in context.)*
3. **DB is read-only unless explicitly authorised.** Use the `dbq` skill for read-only SELECTs. Never write, and never run a migration without the user saying so for that specific migration. The Claude safety classifier blocks remote DB writes — **that is a feature, do not route around it**; ask the user instead.
4. **`CLEANUP_DRY_RUN` must stay `true`** until the user signs off retention windows. The cleanup worker currently only *logs* what it would delete. Flipping it starts permanently deleting rows from `call_events`/`stream_events`/`call_legs`.
5. **Never add `calls` to the cleanup allowlist.** The table allowlist is hardcoded for that reason.
6. **Never cache DNC results.** Deliberately rejected for compliance — a freshly-added DNC number must never be dialable from a stale cache. There's a comment in `call_guard._check_dnc` saying so.
7. **Don't restart services blind during live traffic.** Check for active calls first (`journalctl -u talky-voice-worker --since "5 minutes ago" | grep -i "call"`, or `/api/v1/calls/live` authenticated). A restart drops in-progress calls.
8. **Never print or commit secrets.** Don't `cat /opt/talky/backend/.env`. Read config through `get_settings()` and print only the fields you need (never the DB URL / API keys). A Redis password was leaking into journald — that was fixed this session (`log_redact.py`); don't reintroduce that pattern.
9. **No mass/outbound actions.** Never start a campaign to "test" — the allstate campaign has ~691 real leads. Test calls must be single, controlled, to a number the user names.
10. **systemd unit changes need the full ritual:** edit the repo copy → `cp` to `/etc/systemd/system/` → `daemon-reload` → restart → **verify the unit actually came up** (`systemctl is-active` + `systemctl show <unit> -p Type,Restart,WatchdogUSec`). The three worker units are `Type=notify` now — if a worker fails to send `READY=1`, systemd will hang in `activating` and eventually fail the unit.

**Working style that caught real bugs this session:** never trust a subagent's report — audit the actual diff (`git diff <file>`) before committing. Several "done" claims were wrong or incomplete; two workers' edits were silently reverted by another worker's stash.

---

## 4. The 24 fixes — exact status

### ✅ DONE + DEPLOYED (19)

| # | Fix | Note |
|---|---|---|
| 1,2,3 | Sample rates | **Verified consistent — deliberately NOT changed.** 655/655 TTS probes over 14 days show one uniform config (`s16le@16000`); zero mismatch warnings; gateway self-reports `internal=16000Hz, wire=8000Hz PCMU`. The doc's premise is false; the buzz it describes was fixed weeks ago. **Do not "fix" this.** |
| 4,5 | Greeting barge-in | Fixed **properly**: content-aware echo immunity extended to the agent-first presynth path. The doc's blind 3-second grace was **rejected** — it would suppress legitimate interrupts. |
| 6 | soxr | Already installed (1.1.0) |
| 7 | Dialer heartbeat | Deployed. (Found: the heartbeat coroutine existed but was **never scheduled**.) |
| 8 | Worker alert | `/healthz/workers` + `talky-healthwatch.timer`. The doc's Prometheus rule was unimplementable (no redis-exporter on the box). |
| 10 | prod_gate Stripe | Deployed — blocks boot on a non-live key or missing SDK |
| 11,12 | Tenant isolation | Deployed (IDOR-safe Optional field + service-layer filters + 26 tests) |
| 14 | Realtime fallback | Deployed — dropped OpenAI WS now falls back to the cascaded pipeline instead of dead air |
| 16 | Circular import | Deployed + an AST architecture test locks the direction permanently |
| 17,18 | systemd restart | Deployed; **SIGKILL recovery proven live** |
| 19 | CI blocking | Already done 2026-07-16. The doc pointed at a **stale root `ci.yml` that GitHub Actions never reads** — the real one is `.github/workflows/ci.yml`. |
| 20 | Guard caching | Subscription cached 60s (fail-through-to-DB). **DNC caching rejected** (compliance). |
| 21 | Cleanup worker | Deployed **in dry-run** |

### ⏳ REMAINING (5)

| # | Fix | What's needed |
|---|---|---|
| **9** | Stripe live | **Only the `sk_live_` key is missing.** Verified today: no `STRIPE_SECRET_KEY` is set at all and the `stripe` SDK isn't in requirements — billing is *structurally* mock. Plan in §5. |
| **13** | PgBouncer | ⛔ **Blocked by a real hazard.** `app/workers/dialer_worker.py:866` uses a **session-level** `SET app.bypass_rls = 'on'` with a comment relying on it surviving on the pooled connection. Under PgBouncer *transaction* mode that breaks — and RLS-**bypass** state could bleed between logical sessions (cross-tenant exposure). **Do the RLS `SET` → `SET LOCAL` audit first.** The adapter paths already do it correctly; copy that pattern. Also: prod is systemd, so PgBouncer is a host service, not a compose service. |
| **15** | Twilio state | ⛔ The doc's approach **cannot work** — Twilio's media WebSocket terminates *in this process*, so no external channel survives a restart to reconnect to (unlike Asterisk/ARI). Needs a new `TwilioSessionRegistry` whose recovery marks orphaned DB `calls` rows ended. Note `vonage_bridge._vonage_sessions` has the same class of bug but **worse** — its `/event` handler reads the dict cross-request. |
| **22,23,24** | File splits | ⏳ **Must wait for batch-2 #9–#11** (below) — those designs reference current line numbers in `voice_orchestrator.py` and would be invalidated by a split. |

---

## 5. What to work on next — priority order

### P1 · Live validation calls (highest value, low effort)
Nothing shipped this week has been heard on a real call. Place **one controlled call** to a number the user names — the known-good setup is destination `+442046132300` via trunk `blaze-pool-150004`, whose real caller-ID is **`+442046132301`** (the user's earlier `+4420461323` was one digit short).
Tenant: `info@allstateestimation.co.uk` (`790ca2db-6696-4fe9-9a2c-cd690c414a1e`) — LLM already `qwen/qwen3.6-27b`; TTS is **ElevenLabs**, so switch to **Cartesia** in AI Options first if the user still wants that trial.
Then verify in logs: greeting is **no longer truncated** (`outbound_greeting_presynth_done ... interrupted=False`, previously `interrupted=True` at ~1s), turn-taking behaves, no dead air.
⚠️ Qwen 3.6 27B was pulled once before (2026-06-27) for dodging the AI-disclosure question and hallucinating — watch its answers.
There is also an **unanalysed set of test calls from 2026-07-20 ~11:25–11:30** worth mining.

### P2 · Batch-2 #9 — STT connection lifecycle (biggest remaining reliability item)
Designed in full, not built. Today a mid-call Deepgram socket drop silently ends the call. Increment #9 = **F-01 + F-06 together** (they must ship in one commit):
- New `STTStreamFault(RuntimeError)` in `stt_provider.py` — composes with the existing `TerminalSTTError` path
- `deepgram_flux.py`: revive the **dead reconnect loop** (its cap/backoff code is unreachable today), with a stop-reason vocabulary — only an unexpected transport close is reconnect-eligible; auth/provider errors fail fast; reset the reason per attempt
- `deepgram_nova.py`: raise on abnormal close + a **KeepAlive heartbeat**. ⚠️ **The design's `send_keep_alive()` does not exist in SDK 5.3.0** — verified on prod; the correct call is `await conn.send_control(ListenV1ControlMessage(type="KeepAlive"))`
- `resilient_stt.py`: re-raise instead of silently returning when there's no secondary; `_ReplayBuffer.checkpoint()` on end-of-turn
- **Why F-06 must ship with F-01:** fixing F-01 makes reconnects actually fire, and without the EOT checkpoint the replay buffer re-transcribes already-committed audio.

Then **#10** (F-07 Flux concurrency guard — Nova already respects the cap, Flux doesn't) and **#11** (Nova normalization + config parity — includes a real **email-truncation bug**: `turn_text()` returns finals *or* the interim tail, dropping a trailing not-yet-final fragment like "dot com").

### P3 · Stripe go-live (once the user provides the key)
1. Pin `stripe` in `requirements.txt` + install in the prod venv
2. **Rehearse locally in test mode** (`sk_test_`) — full checkout → webhook → subscription-status write. Must be local: prod_gate now *refuses* a test key on prod.
3. Verify the **webhook path**: Stripe needs HTTPS; the frontend proxies `/api/v1` via Vercel, and signature verification needs the **raw body byte-for-byte** through that proxy — if it doesn't survive, expose a direct TLS endpoint for webhooks only. This is the classic go-live breaker.
4. Create live Products/Prices via API (test-mode price IDs won't work) and check the plan→price mapping in `plans.py`
5. Set key → create the live webhook endpoint via API → capture its signing secret to `STRIPE_WEBHOOK_SECRET` → restart → boot log must **not** say "mock mode" and prod_gate must pass
6. One real checkout on the cheapest plan, confirm webhook lands + DB `subscription_status` flips, then refund
Kill-switch: `STRIPE_BILLING_DISABLED`.

### P4 · Then
- Fixes **22–24** (file splits) — only after #9–#11
- Fix **13** after the RLS audit; fix **15** (Twilio registry) when Twilio traffic is real
- Flip `CLEANUP_DRY_RUN=false` after the user reviews a night of dry-run counts

---

## 6. Small queued debt (good filler tasks)
- **Batch-1 migration still unapplied** — `20260717_calls_lead_id_nullable.sql`; blocked by the safety classifier, so the **user** should run it (command in `SESSION-RECORD` §1). Also `app/infrastructure/storage/models.py:75` still says `nullable=False` (drift).
- **Delete the stale root `ci.yml`** — the decoy that misled the fix doc
- **Flaky test**: `test_stt_ask_ai_singleton_respects_recovery_window` (50 ms window) fails only under full-suite CPU load; passes 5/5 isolated. Widen the window or mark it.
- **A-law codecs** still per-sample Python loops (`audio_utils.pcm_to_alaw`/`alaw_to_pcm`) — apply the same numpy-LUT treatment µ-law got (with the same exhaustive bit-exactness test)
- **External uptime monitor** pointed at `/healthz/workers` — journald alerting is covered by the healthwatch timer, but nothing catches "the whole box died"
- **Reminder worker Redis** doesn't retry if Redis is down at boot → the probe correctly flags it unhealthy while DB work continues (right fail-direction; just know it)
- Bandit 60 Medium / 214 Low ratchet backlog (High is already 0 and gated)

---

## 7. Landmines / things already decided — don't redo these
- ❌ **Don't apply doc fixes 1–3** (sample rates). Refuted with 14 days of production evidence.
- ❌ **Don't add the doc's 3-second greeting grace.** Superseded by content-aware immunity; a blind timer would swallow real interrupts.
- ❌ **Don't cache DNC.** Compliance.
- ❌ **Don't split `voice_orchestrator.py`/`lifecycle.py`/`call_guard.py`** before batch-2 #9–#11.
- ❌ **Don't wire PgBouncer** before the RLS `SET LOCAL` audit.
- ❌ **Don't use the doc's Prometheus alert** — no redis-exporter exists.
- ✅ The `.github/workflows/ci.yml` scanners are **already blocking and green** — the root `ci.yml` is dead.
- ✅ The domain layer must never import `app.api` — `tests/unit/test_no_domain_api_imports.py` enforces it (two allowlisted pre-existing residuals: `campaign_service.py`, `state_backend.py`).

---

## 8. Useful project-specific skills
`dbq` (read-only prod SQL) · `prodlogs` (log triage) · `deploy` (test→commit→push→deploy→verify) · `detect-turn-taking`, `detect-quality`, `detect-voicemail`, `detect-intent` · `learn-from-calls` (weekly loop) · `voice-eval` (isolated /tmp overlay, never touches prod)

---

*Prod HEAD `863f3032` · rollback `4693bf13` · gate: 3547 passed, 0 errors.*
