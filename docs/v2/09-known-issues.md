# Known Issues & Technical Debt — running findings

**Status:** RUNNING FILE. Opened 2026-07-24 (TKT-001). This is fed continuously as tickets execute,
per ground rule 9 — *anything found to be false during a ticket gets written here immediately, not
remembered.* It becomes the DOC-09 deliverable in Sprint 2; until then it accumulates.

> This document is only useful if it is honest. A known-issues file that omits the embarrassing
> entries is worse than none, because it creates false confidence. Every entry below states what is
> wrong, how we know, and what it costs — including the ones we caused ourselves.

**Severity key:** 🔴 Critical (act now) · 🟠 High (schedule) · 🟡 Medium (v2 backlog) · ⚪ Low/Info

---

## Open — action required

### 🔴 F-23 · Row-level security is defined but **not enforced**
- **What:** the production runtime role is `rolsuper = true`, `rolbypassrls = true`. Postgres skips
  RLS entirely for such a role. **All 64 defined policies are inert.**
- **Evidence:** `SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user`
  → `{'role': 'talkyai', 'rolsuper': True, 'rolbypassrls': True, 'rolcreaterole': True}`.
  `calls`/`campaigns`/`leads` have `relrowsecurity=true` and `relforcerowsecurity=false`;
  `tenants` and `tenant_ai_configs` have RLS not enabled at all (**F-30**).
- **Impact:** tenant isolation rests **entirely** on application-layer `WHERE tenant_id = …` filters.
  That layer is real and was hardened deliberately — but it is the only one, not the second one.
  Separately, the application connects to Postgres as a **superuser with CREATEROLE**, which is a
  least-privilege failure on its own and compounds F-10 (all OS services run as root).
- **Corroboration:** `core/db.py` already notes `readonly=True` is "a no-op against today's superuser
  pool". The knowledge existed in-code and never became a finding.
- **Full analysis:** `docs/v2/rls-set-audit.md`.

### 🟡 F-24 · 39 session-scoped RLS sites — **latent** (revised down from High)
- 3 bare `SET app.…` (dialer worker, event emitter, billing service) and **36** via
  `apply_tenant_rls_context()`, which passes `set_config(…, false)` — `false` is the `is_local` flag.
- **Correction to the first write-up.** These do **not** leak between requests today. asyncpg's pool
  issues `RESET ALL` on release (verified in the deployed `asyncpg 0.29.0`: `pool.py:209` →
  `Connection.reset()` → `RESET ALL;`). The first draft asserted a live cross-tenant leak; that was an
  assumed pooling behaviour rather than a verified one, and it is withdrawn.
- **Still a hard blocker for TKT-010.** Under PgBouncer transaction pooling a bare `SET` is its own
  implicit transaction, lands on some server connection, and is released — the *next* statement may be
  routed elsewhere. So the GUC both fails to apply where intended **and** pollutes a connection other
  tenants will use.
- **Still ordered before the F-23 role fix.** De-privileging the role arms all 39 at once.
- **Today's safety comes from a library implementation detail, not from this codebase.** Now pinned by
  `backend/tests/unit/test_rls_set_local_invariant.py`, which allowlists exactly these 4 files and
  fails if a fifth appears — or if an allowlisted file is fixed and the entry is left behind.

### ✅ F-25 · Three sites broken regardless of pooling — **FIXED 2026-07-24**
- `telephony_bridge._verify_call_ownership`, `telephony_bridge.hangup_calls_for_campaign` and
  `provider_cost_ledger._flush_once` issued `SET LOCAL` with **no open transaction**, so the bypass
  was discarded before the query it was meant for.
- **Impact had RLS ever been enforced:** an ownership check that fails open or closed unpredictably,
  a campaign Stop that silently sweeps nothing, and a cost ledger that drops rows. Masked entirely by
  F-23 today — which is the only reason nobody noticed.
- **Fixed:** each wrapped in `async with conn.transaction():`, with a comment explaining why the
  wrapper is load-bearing so it does not get "tidied away" later.

### 🔴 F-14 · Production Postgres password in a public repository
- **What:** `tmp_query{,2,3,4}.sh` at repo root each contain `export PGPASSWORD=…` for
  `psql -U talkyai -d talkyai`. Introduced by `0ffa7fa6` (2026-07-23). The repository
  `talkyai-source/Talky.ai-complete-` is **public**.
- **Evidence:** gitleaks in CI — `leaks found: 4`, all four `rule=generic-api-key`, SARIF confirms
  file and line. Repo visibility from `gh repo view --json isPrivate` → `false`.
- **Impact:** the credential is in git history; deleting the files does not remove it.
- **On the "it binds loopback" mitigation — stated more carefully than in the first draft.** Postgres
  is not *published to the internet by Docker's port mapping*; that is what `docker ps` shows, and it
  is all it shows. It is **not** verified against the host firewall, any SSH tunnel, or a reverse
  proxy. More importantly the mitigation is weak on its own terms: **F-10** means any RCE in the API
  or a worker is already root on that box, at which point `.env` is readable directly and the leaked
  password adds nothing. The dominant realistic threat for a public-repo leak is not manual network
  pivoting — it is **automated harvesting of public pushes, which typically happens within minutes**.
  **Treat the credential as compromised regardless of network reachability.**
- **Not checked:** GitHub secret-scanning alerts returned 404 (feature not enabled or not accessible),
  so we do not know whether GitHub itself flagged this. Worth enabling either way.
- **Done 2026-07-24:** the four `tmp_query*.sh` files are deleted from the working tree (nothing in
  the codebase referenced them — only these documents did), and `.gitleaksignore` documents the four
  fingerprints.
- **The fingerprints are COMMENTED OUT, deliberately.** They were briefly written active, which would
  have turned the CI gitleaks job green — and with it the Backend/Frontend/SQL/Docker jobs behind it —
  while the password is **not confirmed rotated**. That directly contradicted the file's own header.
  Caught in review and reverted. **The red build is the control**: it is the only automated signal
  that a live credential sits in public history, and switching it off while the credential is live
  would leave the leak in place and the alarm off. Uncomment only after rotation.
- Commit date note: `0ffa7fa6` is `2026-07-23T19:42Z` UTC, `2026-07-24T00:42+05:00` local. Both dates
  appear across these documents; they are the same moment.
- **⚠️ STILL OUTSTANDING:** **rotate the `talkyai` password**, and decide the repository's visibility.
  The credential remains in public history; deletion changed nothing about that.
- **Owner:** repository owner. **Status:** files removed, **rotation and visibility decision open**.
- ⚠️ **Tension worth naming:** this is rated "Critical / act now" and has sat untouched for two days.
  That is a legitimate owner-decision blocker, not negligence — but a Critical with no owner ping and
  no date is how a Critical quietly becomes a Medium. Escalate if unresolved at the next check-in.

### 🟠 F-13 · CI red on `main` for 25 consecutive runs
- **What:** the last 25 `ci.yml` runs, spanning 2026-07-15 → 07-23, **all failed**. 24 are pushes to
  `main`; one is a Dependabot `pull_request` run. Spot-checking one run further back (07-14) also
  shows failure, so **25 is a floor, not the total length of the streak**. Two causes: before 07-23
  the `Backend` and `Frontend` jobs failed; since 07-23 gitleaks fails *first*, so `SQL Schema`,
  `Frontend`, `Backend` and `Docker Build` are all **skipped**.
- **Impact:** **the backend suite has not executed in CI for over a week.** Local is green at 3548;
  CI and local have diverged unobserved. The board recorded original task 33 ("CI blocking") as done —
  the gates block correctly, they have simply never been green, which is a different situation.
- **Note:** fixing this is gated on F-14 (gitleaks will keep failing while the leak is in history).
- **Status:** open. **Blocks:** TKT-017 code freeze, REV-39.

### 🟠 F-1 · C++ gateway ran from an unversioned unit file — *partially resolved*
- **What:** `talky-voice-gateway.service` has run in production since 2026-05-12 from
  `/etc/systemd/system/` with no copy in the repository. All four Python units had one.
- **Resolved:** the live unit was captured verbatim and committed as
  `backend/systemd/talky-voice-gateway.service` (TKT-001).
- **Still open:** the running unit and the repo copy are not linked by any deploy step —
  `deploy_to_server.sh` neither installs nor restarts it, and no CI or deploy path builds the binary.
  It remains manually built and manually installed.

### 🟠 F-10 · Every Python service runs as root
- **What:** no `User=` directive in `talky-api`, `talky-dialer-worker`, `talky-voice-worker` or
  `talky-reminder-worker`; `talky-trunk-status` sets `User=root` explicitly. Confirmed live —
  `systemctl show -p User` returns empty, i.e. root.
- **Impact:** any RCE in the API or a worker is immediately root on the box that also holds the
  database container and the SIP trunk credentials. The only non-root service is the C++ gateway
  (`User=admins`) — the inverse of what you would choose.
- **Note:** fixing this is not a one-line change — file ownership under `/opt/talky`, the venv, and
  journald access all need to move together. Schedule it; don't improvise it.

### 🔴 F-17 · Carrier SIP trunk credentials public since 2026-05-18 — **escalated from High**
- **What:** `setup-asterisk.sh:23-24` assigns `TRUNK_USER` / `TRUNK_PASS` as literals and interpolates
  them into `/etc/asterisk/pjsip.conf` for the `sip3.blazedigitel.com` trunk.
- **Exposure:** in the **public** repository since commit `b5351a50`, **2026-05-18** — verified with
  `git log --follow --diff-filter=A -- setup-asterisk.sh`. That is **over two months**, and two months
  *longer* than F-14's Postgres password.
- **Why this is more serious than F-14, not less:** F-14 is mitigated by Postgres binding loopback. A
  SIP trunk has no equivalent mitigation — **it authenticates by credential from any source address
  by design.** That is what a trunk is. Anyone with these values can register against the carrier and
  originate calls billed to this account, from anywhere, without touching this server at all.
- **Realistic outcome:** toll fraud. The standard pattern is automated harvesting of public commits,
  followed by high-volume international dialling, typically discovered via the invoice.
- **Contrast, and it makes the omission harder to excuse:** the ARI password in the *same script* is
  generated at runtime with `openssl rand -hex 24` and written to `/opt/talky/secrets/`. The correct
  pattern was understood and applied fifteen lines away.
- **Code fixed 2026-07-24:** both literals removed. `setup-asterisk.sh` now requires them from the
  environment via `${TRUNK_USER:?…}` / `${TRUNK_PASS:?…}`, which **fails closed** — the script aborts
  rather than provisioning a broken trunk. A banner above the block records the exposure window and
  states that rotation is still mandatory.
- **⚠️ The code fix is INCOMPLETE — the same trunk identifier appears elsewhere in the repo.**
  `setup-asterisk.sh` was the file the ticket named, so it was the only file fixed. A review found the
  trunk username/DID digits also present at **`backend/app/infrastructure/telephony/pjsip_config_generator.py:127`**
  (load-bearing explanatory comment, not a placeholder) and **`backend/app/core/errors.py:26`**.
  Lower severity than the password half of the pair, but "code fixed" overstated it. Replace both with
  a placeholder, or accept them explicitly once the credential is rotated.
- **⚠️ STILL OUTSTANDING, and only the owner can do it:**
  1. **Rotate with the carrier.** Removing the literals from the working tree does **not** remove them
     from git history, and the history is public. Until rotation, the exposure is unchanged.
  2. **Request recent CDRs** to confirm no unauthorised origination has already occurred. Two months
     is long enough that "probably fine" is not an answer.
- **Owner:** repository owner + carrier account holder. **Status:** partially fixed in code,
  **rotation open, two further occurrences open**.

### 🟠 F-12 · Monitoring is not merely unscraped — it could not work as configured
- Three non-unified config sets (root + `backend/deploy/prometheus/`, `infra/`,
  `telephony/observability/`). Nothing is scraping production — confirmed by process list.
- **No checked-in Grafana panel queries a metric the application emits.** The app defines **39**
  metrics — **13** `voice_*` in `infrastructure/metrics/voice_metrics.py` (wired into the live call
  path) and **26** `talky_telephony_*` in `core/telephony_observability.py`. Dashboards query
  `http_requests_total`, `telephony_active_calls`, `talky_pod_capacity` — none of which exist.
  `deploy/grafana/README.md:20` attributes a metric to a file that does not define it.
- `/metrics` returns **503** without `TELEPHONY_METRICS_TOKEN` and **401** without the header, while
  the only real scrape config has that header commented out.
- Alertmanager has four unfilled `${...}` placeholders and a literal `oncall@your-domain.com`; stock
  Alertmanager cannot expand `${VAR}` anyway.
- **Consequence:** slow degradation is not detected today — only hard failure, via healthwatch and
  Sentry. Building a stack is explicitly out of scope for this window; the honest scope is recorded
  in VID-21.

### 🟠 F-2 · Production is a hybrid, and the documentation says otherwise
- Postgres 15 and Redis 7 run as **Docker containers** (`talky-postgres-1`, `talky-redis-1`) owned by
  `/opt/talky/docker-compose.yml`. The application runs under systemd from a git checkout. Asterisk is
  a bare-metal package.
- **Impact:** any runbook saying "systemd only" leaves an engineer unable to explain or restart the
  database. After a host reboot the containers must be up *before* the API or every service
  crash-loops. **DOC-02 and DOC-10 must both state this.**

### 🟡 F-3 · `talky-api` has no watchdog, unlike the workers
- `Type=exec` / `Restart=on-failure`, no `WatchdogSec` — while all three workers are `Type=notify` /
  `Restart=always` / `WatchdogSec=180`. The repo unit matches the deployed one, so this is omission,
  not drift.
- **Not a blind fix:** uvicorn does not `sd_notify` natively, so `Type=notify` would need app-side
  support. The API is the process that terminates every call and holds the ARI connection; a hung API
  is currently invisible to systemd. Needs a decision.

### 🟡 F-11 · The test gate does not run on production's Python
- Local gate: **Python 3.11.9**. Production: **3.12.3**. A green gate is not evidence for the deployed
  interpreter. CI's version needs confirming once CI runs again.

### 🟡 F-16 · `telephony/README.md` documents a component that has never run
- Line 49: *"Current active SIP edge runtime: `opensips/`"*. Production runs **Asterisk only**.
- **Why it drifted:** the README was last touched 2026-03-17; the bare-metal Asterisk build-out
  continued through 2026-07-21 and nobody revisited it. A new engineer following it would debug a SIP
  edge that is not in the call path. **DOC-04 must correct it.**

### 🟡 F-4 · Two systemd directories in the repo
- `backend/systemd/` (**6** `.service` + **2** `.timer` + `talky.target` + `install-services.sh`) and
  `backend/deploy/systemd/` (trunk-status `.service` + `.timer` only). Nothing states which is
  authoritative, and `install-services.sh` reads only the first — so the trunk-status units are
  invisible to the installer. Noted inline in the script until the two are consolidated.

### ⚪ F-18 · Committed C++ build artifacts
- `services/voice-gateway-cpp/build/` **and** `build-asan/` (5.7 MB) are tracked despite being listed
  in `.gitignore` — gitignore does not retroactively untrack.

### ⚪ F-15 · Phantom submodule entry
- `.claude/worktrees/laughing-ramanujan-6e8820` is committed as a submodule with no `.gitmodules`
  record. Every checkout emits `fatal: No url found for submodule path`. Harmless to builds, noisy.

### ⚪ F-7 · `docs/ARCHITECTURE.md` drift
- `:35` says Python 3.11 (prod 3.12.3); `:89` describes docker-compose networking for the app;
  `:95` describes an OTel/Tempo/Prometheus stack that does not run.

### ⚪ F-8 · Stray scratch files at repo root
- `dream_*.py` (6 files) committed in `0ffa7fa6`. Verified to contain **no** credentials, unlike the
  `tmp_query*.sh` files in the same commit (F-14).

### ⚪ F-9 · Idle `hello-world` container
- Left from Docker install verification, 2 months old.

### ⚪ F-5 · Production one commit behind `origin/main`
- Prod `80f6cdab`, main `0ffa7fa6`. The gap is documentation only — no application code differs.

---

## Closed

### ✅ F-6 · `healthwatch.sh` mode drift — *closed 2026-07-24*
- **Was:** the server's copy was `100755`, the repo's `100644` — an uncommitted `chmod +x` on the box,
  making TKT-001 test case 4 fail.
- **Correction to the original write-up:** I first recorded that a fresh deploy would ship it
  non-executable and break the timer. That was wrong — `talky-healthwatch.service` invokes it as
  `/bin/bash /opt/talky/backend/deploy/healthwatch.sh`, so the exec bit is never required. The drift
  was cosmetic.
- **Fixed by:** `git update-index --chmod=+x backend/deploy/healthwatch.sh`, so repo and production
  now agree and the server's working tree stops reporting drift.

---

## Deliberate decisions that look like bugs

Recorded so nobody "fixes" them. Each has been attempted or proposed before.

| Decision | Why | Reference |
|---|---|---|
| Sample-rate fixes 1–3 **not** applied | Premise refuted with 655 uniform production probes over 14 days. Applying them would introduce distortion. | R-10 |
| Blind 3-second barge-in grace **rejected** | Replaced with content-aware echo immunity. A blind timer suppresses legitimate caller interrupts. | R-10 |
| DNC results **never cached** | Compliance. A number added to DNC must be blocked on the very next attempt. | fix 20–21 |
| `CLEANUP_DRY_RUN` left `true` | Retention windows are not signed off. It logs what it would delete. | TKT-013 |
| `talky-cleanup.service` has no `Restart=` | Deliberate — a retention job that retries itself on failure is a data-loss risk. | unit comment |
| Twilio session resumption **not implemented** | Impossible as specified; the socket dies with the process. | R-12 |
| C++ gateway is `Type=exec`, not `notify` | It has no sd_notify support. `Type=notify` would fail to start it. | F-1 |

---

## Measurement gaps

Not defects — things nobody has measured, which is its own risk.

- **The concurrency knee is unknown.** No load-test evidence exists. The host is 2 vCPU / 3.9 GB
  carrying the API, four workers, the C++ gateway, Asterisk and both datastore containers.
- **`talky-api` runs a single uvicorn worker** because per-call ARI state is process-local. That is
  the scaling ceiling and v2 must address it.
- **Nothing shipped in the last week has been heard on a real call** (TKT-004).
