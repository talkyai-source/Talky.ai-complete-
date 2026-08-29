# Production Ground Truth — what is *actually* running

**Ticket:** TKT-001 · **Captured:** 2026-07-24 19:45–19:52 UTC · **Method:** read-only SSH to
`admins@144.76.17.150` (Blaze-VoIP-API) · **Production HEAD at capture:** `80f6cdab`

> Every claim below is followed by the command that produced it and its raw output. An assertion
> without its command is not a finding. Nothing in this document was inferred from repository
> configuration — repo config is a *statement of intent*, not evidence that something runs.

**Why this document exists.** The V2 readiness plan assumed a five-component telephony stack, a C++
gateway of unknown status, and a Prometheus/Grafana monitoring stack. Three of those assumptions were
wrong, and one thing nobody had assumed at all turned out to be true. Documenting a standby component
as active is the exact failure this project exists to eliminate.

---

## Summary of what changed as a result

| # | Question | Answer | Consequence |
|---|---|---|---|
| 1 | Which `talky-*` units exist and run? | 5 services + 3 timers active | — |
| 2 | Is the C++ gateway running? | **Yes** — and its unit file is **not in version control** | **F-1, High** |
| 3 | Which telephony processes run? | **Asterisk only.** No OpenSIPS/Kamailio/RTPengine/FreeSWITCH | Settles **R-5** |
| 4 | Is Docker running anything? | **Yes — Postgres and Redis are containers** | **F-2, High.** Refutes "systemd only" |
| 5 | Any Prometheus/Grafana/exporters? | **None running** | Settles **R-8**; VID-21 rewrite justified |
| 6 | Does the server tree match `origin/main`? | One commit behind; drift is docs-only | **F-5, Low** |

---

## Q1 — systemd units

```bash
systemctl list-units 'talky-*' --all --no-pager --plain
```
```
talky-api.service             loaded active   running Talky.ai API Server
talky-cleanup.service         loaded inactive dead    Talky.ai Data-Retention Cleanup
talky-dialer-worker.service   loaded active   running Talky.ai Dialer Worker
talky-healthwatch.service     loaded inactive dead    Talky.ai Worker Health Watch (heartbeat probe)
talky-reminder-worker.service loaded active   running Talky.ai Reminder Worker
talky-trunk-status.service    loaded inactive dead    Talky SIP-trunk live registration status updater
talky-voice-gateway.service   loaded active   running Talky.ai Voice Media Gateway (C++)
talky-voice-worker.service    loaded active   running Talky.ai Voice Pipeline Worker
talky-cleanup.timer           loaded active   waiting Nightly Talky.ai data-retention cleanup
talky-healthwatch.timer       loaded active   waiting Run Talky.ai Worker Health Watch every 2 minutes
talky-trunk-status.timer      loaded active   waiting Refresh Talky SIP-trunk registration status every 15s
```

The three `inactive dead` services are **correct** — they are timer-activated oneshots, not daemons.
`inactive` between runs is the expected state, and reading it as a fault is a common misdiagnosis.

Timer cadence, observed live:

```
talky-trunk-status.timer   next in 13s   last 1s ago
talky-healthwatch.timer    next in 27s   last 1min 32s ago
talky-cleanup.timer        next 03:00    last 16h ago
```

**Health at capture:** `/api/v1/healthz/deep` → `200`; `/api/v1/healthz/workers` → `healthy: true`,
all three workers (dialer, voice, reminder) beating within ~15 s.

---

## Q2 — the C++ voice gateway · **FINDING F-1 (High)**

It is running:

```bash
pgrep -a -f 'voice_gateway'
```
```
2489630 /opt/talky/services/voice-gateway-cpp/build/voice_gateway --host 127.0.0.1 --port 18080
```

It runs from `/etc/systemd/system/talky-voice-gateway.service`, dated **12 May 2026**:

```ini
[Unit]
Description=Talky.ai Voice Media Gateway (C++)
After=network.target

[Service]
User=admins
Group=admins
Type=exec
WorkingDirectory=/opt/talky/services/voice-gateway-cpp
ExecStart=/opt/talky/services/voice-gateway-cpp/build/voice_gateway --host 127.0.0.1 --port 18080
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=talky-voice-gateway

[Install]
WantedBy=multi-user.target
```

**That unit file does not exist anywhere in the repository.** Every other running unit does:

```bash
for u in talky-api talky-voice-worker talky-dialer-worker talky-reminder-worker talky-voice-gateway; do
  find /opt/talky -name "$u.service" | head -1; done
```
```
talky-api                repo copy: /opt/talky/backend/systemd/talky-api.service
talky-voice-worker       repo copy: /opt/talky/backend/systemd/talky-voice-worker.service
talky-dialer-worker      repo copy: /opt/talky/backend/systemd/talky-dialer-worker.service
talky-reminder-worker    repo copy: /opt/talky/backend/systemd/talky-reminder-worker.service
talky-voice-gateway      repo copy: NONE IN REPO
```

Confirmed against the working tree — `talky-voice-gateway` appears only in `deploy_to_server.sh`,
an old architecture review, and the ticket board. There is no `.service` file for it under version
control.

**Why this matters.** The gateway sits in the live media path. If this box is rebuilt, or the file is
lost, the gateway does not come back and **no copy of its definition exists anywhere**. Recovery would
mean reconstructing it from memory during an outage. The unit content is captured verbatim above so
that this is no longer true; committing it to `backend/systemd/` is the fix.

Note also that it is `Type=exec` / `Restart=on-failure` with no watchdog, and runs as `admins` rather
than a dedicated service account.

---

## Q3 — telephony processes · **settles R-5**

```bash
pgrep -a -f 'asterisk|opensips|kamailio|rtpengine|freeswitch'
```
```
4191270 /usr/sbin/asterisk -g -f -p -U asterisk
4191271 astcanary /var/run/asterisk/alt.asterisk.canary.tweet.tweet.tweet 4191270
```

**Asterisk only.** No OpenSIPS, no Kamailio, no RTPengine, no FreeSWITCH — despite the repo carrying
configuration for all four under `telephony/`. That tree is **design scaffold, not deployed
infrastructure**, and every document and video describing the telephony stack must say so plainly.

`astcanary` is Asterisk's own watchdog helper, not a separate component.

---

## Q4 — Docker · **FINDING F-2 (High)** · refutes the "systemd only" model

The assumption going in — recorded in `docs/sessions/worklogs/HANDOFF-NEXT-AGENT.md` and in this board's revision **R-2** —
was that production is systemd and the compose path is dead. That is **half wrong**:

```bash
docker ps -a
```
```
CONTAINER ID   IMAGE                COMMAND                  CREATED        STATUS                  PORTS
b829a084b128   postgres:15-alpine   "docker-entrypoint.s…"   2 months ago   Up 2 months (healthy)   127.0.0.1:5432->5432/tcp   talky-postgres-1
2e796a359107   redis:7-alpine       "docker-entrypoint.s…"   2 months ago   Up 2 months (healthy)   127.0.0.1:6379->6379/tcp   talky-redis-1
24b94013b4dd   hello-world          "/hello"                 2 months ago   Exited (0) 2 months ago                             determined_elion
```

Both datastores are owned by a compose project rooted in the repo checkout:

```bash
docker inspect talky-postgres-1 --format '{{index .Config.Labels "com.docker.compose.project"}} | {{index .Config.Labels "com.docker.compose.project.config_files"}}'
```
```
talky | /opt/talky/docker-compose.yml
```

**Production is a hybrid, and this is the single most important correction in this document:**

| Layer | How it runs |
|---|---|
| API, 3 workers, C++ gateway | **systemd**, from a git checkout at `/opt/talky` |
| **Postgres 15, Redis 7** | **docker compose**, project `talky`, from `/opt/talky/docker-compose.yml` |
| Asterisk | bare-metal system package |

Consequences, all of which change deliverables:
- A deployment runbook (DOC-10) saying "systemd only" would leave an engineer unable to explain, or
  restart, the database. **Both mechanisms belong in the runbook.**
- After a host reboot the containers must come up *before* the API, or every service crash-loops on a
  missing database. This ordering must be stated and verified.
- It sharpens the risk in **R-2**: `.github/workflows/deploy.yml` runs `docker compose up -d`. Against
  *this* host, that is not a no-op — it acts on the project that owns the live datastores. What that
  workflow's compose file actually declares determines whether the risk is "harmless" or "recreates
  the production database container". Resolved in TKT-003.

The idle `hello-world` container is leftover install verification; harmless, worth removing during a
cleanup ticket.

---

## Q5 — monitoring · **settles R-8**

```bash
pgrep -a -f 'prometheus|grafana|alertmanager|node_exporter|redis_exporter|otelcol'
```
```
NO MONITORING STACK RUNNING
```

Nothing is scraping this box. The repository's Prometheus/Grafana/Alertmanager configuration is
unwired. **Recording a walkthrough of dashboards that would render "No data" would actively mislead
viewers into believing the system is monitored** — VID-21's rewrite is confirmed as correct, and
building a monitoring stack stays out of scope for this window.

What *does* exist and genuinely works: the `/healthz/deep` and `/healthz/workers` endpoints, the
three-layer worker liveness design, `talky-healthwatch.timer` (every 2 min), `talky-trunk-status.timer`
(every 15 s), `talky-cleanup.timer` (nightly, dry-run), Sentry, and journald.

### The gap is worse than "nothing is scraping" — **F-12**

Repo audit of all three monitoring configuration sets found two further problems that would survive
even if a Prometheus were pointed at this box tomorrow:

1. **The dashboards query metrics the application has never emitted.** The app defines **39** metrics
   in two families — `voice_*` (**13**, in `infrastructure/metrics/voice_metrics.py`, genuinely wired
   into the live call path) and `talky_telephony_*` (**26**, in `core/telephony_observability.py`). The
   checked-in Grafana dashboards query `http_requests_total`, `pg_stat_database_numbackends`,
   `redis_commands_processed_total`, `telephony_active_calls`, `talky_active_calls`,
   `talky_pod_capacity` and similar. **Not one panel in any of the three dashboard sets references a
   metric name the application actually produces.** `deploy/grafana/README.md:20` attributes
   `telephony_active_calls` to `telephony_observability.py`; that file defines no such name.
2. **`/metrics` is auth-gated and fails closed.** `api/operational.py` returns **503** when
   `TELEPHONY_METRICS_TOKEN` is unset and **401** without a matching `X-Metrics-Token` header — and
   `core/prod_gate.py` makes the missing token a *boot-blocking* violation, so a correctly-gated
   production instance must have it set. Yet the only scrape config in the repo with a real job
   (`telephony/observability/prometheus/prometheus.yml`) ships with that header **commented out**.
   As checked in, it would receive 401s.

Alertmanager is unusable as committed: four unfilled `${...}` placeholders and a literal
`oncall@your-domain.com`. Stock Alertmanager does not expand `${VAR}` at all, so even populating the
environment would not work without an `envsubst` step that does not exist in the repo.

No redis, node or postgres exporter is defined anywhere — which is why the dialer liveness alert was
delivered as a systemd timer rather than a Prometheus rule.

**Consequence for VID-21:** defensible to film — the four `/healthz` routes with their real dependency
semantics, the healthwatch timer → journald path, and the two Sentry SDKs. Not defensible to film —
reading Grafana dashboards or explaining Prometheus alerts, because none of the three checked-in
dashboard sets could render real data even under a best-case scrape.

---

## Q6 — server tree vs `origin/main` · **FINDING F-5 (Low)**

```bash
git -C /opt/talky log -1 --format='%h %ad %s' --date=short
git -C /opt/talky status --porcelain
```
```
80f6cdab 2026-07-21 feat(billing): add stripe SDK dependency for billing readiness (doc fix 9 prep)

 M backend/deploy/healthwatch.sh
 M services/voice-gateway-cpp/build/voice_gateway
?? secrets/
```

Production is **one commit behind `origin/main`** (`0ffa7fa6`). That commit is **documentation only** —
the ticket board, the handoff note, a session record and some stray root-level `dream_*.py` scratch
files. No backend code. Production is therefore running current application code, and the gap is
cosmetic. It should still be closed so that "prod == main" is a statement someone can rely on.

Drift is within tolerance but not exactly as TKT-001 predicted:
- `services/voice-gateway-cpp/build/voice_gateway` — the rebuilt C++ binary. **Expected.**
- `secrets/` untracked — **expected**, correctly gitignored.
- `backend/deploy/healthwatch.sh` — **not** expected. The diff is a **file-mode change only**
  (`100644 → 100755`); someone `chmod +x`'d it on the box. Zero content difference. Benign today, but
  a fresh checkout would deploy it non-executable — **F-6**.

**TKT-001 test case 4 ("only the C++ binary + untracked `secrets/`") therefore fails**, on a
technicality worth recording rather than waving through.

---

## Deployment — which path is authoritative · TKT-003

Three mechanisms in this repo describe how the backend reaches production. The question was which one
is real. It is now settled **empirically**, from the Actions run history rather than from the files.

### The finding: the compose path has never deployed anything

```bash
gh run list --workflow=deploy.yml -L 15
```
Fifteen runs, **every one "success", every one 6–18 seconds.** A Docker build, push and deploy cannot
complete in eight seconds. Inspecting any run explains it:

```bash
gh run view 30039086948 --json jobs
```
```
Validate deployment request: success
Build & Push: skipped
Deploy: skipped
Rollback: skipped
Notify: skipped
```

`request-check` resolves `mode=skip` and every real job is skipped, so the run reports success having
done nothing. The gate is `github.event.workflow_run.conclusion == 'success'` — and **CI has never
been green in this window** (see below). The workflow is armed but has never fired.

Two further facts change the risk assessment recorded in **R-2**:
- The automatic `workflow_run` trigger sets `target_environment=staging` at initialisation and
  **only** overwrites it inside the `workflow_dispatch` branch. An automatic post-CI deploy therefore
  targets the **staging** GitHub Environment — production requires a manual dispatch. (Configured
  environments are `Preview` and `Production`; **no `staging` environment exists**.)
- The apply step is `docker compose up -d --no-build --no-deps backend` — scoped to the `backend`
  service. It would **not** recreate the live `talky-postgres-1` / `talky-redis-1` containers. It does
  SCP a `docker-compose.yml` over `${DEPLOY_PATH}` first, which *would* overwrite the file that
  defines them — but the server's copy is unmodified against git, corroborating that this has never
  run against this host.

### ⚠️ The ordering constraint this creates

**Red CI is currently the only thing preventing an automatic deploy to production.** Fix CI first and
the dormant workflow becomes live on the next green build. **TKT-003's gating must land before the CI
fix**, not after. This inverts the order implied by the original plan.

### Verdict

| Path | Status |
|---|---|
| `deploy_to_server.sh` — git checkout + `systemctl restart` at `/opt/talky` | **AUTHORITATIVE.** Corroborated by the server being a git checkout at `80f6cdab` with only expected drift. |
| `.claude/commands/deploy.md` — hand-written operator procedure | **Live, and a third independent copy** of the same git+systemd steps. Should reference the script, not restate it. |
| `.github/workflows/deploy.yml` — GHCR image + compose | **DORMANT, never executed.** To be gated behind manual dispatch only, with a header comment explaining what it is and why. Do not delete. |
| `infra/k8s/`, `infra/helm/` | **Scaffold.** Placeholder image tags and example domains. |
| Frontend | **Vercel native git integration** (project `talkleeai`), no `vercel.json`, no Actions involvement. Admin panel's Vercel status undetermined. |

Note the deploy workflow's Alembic migration step is **commented out** — migrations are manual either
way, consistent with the 47 raw SQL files.

### CI is red, and has been for 25 consecutive runs · **FINDING F-13 (High)**

```bash
gh run list --workflow=ci.yml -L 25
```
Every run from **2026-07-15 to 2026-07-23 failed.** Two distinct causes:

| Window | Failing job | Effect |
|---|---|---|
| 2026-07-15 → 07-21 | `Backend` **and** `Frontend` | gitleaks passed; tests ran and failed |
| 2026-07-23 → | `Secret Scan (gitleaks)` | fails **first** → `SQL Schema`, `Frontend`, `Backend`, `Docker Build` all **skipped** |

**The backend test suite has not executed in CI for over a week.** The local gate is green at 3548;
CI is not, and the two have diverged unobserved. The board records original task 33 ("fix 19, CI
blocking") as done — the gates are indeed blocking, but they have never been green, which is a
materially different situation and must be corrected in REV-39.

Also surfaced by the same logs: `fatal: No url found for submodule path
'.claude/worktrees/laughing-ramanujan-6e8820' in .gitmodules` — a stray worktree committed as a
submodule entry with no `.gitmodules` record. Harmless to the build, but it makes every checkout emit
a git error.

### 🔴 Why gitleaks fails · **FINDING F-14 (Critical) — handled separately**

`leaks found: 4`. All four are a production Postgres password in `tmp_query{,2,3,4}.sh` at repository
root, introduced by `0ffa7fa6` (2026-07-23) — **and this repository is public.** Full detail,
impact assessment and remediation sequence are tracked as an incident, not as a board ticket. It is
recorded here only because it is the mechanical cause of the CI failure above.

---

## Host facts

```
python : Python 3.12.3          (docs/ARCHITECTURE.md:35 says 3.11 — F-7)
os     : Ubuntu 24.04.4 LTS / kernel 6.17.0-20-generic
cpu/mem: 2 vCPU / 3.9 GB RAM
disk   : 47G total, 25G used (55%)
uptime : 103 days, load average 0.32 / 0.14 / 0.09
```

**2 vCPU and 3.9 GB** carries the API, four workers, the C++ gateway, Asterisk, and both datastore
containers. Load is low at idle, but this is the architectural ceiling the V2 work has to plan
around, and the concurrency knee has never been measured. This number belongs in DOC-02 and REV-41.

---

## Findings raised — for DOC-09

| ID | Sev | Finding |
|---|---|---|
| **F-1** | **High** | `talky-voice-gateway.service` runs in production from an **unversioned** unit file. No copy in the repo. Content captured above. |
| **F-2** | **High** | Postgres and Redis run as **Docker containers** via `/opt/talky/docker-compose.yml`, not bare metal. Architecture docs and the planned runbook are wrong. |
| **F-3** | Med | `talky-api` is `Type=exec` / `Restart=on-failure`, **no watchdog** — while all three workers are `Type=notify` / `Restart=always` / `WatchdogSec=180`. The repo unit matches, so this is by design or by omission, not deploy drift. The board records systemd fixes 17–18 as "already done"; for the API they are not. Needs a decision, not a blind change (uvicorn does not `sd_notify` natively). |
| **F-4** | Med | ~~The repo has **two** systemd directories — `backend/systemd/` (6 `.service` + 2 `.timer` + target + installer) and `backend/deploy/systemd/` (trunk-status only). Nothing states which is authoritative, and the installer reads only the first.~~ **RESOLVED 2026-08-27:** the trunk-status `.service` + `.timer` moved into `backend/systemd/`, which is now the single source of truth. `install-services.sh` globs that one directory. |
| **F-5** | Low | Production is one commit behind `origin/main`; the gap is documentation-only. |
| **F-6** | Low | `backend/deploy/healthwatch.sh` is mode-drifted on the server (`+x` applied live, not committed). A fresh deploy would ship it non-executable. |
| **F-7** | Low | `docs/ARCHITECTURE.md:35` says Python 3.11; production runs 3.12.3. |
| **F-8** | Low | ~~Stray `dream_*.py` scratch files committed at repository root in `0ffa7fa6`.~~ **RESOLVED 2026-08-27:** moved to `scripts/archive/dream/`. |
| **F-9** | Info | Idle `hello-world` container from install verification, 2 months old. |
| **F-10** | **High** | **Every Python service runs as `root`.** No `User=` directive in `talky-api`, `talky-dialer-worker`, `talky-voice-worker` or `talky-reminder-worker`; `talky-trunk-status` sets `User=root` explicitly. Confirmed live (`systemctl show -p User` returns empty → root). The only non-root service is the C++ gateway (`User=admins`) — the inverse of what you would choose. No dedicated service account exists. |
| **F-11** | Med | The test gate runs on **Python 3.11.9** locally while production runs **3.12.3**. A green gate is not evidence for the deployed interpreter. See `tickets/BASELINE.md`. |
| **F-12** | **High** | Monitoring is worse than unscraped: **no checked-in Grafana panel queries a metric the app emits**, `/metrics` is token-gated (503/401) while the only real scrape config has its auth header commented out, and Alertmanager has four unfilled `${...}` placeholders that stock Alertmanager cannot expand anyway. |
| **F-13** | **High** | **The last 25 `ci.yml` runs all failed**, spanning 2026-07-15 → 07-23. 24 are pushes to `main`; one is a Dependabot `pull_request` run. The streak extends at least one run further back (07-14 also failed), so 25 is a floor, not a total. The backend suite has not run in CI for over a week. Red CI is also the only thing currently preventing the dormant auto-deploy from firing. |
| **F-14** | **Critical** | Production Postgres password committed to a **public** repository in four root-level `tmp_query*.sh` files (`0ffa7fa6`, 2026-07-23). Tracked as an incident. The "binds `127.0.0.1`" mitigation is weaker than first written — see F-14 in `09-known-issues.md`; treat as compromised. |
| **F-15** | Low | `.claude/worktrees/laughing-ramanujan-6e8820` is committed as a submodule entry with no `.gitmodules` record — every checkout emits `fatal: No url found for submodule path`. |
| **F-16** | Med | `telephony/README.md:49` states "Current active SIP edge runtime: `opensips/`" — false since at least May. It was last touched 2026-03-17; the real bare-metal Asterisk build-out continued through 2026-07-21 and nobody revisited it. |
| **F-17** | **Critical** | Carrier SIP trunk credentials hardcoded in `setup-asterisk.sh` (`TRUNK_USER`, `TRUNK_PASS`, lines 23–24), **public since `b5351a50`, 2026-05-18 — over two months, and two months longer than F-14.** Unlike Postgres there is no loopback mitigation: a SIP trunk authenticates by credential from any source address by design, so these are remotely usable without touching this server. Toll fraud is the realistic outcome. The ARI password in the same script *is* generated at runtime — the correct pattern was applied fifteen lines away. **Rotate with the carrier ahead of F-14.** |
| **F-18** | Low | Committed C++ build artifacts: `services/voice-gateway-cpp/build/` **and** `build-asan/` (5.7 MB) are tracked despite being listed in `.gitignore` — gitignore does not retroactively untrack. |

---

## Test cases — TKT-001

| # | Check | Expected | Result |
|---|---|---|---|
| 1 | `systemctl is-active` × 4 core units | all `active` | 🟢 all four active |
| 2 | `/api/v1/healthz/deep` | `200` | 🟢 `200` first call |
| 3 | `/api/v1/healthz/workers` | healthy, 3 workers | 🟢 `healthy: true`, 3 beating |
| 4 | `git status --porcelain` | only C++ binary + `secrets/` | 🔴 **fails** — plus `healthwatch.sh` mode drift (F-6) |
| 5 | Server HEAD vs `origin/main` | equal or behind by known commits | 🟢 behind by one, documentation-only |
| 6 | Every claim has command + output | yes | 🟢 |

**Status: 5 of 6 pass.** Test 4's failure is recorded rather than waived — it is exactly the kind of
small unversioned change that becomes an unexplained difference six months later.
