# Architecture

High-level map of how Talky.ai is put together. For deploy steps see
[DEPLOYMENT.md](./DEPLOYMENT.md). For incident playbooks see
[RUNBOOK.md](./RUNBOOK.md).

## System overview

Talky.ai is an AI voice dialer: it places & receives phone calls, runs the
audio through speech-to-text, an LLM agent, and text-to-speech, and writes
results back to a CRM.

```
            ┌─────────────┐                     ┌──────────────┐
   user ──► │  Frontend   │ ── HTTPS / WSS ──►  │  Backend API │
            │  (Next.js)  │                     │  (FastAPI)   │
            └─────────────┘                     └──────┬───────┘
                                                       │
        ┌──────────────────────┬───────────────────────┼─────────────────────┐
        │                      │                       │                     │
        ▼                      ▼                       ▼                     ▼
  ┌───────────┐          ┌───────────┐          ┌────────────┐         ┌──────────┐
  │ PostgreSQL│          │   Redis   │          │  Telephony │         │ AI provs │
  │   (RLS)   │          │  (cache,  │          │  Asterisk  │         │ Deepgram │
  │           │          │  rate-lim)│          │     +      │         │ Groq     │
  └───────────┘          └───────────┘          │ C++ voice  │         │ Cartesia │
                                                │  gateway   │         │ Gemini   │
                                                └────────────┘         └──────────┘
```

Asterisk is the only SIP/media process in production. FreeSWITCH, OpenSIPS,
Kamailio and rtpengine are modelled under `telephony/` but are **not deployed**.

## Process boundaries

| Component | Tech | Purpose |
|---|---|---|
| Backend API | FastAPI / Python (3.12.3 in production) | REST + WebSocket; orchestrates calls, agents, CRM |
| Frontend | Next.js (Talk-Leee) + Vite (Admin) | Operator UI; deploys to Vercel from `main` |
| Postgres 15 | with Row-Level Security | Tenant-isolated state |
| Redis 7 | AOF-persistent | Cache, rate-limits, session store |
| Telephony media | see the table below | SIP signalling + RTP media |
| Voice gateway | C++ (`services/voice-gateway-cpp`) | Media gateway on `127.0.0.1:18080` |
| AI providers | external HTTPS | Deepgram (STT), Groq / Gemini (LLM), Cartesia / Google TTS |

### Telephony: what is deployed vs. what is only modelled

The repository carries configuration for a five-component telephony stack.
**Production runs Asterisk and nothing else** — verified on the box with
`pgrep -a -f 'asterisk|opensips|kamailio|rtpengine|freeswitch'`, which returns
only `asterisk` and its `astcanary` watchdog helper. Treat the rest of
`telephony/` as design scaffold.

| Component | Status |
|---|---|
| **Asterisk** | **Deployed in production** — the only SIP/media process on the box |
| FreeSWITCH | Modelled in repo, **not deployed in production** (Asterisk-only) |
| OpenSIPS | Modelled in repo, **not deployed in production** (Asterisk-only) |
| Kamailio | Modelled in repo, **not deployed in production** (Asterisk-only) |
| rtpengine | Modelled in repo, **not deployed in production** (Asterisk-only) |

## How it runs in production

A single Hetzner bare-metal host, hybrid by layer:

| Layer | Runtime |
|---|---|
| API, dialer/voice/reminder workers, C++ gateway | **systemd units** from the git checkout at `/opt/talky` (see `backend/systemd/`) |
| Postgres 15, Redis 7 | **docker compose**, project `talky`, from `/opt/talky/docker-compose.yml` |
| Talk-Leee + Admin frontends | **Vercel**, auto-deploy from `main` (excluded from the prod checkout) |

There is no Kubernetes and no running Prometheus/Grafana on the box. See
[DEPLOYMENT.md](./DEPLOYMENT.md) and [RUNBOOK.md](./RUNBOOK.md).

## Code layout (backend)

```
backend/app/
├── api/v1/endpoints/   # HTTP + WebSocket routes
├── core/               # cross-cutting: config, db, security, telemetry
├── domain/             # business rules (call flow, rate limits, RBAC)
├── services/           # use cases, orchestrators
├── infrastructure/     # adapters: DB repos, telephony, AI providers
├── workers/            # background tasks
└── main.py             # FastAPI app, lifespan, middleware wiring
```

The split follows a hexagonal-ish pattern: `domain/` has no I/O, `infrastructure/`
talks to the outside world, `services/` glues them together, `api/` is the HTTP edge.

## Middleware stack (outermost → innermost)

Order matters; see [`app/core/app_bootstrap.py`](../backend/app/core/app_bootstrap.py).

1. **RequestIdMiddleware** — generates / propagates `X-Request-ID`, exposes it via contextvar
2. **SecurityHeadersMiddleware** — CSP, HSTS (prod-only), X-Frame-Options, etc.
3. **CORSMiddleware** — restricts origins per `settings.allowed_origins`
4. **TenantMiddleware** — extracts tenant ID from JWT, sets Postgres RLS session var
5. **SessionSecurityMiddleware** — session validity, device fingerprinting
6. **APISecurityMiddleware** — global rate limiting (slowapi), UA filtering

## Data flow: a placed call

1. Operator hits `POST /api/v1/calls/start` from the UI
2. Backend resolves tenant, checks rate limits, persists a `Call` row
3. Backend signals the telephony adapter (Asterisk, via ARI) to dial
4. Once answered, RTP media flows through the C++ voice gateway; backend opens
   a WebSocket bridge for the audio frames
5. Frames stream → Deepgram STT → transcript chunks
6. Transcript chunks → Groq/Gemini LLM agent (LangGraph state machine)
7. LLM response → Cartesia/Google TTS → audio frames back to caller
8. Throughout, telemetry spans land in OTLP (Tempo/Jaeger), metrics in Prometheus,
   logs are structured JSON tagged with `request_id` + `tenant_id` + `call_id`

## Security model

- **AuthN**: JWT (access + refresh), optional WebAuthn / TOTP
- **AuthZ**: RBAC (roles in `app/core/security/rbac.py`)
- **Tenant isolation**: Postgres RLS enforced via `SET LOCAL app.current_tenant_id`
  in every request (see `tenant_middleware.py`)
- **Secrets at rest**: KMS-wrapped via `app/core/kms.py` master key
- **Network**: services bind to `127.0.0.1` in compose; only the backend port is public
- **Headers**: strict CSP / HSTS / X-Frame-Options / Permissions-Policy on every response

## Observability

- **Logs**: structured, every line tagged with `request_id` (correlation ID)
- **Traces**: OpenTelemetry → OTLP/gRPC → Tempo/Jaeger; auto-instruments
  FastAPI, asyncpg, Redis
- **Metrics**: Prometheus `/metrics` endpoint (auth-gated)
- **Health**:
  - `/health` — liveness (Docker/k8s probe)
  - `/api/v1/health` — readiness with dependency checks
  - `/api/v1/admin/health/{detailed,workers,queues,database}` — deep probes
- **Errors**: Sentry SDK initialised in `sentry_init.py`

## Configuration

- YAML per-environment in `backend/config/{development,production}.yaml`
- Secrets via env vars; `.env` for local dev (gitignored)
- Pydantic Settings (`app/core/config.py`) is the single read path
- `prod_gate.py` halts boot if production-required values are missing/weak

## Testing

- `tests/integration/` — DB + Redis spun up in CI via service containers
- `tests/security/` — auth, RLS, rate limiting
- pytest-asyncio mode = auto; coverage threshold enforced in CI

## Where to look when…

| Symptom | First file to read |
|---|---|
| Auth bug | `app/api/v1/endpoints/auth.py`, `app/core/security/` |
| Tenant data leak | `app/core/tenant_middleware.py`, `app/core/tenant_rls.py` |
| Call won't connect | `app/infrastructure/telephony/`, `app/domain/services/telephony_*` |
| LLM giving wrong answer | `app/services/agent/`, LangGraph state machine |
| Rate limit too tight/loose | `app/core/api_security_middleware.py`, `app/api/v1/endpoints/auth.py:limiter` |
| Slow query | check OTel span; `app/core/db.py` for pool config |
