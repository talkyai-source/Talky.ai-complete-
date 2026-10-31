# Talky.ai — Project Specification & Test Guide

> **Purpose of this file.** A single, self-contained description of the system for
> automated test tooling (TestSprite and similar) and for any engineer or agent
> arriving with no prior context. It covers what the product does, how it is built,
> how to run it, the invariants tests must respect, and — importantly — the
> operations that must **never** be exercised by a test run.
>
> Last verified against the codebase: **2026-08-03**, commit `44d9dd4f`.

---

## 1. What this is

**Talky.ai** (frontend brand: **Talk-Lee**) is a multi-tenant **AI voice dialer**.
A tenant uploads leads, configures an AI agent (persona, script, knowledge base,
voice), and starts a campaign. The platform then places real outbound telephone
calls, and on each answered call an AI agent holds a live, real-time spoken
conversation with the person who picked up — listening, answering questions from a
campaign knowledge base, qualifying the lead, and booking meetings.

It is not a chatbot with a phone number bolted on. The latency budget is a real
constraint: speech-to-text, LLM inference, and text-to-speech run as a streaming
pipeline with barge-in (the caller can interrupt mid-sentence and the agent stops
talking), targeting a sub-second turnaround.

**Business-critical surfaces:** minute-based billing and quota enforcement, per-tenant
data isolation, DNC (do-not-call) suppression, calling-hours compliance, and call
recording with consent disclosure.

---

## 2. Architecture

```
┌─────────────────┐        ┌──────────────────────────────────────┐
│  Next.js app    │  HTTPS │  FastAPI backend                     │
│  (Talk-Leee)    │───────▶│  REST + WebSocket, JWT auth          │
│  Vercel         │   WS   │                                      │
└─────────────────┘        │  ┌────────────────────────────────┐  │
                           │  │ Voice pipeline (per live call) │  │
┌─────────────────┐        │  │  STT ⇄ LLM ⇄ TTS, barge-in     │  │
│  Admin panel    │───────▶│  └────────────────────────────────┘  │
└─────────────────┘        │  ┌────────────────────────────────┐  │
                           │  │ Dialer worker (job queue)      │  │
                           │  │ Voice worker / Reminder worker │  │
                           │  └────────────────────────────────┘  │
                           └───────────┬──────────────┬───────────┘
                                       │              │
                            ┌──────────▼───┐   ┌──────▼────────┐
                            │ PostgreSQL 15│   │  Redis 7      │
                            │ (Docker)     │   │  (Docker)     │
                            └──────────────┘   └───────────────┘
                                       │
                            ┌──────────▼─────────────────────────┐
                            │ Telephony edge                     │
                            │  Asterisk (SIP/PBX) + RTP          │
                            │  C++ voice gateway (media path)    │
                            │  Twilio / Vonage bridges           │
                            └────────────────────────────────────┘
```

**Deployment topology**

| Component | Where it runs |
|---|---|
| Frontend (`Talk-Leee`) | Vercel, auto-deploys from `main` |
| Backend API + workers | Single Linux server, **systemd** units (`talky-api`, `talky-dialer-worker`, `talky-voice-worker`, `talky-reminder-worker`) |
| PostgreSQL 15, Redis 7 | Docker containers on the same server |
| Asterisk / RTP | Same server, host-level |

Backend deploys are `git pull` + `systemctl restart` — **not** container rebuilds.
This matters: code on disk can differ from the code actually running if a restart
was skipped.

---

## 3. Tech stack

**Backend** — Python **≥3.11**
- FastAPI 0.139, Uvicorn, Pydantic v2
- `asyncpg` 0.29 (raw SQL, connection pooling — **no ORM**)
- `redis` 5.0 (job scheduling, leases, rate limits, ephemeral call state)
- `websockets` 13.1, `httpx` 0.28
- Stripe (billing), `passlib[bcrypt]`, `prometheus-client`
- ~39 documented environment variables (`backend/.env.example`)

**Frontend** — `Talk-Leee/`
- Next.js 15.5 (App Router), React 19.2, TypeScript
- TanStack Query 5, Tailwind 4, Radix UI, Framer Motion
- Zod validation, Sentry, SimpleWebAuthn (passkeys)
- Playwright (visual/E2E), `node --test` + `tsx` (unit)

**AI / media providers** (all pluggable via factories)

| Layer | Implementations |
|---|---|
| STT | Deepgram Flux (primary), Deepgram Nova, Deepgram legacy |
| TTS | Cartesia, ElevenLabs (+ voice cloning), Google TTS, Deepgram TTS |
| LLM | Groq, Cerebras, Gemini |
| Telephony | Asterisk (ARI), FreeSWITCH (ESL), Twilio, Vonage, generic SIP, browser media |

**Database** — PostgreSQL 15, **92 tables** in `public`.
Row-Level Security policies exist, but production connects under a **`BYPASSRLS`**
role. **Application-level tenant filtering is therefore the only real isolation
boundary.** Any endpoint that forgets its `tenant_id` predicate is a cross-tenant
data leak. This is the single highest-value area to test.

---

## 4. Repository layout

```
backend/                    FastAPI service + workers
  app/
    api/v1/endpoints/       47 endpoint modules (see §7)
    api/v1/routes.py        router aggregation — the API surface index
    core/                   config, db pool, security (RBAC, tenant isolation)
    domain/
      models/               dataclasses & enums (CallState, CallOutcome, …)
      services/             business logic (dialer, voice_pipeline, billing, …)
      repositories/         data access
      interfaces/           provider protocols
    infrastructure/
      stt/ tts/ llm/        AI provider adapters + factories
      telephony/            Asterisk/FreeSWITCH/Twilio/Vonage adapters
      assistant/            in-app AI assistant + its tools
    workers/                dialer_worker, disposition_policy, …
    services/scripts/       shared computations (tenant_minutes, …)
  tests/
    unit/                   271 files  ← main suite
    security/               25 files   ← tenant isolation, authz, injection
    integration/ chaos/ load/          ← not in the default gate
  database/
    schema/baseline_*.sql   schema baseline
    migrations/             SQL migrations

Talk-Leee/                  Next.js customer app (the main frontend)
Admin/                      Admin panel (separate frontend + docs)
services/voice-gateway-cpp/ C++ media gateway (RTP path)
telephony/                  Asterisk / Kamailio / OpenSIPS / rtpengine configs
docs/sessions/              engineering session records — real fix history
tickets/                    active work board
deploy/ infra/ scripts/     deployment tooling
```

---

## 5. Running it locally

### Infrastructure
```bash
docker compose up -d postgres redis
```

### Backend
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env          # then fill in the values — see §11
uvicorn app.main:app --reload --port 8000
```

- API base: `http://localhost:8000`
- Interactive docs: `http://localhost:8000/docs`
- Health: `GET /health` · Deep health: `GET /api/v1/healthz/deep`
  → `{"ready":true,"db":"ok","redis":"ok"}`

> **Note the path.** The deep health check is under the `/api/v1` prefix.
> `/healthz` and `/readyz` at the root return **404** — do not use them as liveness
> probes.

### Frontend
```bash
cd Talk-Leee
npm install
npm run dev          # http://localhost:3000
npm run typecheck    # tsc --noEmit
npm run lint
npm run test         # node --test
npm run test:visual  # Playwright
```

---

## 6. Authentication, roles & tenancy

**Auth:** JWT bearer tokens. Optional MFA (TOTP) and WebAuthn passkeys. Refresh
tokens and server-side session records are both tracked.

**`CurrentUser`** (`backend/app/api/v1/dependencies.py`) carries:
`id`, `email`, `tenant_id`, `role`, `name`, `business_name`, `minutes_remaining`,
plus a cached effective-permission set.

**Role hierarchy** (highest → lowest):

| Role | Scope |
|---|---|
| `platform_admin` | Everything, all tenants |
| `partner_admin` | Multiple tenants (reseller / white-label) |
| `tenant_admin` | Full access within one tenant |
| `user` | Standard user |
| `readonly` | View only |

**Permissions** are granular strings in `resource:action` form —
`campaigns:create`, `calls:delete`, `billing:update`, `platform:admin`, etc.
Enforced with `Depends(require_permission(Permission.X))`.

### Tenancy rules that tests must verify

1. Every tenant-scoped query **must** filter on `tenant_id` in the application
   layer. RLS is bypassed in production and will not save you.
2. A client-supplied `tenant_id` parameter must **never** override the caller's own
   tenant. The correct pattern is validate-then-403, not
   `tenant_id or current_user.tenant_id`. This exact defect has shipped before, on
   both read and write paths.
3. Cross-tenant access attempts should return **403**, not empty results — silent
   empties hide the bug.

---

## 7. Core domain model & call lifecycle

```
Campaign ──▶ Leads ──▶ dialer_jobs ──▶ calls ──▶ outcome + transcript + recording
   │                        │              │
   │                     Redis queue    live voice pipeline
   │                     + retries      (STT ⇄ LLM ⇄ TTS)
   └── campaign_knowledge_nodes (RAG-ish knowledge base)
```

### Call statuses — `CallState` (`domain/services/call_status.py`)

`queued` → `dialing` → `ringing` → `answered` → `in_call` → `ended`
plus legacy values `initiated`, `completed`, `failed`.

> ### ⚠️ The most important gotcha in this codebase
> **In production, `calls.status` has only ever held two values: `completed` and
> `ended`.**
>
> `answered`, `in_progress`, `busy`, `no_answer` are **outcomes**, not statuses.
> Filtering `status IN ('answered','completed','in_progress')` matches two values
> that have never existed and silently excludes `ended` — the majority of all
> calls. That mistake has caused multiple production bugs (dashboards reading
> near-zero, tenants under-billed). If you see a status filter naming an outcome
> value, it is a bug.
>
> Use the shared vocabulary — `dialer/job_states.py` and
> `domain/services/call_outcomes.py` — never inline literals.

### Call outcomes — `CallOutcome`

Connected: `answered`, `customer_hung_up`, `agent_hung_up`, `goal_achieved`,
`goal_not_achieved`
Not connected: `no_answer`, `busy`, `rejected`, `unreachable`, `network_failure`,
`failed`, `cancelled`, `voicemail`

Classification lives in **`app/domain/services/call_outcomes.py`** —
`ANSWERED_OUTCOMES` / `FAILED_OUTCOMES` / `GOAL_OUTCOMES`. It is built *from* the
`CallOutcome` enum and a test asserts the partition stays **total**, so a new enum
member cannot silently count as neither connected nor failed.

### Billable minutes — one definition only

`SUM(calls.duration_seconds)` for the current calendar month, **with no
disposition filter**, versus `tenants.minutes_allocated`
(`domain/services/minutes_quota.py::compute_minutes_status`).

- No filter is deliberate: an unconnected call has no duration, so
  `duration_seconds` filters itself. `voicemail` is a *failed* disposition but is
  real, chargeable airtime.
- `minutes_allocated <= 0` means **unlimited**.
- `tenants.minutes_used` and `usage_records` are **dead** — nothing writes them.
  Never read them.
- The gate that blocks calls and every figure shown to the user must come from
  this one function. Two definitions means a tenant gets blocked by a number
  their invoice does not show.

### Dialer job states

`pending`, `queued`, `retry_scheduled`, `processing`, `calling` are covered by the
partial unique index `uq_dialer_jobs_one_active_per_lead` — **one active job per
lead**. Writing a terminal status while a live retry copy exists in Redis opens a
double-dial window. Terminal states include `completed`, `failed`, `skipped`,
`blocked`, `non_retryable`.

### Voice pipeline (per live call)

Streaming STT (Deepgram Flux) → turn detection → prompt assembly (persona +
knowledge + live state) → streaming LLM → streaming TTS → RTP egress, with
**barge-in** cancelling the in-flight turn. Guards include a self-echo filter (the
agent must not transcribe its own audio as caller speech) and an action-envelope
guard (internal JSON control messages must never be spoken aloud).

---

## 8. API surface

Base prefix: **`/api/v1`**. Routers are registered in
`backend/app/api/v1/routes.py` — read that file for the authoritative list.

| Group | Routers |
|---|---|
| Auth & identity | `auth`, `mfa`, `passkeys`, `sessions`, `rbac` |
| Campaigns | `campaigns`, `campaign_knowledge`, `contacts`, `contact_lists` |
| Calls | `calls`, `recordings`, `stream_events` |
| Insight | `dashboard`, `analytics`, `alerts` |
| Billing | `billing`, `plans`, `clients` |
| AI assistant | `assistant_config`, `assistant_ws`, `assistant_voice_ws`, `ask_ai_ws`, `campaign_test_ws`, `ai_options` |
| Telephony | `telephony_bridge`, `telephony_sip`, `telephony_providers`, `telephony_runtime`, `telephony_concurrency`, `twilio_bridge`, `vonage_bridge`, `tenant_phone_numbers` |
| Compliance | `dnc`, `call_limits`, `abuse_monitoring`, `suspensions`, `blocked_entities` |
| Platform admin | `admin`, `audit_logs`, `security_events`, `secrets`, `emergency_access`, `api_keys`, `rate_limits` |
| Integrations | `connectors`, `webhooks`, `webhooks_secure`, `webhooks_admin`, `meetings`, `email` |

**WebSocket endpoints** exist for the in-app assistant (text + voice), the
"ask AI" panel, and per-campaign agent testing. They require the same JWT auth as
REST.

---

## 9. Frontend routes (`Talk-Leee/src/app`)

**Authenticated app:** `/dashboard`, `/campaigns` (+ `/new`, `/[id]`, `/[id]/edit`),
`/calls` (+ `/[id]`), `/recordings`, `/contacts`, `/analytics`, `/billing`
(+ `/plans`, `/invoices`, `/invoices/[id]`), `/settings`, `/notifications`,
`/meetings`, `/reminders`, `/email`, `/connectors`, `/ai-options`, `/ai-voices`,
`/assistant` (+ `/actions`, `/meetings`, `/reminders`)

**Auth:** `/auth/login`, `/auth/register`, `/auth/forgot-password`, `/auth/callback`

**Admin:** `/admin` and `/admin/{api-keys,audit-logs,billing,rate-limiting,secrets,voice-security,webhooks,abuse-detection}`

**White-label:** `/white-label/[partner]/{dashboard,analytics,billing,tenants,preview}`

**Marketing / static:** `/`, `/industries/*`, `/use-cases/*`, `/privacy`, `/terms`, `/403`

---

## 10. Testing

### Commands

```bash
# Backend — the standard gate
cd backend
python -m pytest tests/unit tests/security -q --ignore=tests/security/test_passkeys.py

# Import smoke test (catches circular imports — a recurring failure mode)
python -c "import app.main"

# Frontend
cd Talk-Leee
npm run typecheck && npm run lint && npm run test
npm run test:visual        # Playwright
```

**Current gate status: 4,724 passed, 6 skipped, 0 failed.**

`tests/security/test_passkeys.py` is excluded — pre-existing stale import,
out of scope. `tests/integration`, `tests/chaos` and `tests/load` need live
infrastructure and are not part of the default gate.

### Conventions worth knowing before writing tests here

- **A number of tests assert on source code shape**, not behaviour (for example:
  "no module that writes the `calls` table may set an outcome literal outside the
  classified sets"). Use the helper at **`tests/unit/_source_scan.py`**, which
  strips comments and docstrings first. A source scan that reads prose will
  re-detect the very bug its own comment documents — this has happened.
- **Slice a method by `async def <name>`, never the bare name.** Dispatch-table
  entries and docstring mentions hundreds of lines away match first, and the test
  then asserts against the wrong region — it will fail on correct code, or pass on
  broken code.
- **Time-dependent tests must pin the day of week.** Calling-hours rules default to
  Mon–Fri; six tests once failed every weekend because they opened the *hours* but
  inherited the weekday default.
- **Fail-open vs fail-closed is deliberate and differs per subsystem.** Quota and
  metering lookups fail *open* (a metering glitch must never strand a legitimate
  call). Recording consent fails *closed* (no disclosure ⇒ discard the audio).
  Do not "fix" one to match the other.

### High-value areas to test

1. **Tenant isolation** — every list/detail/stats endpoint, with a second tenant's
   IDs. Both read and write paths. Expect 403.
2. **Authorization** — each role against each permission-gated endpoint;
   privilege-escalation attempts via role assignment.
3. **Quota & billing consistency** — the number that blocks a call must equal the
   number shown on the dashboard, `/auth/me`, `/billing/subscription` and
   `/billing/usage`. Historically these disagreed.
4. **Call lifecycle correctness** — status/outcome transitions, terminal-state
   handling, one-active-job-per-lead, retry scheduling.
5. **Compliance gates** — DNC suppression, calling hours, consent disclosure,
   caller-ID verification. These must never be cacheable or bypassable.
6. **Input validation** — E.164 phone numbers (NANP `+1` is exactly 11 digits),
   prompt-injection resistance on knowledge/script fields, upload limits.
7. **Idempotency** — webhooks (`processed_webhook_events`), telephony origination
   (`tenant_telephony_idempotency`).

---

## 11. Ground rules for any automated test run

### 🔴 Never do these

- **Never place a real outbound call**, and never start a campaign "to test".
  Calls cost money, ring real people, and carry legal exposure under TCPA/Ofcom.
- **Never delete data** — no dropping or truncating tables, no removing rows,
  recordings, campaigns, leads or logs.
- **Never cache DNC results.** A suppressed number must be re-checked every time.
- **Never disable or stub a compliance gate** to make a test pass.
- **Never run a migration** as part of a test run.
- **Never commit or print secrets.** This repository is **public**. Credentials,
  API keys, tenant UUIDs, customer phone numbers and per-tenant usage figures must
  not appear in code, comments, commit messages, test fixtures or test output.
- **Never restart production services** as part of a test.

### ✅ Do this instead

- Point tests at a **local** stack (`docker compose up -d postgres redis`) seeded
  with synthetic data.
- Use obviously fake phone numbers and clearly-labelled test tenants.
- Treat the production database as **read-only**.
- Assert on the shared vocabulary modules rather than hardcoding status/outcome
  strings — they are the thing most likely to drift.

---

## 12. Known open issues

These are **already known**. Reporting them again as new findings is noise; verifying
they stay fixed is useful.

| Area | Issue |
|---|---|
| Security | Carrier SIP credentials are present in `setup-asterisk.sh` in the history of this **public** repo. Rotation + history rewrite pending an owner decision. |
| Metrics | Historical `agent_hung_up` rows are mislabelled — the code is fixed (`44d9dd4f`) but the existing rows were **not** backfilled, so historical connect rates read high. |
| Billing | `record_usage()` has no callers; `usage_records` and `tenants.minutes_used` are dead. Minutes come from the `calls` table only. |
| Dialer | Reapers bypass the outcome pipeline and can re-dial someone who just had a full conversation. |
| Compliance | Per-lead timezone is not applied — calls are windowed against the tenant's timezone, not the callee's. Auto-DNC suppression does not propagate to `dnc_entries`, so a suppressed number is still dialable from another campaign. |
| Retention | `CLEANUP_DRY_RUN=true`; the promised 90-day retention is not enforced. |
| Latency | Prompt prefix-caching is defeated by a per-turn block at position 0, so cache hits are structurally zero. |
| Observability | The C++ gateway's `/stats` sums only *live* sessions, so a finished call's counters vanish. Reading 0 between calls is an artefact, not a fault. |

---

## 13. Glossary

| Term | Meaning |
|---|---|
| **Tenant** | A customer organisation. The isolation boundary for all data. |
| **Campaign** | A configured calling programme: leads + AI agent + schedule + trunk. |
| **Lead** | A contact to be called. |
| **Dialer job** | One queued attempt to call one lead. At most one active per lead. |
| **Call** | A row in `calls`; one origination attempt with its status, outcome, duration, transcript and recording. |
| **Outcome** | What happened on the call (answered / no_answer / voicemail / …). Distinct from status. |
| **Trunk** | A SIP route to a carrier. Snapshotted per campaign at start. |
| **Caller ID** | A verified originating number. Unverified ⇒ the call is refused. |
| **DNC** | Do Not Call suppression list. |
| **Barge-in** | The caller interrupting the agent mid-utterance; cancels the in-flight turn. |
| **Turn** | One caller-utterance → agent-response cycle in the voice pipeline. |
| **Knowledge node** | A retrievable fact block attached to a campaign. |
| **Disposition** | The classified result of a call, used for retry decisions and reporting. |

---

## 14. Further reading in-repo

| Path | What's there |
|---|---|
| `backend/app/api/v1/routes.py` | Authoritative API surface |
| `backend/app/domain/services/call_status.py` | Call state machine + outcome enum |
| `backend/app/domain/services/call_outcomes.py` | Connected/failed classification |
| `backend/app/domain/services/minutes_quota.py` | The one definition of billable minutes |
| `backend/app/core/security/rbac.py` | Roles and permissions |
| `backend/app/core/security/tenant_isolation.py` | Tenant scoping helpers |
| `docs/sessions/` | Engineering session records — real bugs, causes and fixes |
| `tickets/` | Active work board |
| `AGENTS.md`, `rules.md`, `users-roles.md` | Existing working agreements |
