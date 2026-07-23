# Plan revisions — V2 Readiness Document vs. actual project state

**Written 2026-07-23** against repo HEAD `80f6cdab` and production `144.76.17.150`.

The V2 Readiness Document is a good plan. It is also written from the outside, and nine of its
assumptions do not survive contact with the codebase. This file records each one with the evidence
that contradicts it and what the tickets do instead. Nothing here is opinion — every claim has a
file path or a command behind it.

---

## R-1 · Sprint order inverted: code stabilisation moved BEFORE documentation

**Original:** Phase 1 docs (days 1–7) → Phase 2 videos (8–14) → Phase 3 code fixes (15–21).

**Problem:** Phase 3 task 35 splits `lifecycle.py`, `voice_orchestrator.py` and `call_guard.py`
into modules under 500 lines. Those three files are **5,230 lines** today:

| File | Lines |
|---|---|
| `backend/app/domain/services/telephony/lifecycle.py` | 2,014 |
| `backend/app/domain/services/voice_orchestrator.py` | 1,764 |
| `backend/app/domain/services/call_guard.py` | 1,452 |

Splitting them will produce roughly twelve new modules with new names and new import paths. Every
Phase 1 doc that describes the call flow, the audio pipeline or the voice pipeline, and every Phase 2
video that walks the codebase on screen, would be **stale the moment the split lands** — and a video
cannot be patched, only re-recorded.

**Revision:** Sprint 1 = code stabilisation, ending in a tagged code freeze (TKT-017). Docs and
videos are then produced against code that will not move. This costs nothing — the deliverable set
is identical — and removes the single largest source of rework in the plan.

---

## R-2 · There are two contradictory production deploy paths. Neither doc nor video can be written until that is resolved.

**Evidence:**
- `deploy_to_server.sh` — SSH to `admins@144.76.17.150`, `git pull --ff-only` in `/opt/talky`,
  `systemctl restart talky-api talky-dialer-worker talky-voice-worker talky-reminder-worker`.
  **No Docker anywhere.**
- `.github/workflows/deploy.yml` — builds a backend image, pushes to GHCR, SCPs `docker-compose.yml`
  to `secrets.DEPLOY_HOST`, rewrites the `backend.image` tag and runs `docker compose up -d`.
  It is triggered by `workflow_run` **automatically after CI passes on `main`**.
- `HANDOFF-NEXT-AGENT.md:25` — *"Services (systemd, NOT docker-compose — the fix doc is wrong about this)"*.

These describe two different production systems. One of them is wrong, and the wrong one has an
automatic trigger on every push to `main`.

**Revision:** new ticket **TKT-003** on Day 02, before anything else in the deployment chain.
It establishes which path is real, and then either disables/guards the dead one or fixes it. DOC-10
(deployment runbook) and VID-15 (deployment walkthrough) both block on it. Writing a runbook for the
wrong mechanism would be worse than having no runbook.

---

## R-3 · Task 1 names a post-call analyzer that never runs

**Original task 1:** *"every step from dialer_worker picking up a job to **post_call_analyzer**
running after hang up."*

**Evidence:** `backend/app/domain/services/post_call_analyzer.py` exists and is fully implemented,
but a repo-wide search finds it referenced **only** from `backend/tests/unit/test_post_call_analyzer.py`.
The code that actually runs after a hangup is:

```
lifecycle.py:_on_call_ended
  → services/scripts/call_transcript_persister.py:save_call_transcript_on_hangup
    → call_transcript_persister.py:_schedule_call_summary
      → domain/services/call_summary/store.py:generate_and_store
        → domain/services/call_summary/summarizer.py:summarize_transcript
```

**Revision:** DOC-01 documents the real chain and records `post_call_analyzer.py` as dead code in
DOC-09. A decision — delete it or wire it in — is a DOC-09/REV-40 gap item, not a doc-writing task.

---

## R-4 · The C++ voice gateway may not be running in production at all

**Evidence:**
- `services/voice-gateway-cpp/README.md` self-describes as *"Status: Scaffold created for frozen
  Talk-Leee day plan execution"* (Day 4 baseline).
- **No systemd unit for it exists in the repo.** The units present are `talky-api`,
  `talky-dialer-worker`, `talky-voice-worker`, `talky-reminder-worker`, `talky-cleanup`,
  `talky-healthwatch`, `talky-trunk-status`.
- Yet `deploy_to_server.sh:54` prints the status of a unit named `talky-voice-gateway`, and the
  server working tree carries a modified compiled binary at `services/voice-gateway-cpp/build/voice_gateway`.

So: a binary is built on the box, a deploy script references a unit for it, and no such unit is in
version control. Task 3 asks *"why it exists in C++, how to build it, how to restart it"* — that
question cannot be answered honestly without first establishing whether it is running.

**Revision:** TKT-001 answers it on Day 01. DOC-03's scope then forks: if it is live, document it
fully **and get the unit file into version control** (that is a real production risk — an
unversioned unit is one reinstall away from being lost). If it is not live, DOC-03 becomes a short
"what this is, why it is not deployed, what it would take" doc and the rest of its budget goes to DOC-05.

---

## R-5 · Task 4 assumes five telephony components are all in play. Production appears to run one.

**Original task 4:** *"Asterisk, FreeSWITCH, OpenSIPS, Kamailio, RTPengine. Why all exist, what each
handles, which is active vs standby."*

**Evidence:**
- `telephony/README.md` claims OpenSIPS is the active SIP edge and Asterisk the active B2BUA.
- `setup-asterisk.sh` provisions **bare-metal Asterisk** via `apt install asterisk`, writing
  `/etc/asterisk/{http,ari,rtp,pjsip,extensions}.conf` with a direct PJSIP trunk to
  `sip3.blazedigitel.com`. It does not use the `telephony/asterisk/Dockerfile`.
- `HANDOFF-NEXT-AGENT.md` lists no OpenSIPS / Kamailio / RTPengine / FreeSWITCH process.
- `telephony/freeswitch/conf/**` is almost entirely `.gitkeep` placeholders.
- `telephony/modules/*` is entirely `.gitkeep` — no custom modules exist.

The `telephony/` tree is a large, carefully-executed **design and validation exercise** (three
phases, a signoff doc, hundreds of evidence artifacts). That is not the same as running infrastructure.

**Revision:** DOC-04 is retitled *"Telephony stack — what is live, what is scaffold, and why both
exist"*. Its first section is a process-level inventory taken from the box (TKT-001), not from the
README. Documenting a standby component as if it were active is exactly the failure this whole
document exists to prevent.

---

## R-6 · Task 6 says "every .env variable". There are 245 of them, and `.env` is not the whole story.

**Evidence:** `backend/app/core/config.py` declares ~37 typed settings on `class Settings`. A scan of
`backend/app/**/*.py` finds **245 unique environment variable names** read ad hoc via
`os.getenv()` / `os.environ.get()`. Configuration is decentralised, not consolidated.

Explaining 245 variables to the standard the document asks for ("what it does, what breaks if wrong
or missing") is not a one-day task, and most of that effort would land on flags nobody has ever set.

**Revision:** DOC-06 is tiered:
- **Tier 1 — Critical (must be correct or the system does not work / is unsafe):** full treatment,
  every variable, what breaks. This is the tier that matters.
- **Tier 2 — Operational:** one-line each, grouped by subsystem.
- **Tier 3 — Feature flags & tuning knobs:** auto-generated table (name, default, file it is read
  in) with a note that undocumented defaults are the documented behaviour.
- Plus a standing finding in DOC-09: **config is decentralised**; consolidating into `Settings` is a
  v2 candidate.

---

## R-7 · Task 7 says 36 tables. There are about 70.

**Evidence:** `backend/database/fresh_schema.sql` (5,961 lines) contains **70** `CREATE TABLE`
statements; the older `complete_schema.sql` has 60. Separately,
`backend/app/infrastructure/storage/models.py` declares only **4** ORM classes (`Campaign`, `Lead`,
`Call`, `Conversation`) — most of the app talks to Postgres through raw SQL via
`app/core/postgres_adapter.py`, not that ORM.

**Revision:** DOC-07 documents ~70 tables, grouped by domain (tenancy, telephony, calls, security &
audit, billing, abuse/guard, white-label), with full treatment for the high-volume and
high-consequence tables and a one-line entry for the rest. It also records the ORM/raw-SQL split as a
DOC-09 finding — a four-class ORM sitting beside a seventy-table schema is a trap for a new engineer.

---

## R-8 · Task 21 (monitoring & alerts walkthrough) is not achievable as written

**Original task 21:** *"how to read Grafana dashboards, what Prometheus alerts mean…"*

**Evidence:** there are **three separate, non-unified** monitoring config sets in the repo —
- root `alertmanager.yml` + `backend/deploy/prometheus/voice_alerts.yml` + `deploy/grafana/dashboards/talky-backend.json`
- `infra/alertmanager/talky-alerts.yaml` + `infra/grafana/dashboards/{capacity,pipeline_latency,quality}.json` (Kubernetes-oriented)
- `telephony/observability/{prometheus,alertmanager}/…`

— and **none has in-repo evidence of actually scraping production**. `deploy/grafana/README.md`
says outright that panels will read "No data" until exporters are added. The root `alertmanager.yml`
uses unfilled `${...}` placeholders for its Slack webhook. Prior work already established there is
no redis-exporter on the box, which is why fix 8's alert was delivered as a systemd healthwatch
timer instead of a Prometheus rule.

**Revision:** VID-21 is retitled **"Health & observability — what we actually have"** and covers the
mechanisms that exist and work: `/api/v1/healthz/deep`, `/api/v1/healthz/workers`, the three worker
Redis heartbeats + systemd watchdogs, `talky-healthwatch.timer`, `journalctl` triage, and Sentry.
The Grafana/Prometheus gap is documented in DOC-09 and raised in REV-40.

**Explicitly out of scope:** building a Prometheus/Grafana stack. That is v2 work. Doing it inside a
documentation window would mean documenting something that is 4 days old and unproven — the opposite
of the goal.

---

## R-9 · Review is rolling, not batched at the end

**Original:** tasks 37 and 38 read every doc and watch every recording during days 22–30.

**Problem:** ground rule 5 already requires peer review before a doc is marked complete. Doing it
again at the end is either duplicated work or an admission the first review did not happen. And
finding a doc gap on day 28 leaves no time to fix it.

**Revision:** peer review is a checklist item **inside every DOC and VID ticket**. REV-37/REV-38 on
Day 29 become a short confirmation pass — every deliverable exists, every one has a named reviewer,
nothing was skipped — which is achievable in the time available, unlike reading 12 docs and watching
10 hours of video in two days.

---

## R-10 · Tasks 23 and 24 are refuted, not pending

**Task 23 (fixes 1–3, sample-rate alignment)** — the premise is false. 655 of 655 TTS format probes
over 14 days of production show one uniform configuration (`s16le@16000`); zero mismatch warnings;
the gateway self-reports `internal=16000Hz, wire=8000Hz PCMU`. Forcing rates to 8000 as the document
instructs would **introduce** the distortion it claims to fix. **Dropped, with evidence, permanently.**

**Task 24 (fixes 4–5, barge-in grace)** — the underlying bug was real and is fixed, but not the
document's way. The proposed blind 3-second grace window would suppress legitimate caller interrupts
for the first three seconds of every call. It was replaced with content-aware echo immunity extended
to the agent-first presynth path (commit `33fee92c`). **Done — do not add the grace window.**

---

## R-11 · Fix 13 (PgBouncer) is blocked by a cross-tenant data-exposure hazard, not by effort

`backend/app/workers/dialer_worker.py` uses a **session-level** `SET app.bypass_rls = 'on'`, relying
on it persisting across statements on a pooled connection. Under PgBouncer **transaction** pooling
that assumption breaks — and the state that leaks is *RLS bypass*, on a multi-tenant database.
Worst case is one tenant's connection inheriting bypass and reading another tenant's rows.

**Revision:** TKT-009 (audit every `SET` in the codebase, convert to `SET LOCAL` inside an explicit
transaction — the adapter layer already does this correctly and is the pattern to copy) is a
prerequisite ticket and **TKT-010 will not start until it is green**. If the audit is not clean by
Day 08, PgBouncer is deferred to v2 and recorded as a gap. Shipping it on schedule is not worth a
tenant-isolation incident.

---

## R-12 · Fix 15 (Twilio state) cannot work as described

The document proposes moving `_twilio_sessions` to an external state backend so sessions survive a
restart. They cannot: Twilio's media WebSocket **terminates in this process**. When the process
dies, the socket dies with it, and there is no external channel to reconnect through — unlike
Asterisk/ARI, where the call still exists in Asterisk and can be re-attached.

**Revision:** TKT-011 builds a `TwilioSessionRegistry` whose recovery path marks orphaned `calls`
rows as ended, rather than pretending sessions are recoverable. Note also that
`vonage_bridge._vonage_sessions` has the same class of bug and is **worse** — its `/event` handler
reads the dict across requests. Both are in scope.

---

## R-13 · Fix 19 (CI blocking) was already done — the document audited a dead file

There are two CI files. `ci.yml` at the repo root (260 lines, last touched 2026-06-02) is **not read
by GitHub Actions** — Actions only discovers workflows under `.github/workflows/`. The live file is
`.github/workflows/ci.yml` (399 lines), where `pip-audit` and `bandit` have been blocking since
commit `c4915ac6` on 2026-07-16.

The document's finding was correct about the file it read and wrong about production. Deleting the
decoy is in TKT-013.

---

## R-14 · Task 35 (god-file splits) is three days, not one

5,230 lines across three files, each on the live call path, each with the split constrained by an
existing AST architecture test (`tests/unit/test_no_domain_api_imports.py`) that forbids the domain
layer importing from `app.api`. One file per day, each shipped and gated separately, is the honest
estimate. Bundling three refactors of live call-path code into one commit is how a call-dropping
regression reaches production.

---

## Standing observation — the problem is not missing documentation

There are **552 markdown files** in this repo. `backend/docs/` alone holds day-by-day build logs
from `day_one` to `day_forty_eight` and beyond. What does not exist is anything **canonical and
current**. The closest is `docs/` (5 files, 58–173 lines each) — and it has already drifted:

- `docs/ARCHITECTURE.md:35` says Python 3.11. Production runs **3.12**.
- `docs/ARCHITECTURE.md:89` describes services binding inside docker-compose. Production is **systemd**.
- `docs/ARCHITECTURE.md:95` describes OpenTelemetry traces to Tempo/Jaeger and Prometheus metrics — see R-8.
- `docs/OIDC_INTEGRATION.md` (130 lines) documents an OIDC integration for which no
  `oidc`/`openid`/`authlib` implementation was found under `backend/app`.

**Therefore every Sprint 2 ticket carries the same two obligations:** the new doc lands in `docs/v2/`
as the single canonical source, **and** the superseded documents are marked at the top with a
pointer to it. Adding a thirteenth authority to a pile of 552 solves nothing.
