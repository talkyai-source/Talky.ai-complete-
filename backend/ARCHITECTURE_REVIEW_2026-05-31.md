# Talky.ai Architecture Review

**Date**: 2026-05-31
**Reviewer perspective**: senior engineer with telephony + SaaS scale experience.
**Scope**: on-disk layout, hot-path code, state ownership, deployment model, and the C++ media gateway.

---

## Scope of the codebase

| | Backend (Python/FastAPI) | Frontend (Next.js) | Voice Gateway (C++) |
|---|---|---|---|
| **Files** | 377 .py | 281 .ts/.tsx | 7 source files |
| **LOC** | ~100,000 | ~60,000 | ~2,700 |
| **Tests** | 1,856 test files | 342 test files | 1 test file (182 LOC) |
| **Production state** | Live on `144.76.17.150` | Live on Vercel | Live as `talky-voice-gateway` systemd unit |

## Overall verdict

- **Maturity**: 6.5 / 10 — production-real but not production-hardened.
- **Scalability ceiling, today, as-is**: ~30–50 concurrent calls per pod, single dialer worker, single Asterisk. Fine for a pilot. Will hit walls before 500 cc/pod or multi-region.
- **Architecture cleanliness**: 6 / 10 — the *intent* is hexagonal/clean architecture, the *execution* leaks in critical places.

---

# What's done right

These are senior-quality choices and they deserve to be named:

1. **Layered architecture is the right shape.** `api / domain / infrastructure / core / workers` with `domain/interfaces/*.py` defining contracts (`CallControlAdapter`, `TelephonyProviderAdapter`). This is textbook hexagonal. Adapters for Asterisk, FreeSWITCH, Twilio, Vonage are first-class — that's how you survive carrier migrations.
2. **C++ for the RTP hot path is the correct choice.** Python is the wrong tool for 20 ms-paced packet I/O. The fact that you have a C++ gateway at all — with AddressSanitizer + ThreadSanitizer build modes — is a senior decision a lot of teams don't make until they get burned.
3. **Security baseline is solid.** RLS, audit logger with tamper-evident chain hashes, JWT + cookie-only mode, CSRF middleware, MFA, passkeys, tenant attestation (STIR/SHAKEN scaffolding), Redis-based global concurrency leasing, per-tenant call guards.
4. **Comments lean WHY, not WHAT.** That's senior. (Track 2's `retry_policy.py` is a good example.)
5. **OpenTelemetry tracing is wired in.** Every DB query emits a span. More than most startups bother with.
6. **Operational scaffolding exists**: systemd units, Grafana, Vector for log shipping, alertmanager config.
7. **C++ gateway has unit tests** with sanitizer instrumentation. Few teams do this.
8. **Multiple PBX adapters** behind a single interface — the carrier-vendor choice is reversible.

---

# Critical issues, severity-ordered

## 🔴 P0 — Showstoppers for any scale beyond a single pod

### 1. Per-call state lives in module-level singletons in one process

`backend/app/api/v1/endpoints/telephony_bridge.py` carries **six** module-level mutable dicts/sets:

```python
_telephony_sessions: dict[str, object] = {}        # active calls
_gateway_session_to_call_id: dict[str, str] = {}
_ringing_warmups: dict[str, tuple[...]] = {}
_early_audio_buffers: dict[str, list[bytes]] = {}
_ringing_warmup_created_at: dict[str, float] = {}
_adapter: Optional[CallControlAdapter] = None
```

uvicorn runs with `--workers 2`. Each process has its own copy. A StasisStart callback that lands on worker A cannot find a session created on worker B → silent failure. Today, Asterisk happens to hold the ARI WebSocket open to one process so it mostly works — but the moment uvicorn rolls a process (deploy, restart, OOM), every in-flight call is dropped.

**Why this matters for scale**: any horizontal scaling — second pod, blue/green deploy, rolling restart — drops live calls. Effectively single-process today.

**Fix**: move the state to Redis (`_telephony_sessions` becomes `HSET sessions:<call_id>`). The `app/domain/services/global_concurrency.py` module already shows the pattern.

### 2. Single dialer worker = SPOF for the entire calling business

`talky-dialer-worker.service` is one systemd unit, one process. It ran **completely dead for days** (the `async_generator` bug) with no alarm. Every minute it logged "ERROR Failed to get active tenants" and nobody noticed.

The retry-policy improvement we just shipped doesn't help if the worker itself is down.

**Fix path**: shard by `tenant_id` range across N worker instances; use Redis Streams consumer groups (already designed — `app/domain/services/queue_service.py` mentions a streams backend) instead of the current sorted-set polling.

### 3. God-files in the call hot path

| File | LOC | Why it's a problem |
|---|---|---|
| `domain/services/voice_pipeline_service.py` | **1,923** | Coordinates STT+LLM+TTS+VAD+barge-in. Any change risks the whole audio path. |
| `domain/services/call_guard.py` | 1,268 | DNC + abuse + rate limit + per-tenant policy all in one. |
| `domain/services/voice_orchestrator.py` | 1,084 | Session lifecycle + provider routing + warmup. |
| `domain/services/telephony/lifecycle.py` | 1,083 | Ringing → answer → audio → hangup. Already split once. |
| `api/v1/endpoints/telephony_bridge.py` | 1,046 | The owner of the global state singletons above. |

Any Python file > 800 LOC typically contains 3-5 conceptual responsibilities. The reason we kept shipping one-line fixes to the bridge in this session is that these files are too big for confident edits.

**Fix**: each one decomposes into 3-4 modules along the existing seams.

### 4. Module-level lazy imports as a circular-dependency mask

`domain/services/telephony/lifecycle.py`:

```python
def _bridge():
    from app.api.v1.endpoints import telephony_bridge
    return telephony_bridge
```

The `domain` layer should not need to call into `api`. This is the single most expensive piece of architectural debt because it makes a clean refactor impossible without moving state out of `telephony_bridge.py` first.

**Fix**: extract a `TelephonySessionRegistry` service into `app/domain/services/`. Both the bridge endpoint and lifecycle hooks read from it. State ownership becomes one-way.

---

## 🟠 P1 — Will hurt within 3 months

### 5. Two migration systems coexisting

```
backend/Alembic/versions/      ← 5 files, 0001…0005
backend/database/migrations/   ← 28 raw SQL files with date prefixes
```

The Track 2 schema change shipped as raw SQL because that's the recent convention. Alembic exists but isn't being used. **Pick one.** Mixed migration history is how you end up unable to spin up a fresh prod environment because nobody remembers which script ran when.

### 6. 78 `get_container()` references = service locator anti-pattern

```python
from app.core.container import get_container
container = get_container()
redis_client = getattr(container, "redis", None)
```

Hidden across 78 call sites. Looks like DI, isn't — untestable without monkey-patching the global, hides which services any function actually depends on.

**Senior alternative**: FastAPI's `Depends()` + an explicit `ServiceContext` dataclass passed down. Pay this debt before the codebase doubles.

### 7. 781 raw `conn.fetch / execute / acquire` calls — no repository layer

Every endpoint and service module owns its own SQL. There is no `CallRepository`, no `LeadRepository`, no `CampaignRepository`. This is why:
- The RLS GUC bug we just fixed in `is_verified_for_tenant` likely exists in ~40 other places (the T3 sweep we deferred).
- The UUID-vs-string bug we fixed earlier existed across `campaigns.py`, `secrets.py`, `audit_logs.py`, `security_events.py` separately.
- Every endpoint hand-rolls "filter by tenant_id" in raw SQL with `apply_tenant_filter`.

**Fix path**: introduce `app/domain/repositories/` (the folder already exists, empty), encapsulate the SET LOCAL + tenant filter + UUID coercion pattern once. New code uses repos; old code migrates lazily.

### 8. No connection multiplexing for upstream AI providers

Every call opens its own Deepgram WebSocket, its own Cartesia WebSocket, its own Gemini/Groq HTTP/2 session. At 100 cc that's 300+ outbound WebSockets + N HTTP connections. Provider rate limits and TCP exhaustion will bite before CPU does.

**Fix**: connection pool + multiplex per provider. Most providers support this; Deepgram in particular has a multi-stream endpoint.

### 9. No leader election for watchdogs

`_session_watchdog` in `lifecycle.py` runs on every pod. With 2 workers, both watchdogs scan the same `_telephony_sessions` dict (one each) — fine today because of issue #1, but the moment you fix #1 (move to Redis), every pod's watchdog will race on the same global state.

**Fix**: Redis distributed lock or simple Postgres advisory lock; only one pod is "the watchdog".

---

## 🟡 P2 — Tech debt, fix opportunistically

### 10. Type-annotation coverage 54%
1,410 / 2,584 functions have return types. Adding `mypy --strict` would catch a meaningful chunk of bugs. The two we just fixed in this session — `os` not imported in `passkeys.py`, missing `import logging` in `audit_logger.py` — would have been caught by `pyflakes`/`ruff` running in CI.

### 11. 15 commented-out `await`/`async def` lines
Minor, but they say "we don't trust we can delete this code". Either remove or document why.

### 12. C++ gateway has 1 test file for 2,700 LOC of network code
ThreadSanitizer is enabled but only matters if there are tests that exercise concurrency. RTP I/O in C++ deserves >10 test files.

### 13. No staging environment
Every change in this session went straight from dev disk → prod via scp. That's how you discover the Origin-CSRF / caller_id / URL-encoding chain of bugs the hard way.

### 14. `docker-compose.yml` exists but isn't used in prod
Production is on systemd. Pick one deployment story. Mixed messaging will burn an SRE eventually.

---

# C++ Voice Gateway — specific assessment

| Aspect | Assessment |
|---|---|
| **Scope choice** | ✅ Correct. RTP + session control only — doesn't try to do STT/TTS itself. |
| **C++20** | ✅ Modern. |
| **Sanitizer builds** (ASan/TSan) | ✅ Senior practice. |
| **Module decomposition** | ⚠️ `session.cpp` is 956 LOC, `http_server.cpp` is 946 LOC. Both are border-line god files for C++ scope. |
| **Test coverage** | 🔴 One test file, 182 LOC, for 2,700 LOC of networking code. Concurrent session add/remove + RTP packet ordering needs fuzz/property tests. |
| **Build system** | CMake — standard. Dockerfile present. |
| **Scalability inside the C++ side** | `SessionRegistry` is in-process — same single-pod constraint as Python. For HA you'd need an external session store or sticky routing from Asterisk. |
| **Observability** | No Prometheus metrics exported from the C++ side. `/stats` exists as JSON which is OK but not Prometheus-native. |
| **Memory safety** | Sanitizer-built but no AFL/libFuzzer harness visible. Network-facing C++ should fuzz. |

---

# How calls are actually made and handled — review

The flow today:

```
Frontend → POST /campaigns/{id}/start
       ↓
campaign_service.start_campaign → enqueue dialer_jobs into Redis sorted-set
       ↓
talky-dialer-worker polls Redis every second
       ↓
_make_call: HTTP POST /sip/telephony/call to its OWN backend (!)
       ↓
telephony_bridge.start_telephony / originate via CallControlAdapter
       ↓
AsteriskAdapter → ARI HTTP originate → PJSIP/X@blazedigitel-endpoint
       ↓
Asterisk ARI WebSocket → StasisStart event → _on_ringing → _on_new_call
       ↓
Lifecycle wires up Voice Gateway (C++) for RTP I/O
       ↓
Voice pipeline: STT (Deepgram) ↔ LLM (Gemini) ↔ TTS (Cartesia)
```

### Critical findings about this flow

1. **The dialer worker calls its own backend over HTTP.** That's why we just hit the CSRF/Origin and URL-encoding bugs. **A worker hitting the same monolith over HTTP is an anti-pattern.** The right design: the worker imports and calls the same service class the endpoint uses. HTTP between coupled components adds latency, failure modes, and security gates that shouldn't be there. This single decision created 3 of the bugs in this session.

2. **Asterisk ARI WebSocket is single-process-affine.** ARI events go to whichever process opened the WS. With `--workers 2`, only one of them gets ARI events — the other is dead weight for telephony.

3. **No per-call leader election.** If two dialer worker instances ever existed, they'd both pull the same job from the Redis sorted-set (`ZRANGEBYSCORE` + `ZREM` has a brief race window without `WATCH`/`MULTI`).

4. **The Voice Gateway C++ ↔ Python contract** is HTTP control + RTP data. Fine for a single host. Across hosts the C++ side needs service discovery for the Python media gateway endpoint. Not designed yet.

5. **Ringing-phase pre-warm is a senior touch.** Pre-opening STT/TTS during ring saves the user's first "hello" — that's good. But it depends on `_ringing_warmups` (module global) — bug #1 again.

---

# Scalability ceiling, concretely

Numbers, not hand-waving.

| Bottleneck | Ceiling | Why |
|---|---|---|
| Module-level state in one process | **single-pod** | Process restart drops every live call |
| Single dialer worker | **~10 calls/sec dial rate** | One process polling Redis once/sec |
| Single Asterisk instance | **~100–300 concurrent calls** | PJSIP endpoint, ARI WS queue |
| C++ session registry | **~thousands per host** | In-memory, single host |
| Postgres `calls` table no partitioning | **~50M rows before queries slow** | Will need range partitioning by created_at |
| STT/TTS per-call WS | **rate-limited by provider** | Likely 100-500 cc before throttling |

**Honest summary**: this stack will handle 30–80 simultaneous calls on the current single-pod setup. To get to 500+ cc on a multi-region SaaS, you'd refactor items 1–4 above. That refactor is real — not "rewrite the world", but several weeks of focused work.

---

# Recommended hardening roadmap (in dependency order)

1. **Move telephony module state to Redis** (`_telephony_sessions`, `_ringing_warmups`, etc.). Single most impactful change.
2. **Decompose `telephony_bridge.py` and `voice_pipeline_service.py`** along their existing seams. `lifecycle.py` exists — finish the job.
3. **Replace HTTP-to-self in dialer worker with direct service calls.** Kill the entire class of CSRF/Origin/URL-encoding bugs we just chased.
4. **Pick ONE migration system.** Either Alembic or raw SQL files. Stop mixing.
5. **Introduce a `Repositories/` layer** to encapsulate RLS + tenant filtering. Migrate the 12 known offenders first.
6. **Replace `get_container()` with explicit `Depends(ServiceContext)`.** Hide behind a deprecation warning in `container.py` for the first migration.
7. **Shard dialer workers** by tenant_id mod N. Use Redis Streams consumer groups (already partially designed).
8. **Set up a staging environment** that mirrors prod. The current "fly fix to prod over scp" loop is unsustainable past a 2-person team.
9. **Add CI lint** (`ruff` + `mypy --strict`). The `NameError: os not defined` was a free catch.
10. **Add fuzz tests to the C++ gateway** before someone sends a malformed RTP packet.

---

# Final blunt assessment

This codebase has the shape of a senior team building something real, with one or two engineers carrying most of the architecture work, under pressure. The good decisions are senior (C++ hot path, hexagonal interfaces, audit hashes, sanitizer builds). The bad decisions are tired (god files, lazy imports across layers, HTTP-to-self in worker, module globals for hot state). That pattern is common in startups that scaled features faster than they scaled architecture review.

It is **shippable** in its current state for a small carrier customer. It is **not** at the maturity level you'd want for a regulated industry, multi-region SaaS, or 1000+ cc carrier without the P0 fixes above.

The single highest-leverage change you can make this quarter is **moving telephony state out of process memory into Redis** and **decomposing the three god files**. Do those two and most of the rest of the architectural debt becomes tractable.

---

## Tracking — work as we execute it

Use the table below to mark progress on the roadmap. When we start a step, change the status; when we finish, write the PR/commit SHA in "Done".

| # | Roadmap item | Severity | Status | Done (commit / date) | Notes |
|---|---|---|---|---|---|
| 1 | Move telephony module state to Redis | 🔴 P0 | **DEPLOYED to prod (redis mode active), soaking** | `aacc6cc` `64cc742` `c1e170b` `4bcb96a` `f3e3b74` (2026-05-31) | Phase 1 (steps 1-5) live on prod with `TELEPHONY_STATE_BACKEND=redis`; per-process heartbeats confirmed in Redis. Per-incarnation identity makes recovery safe under `--workers 4`. **Remaining before ✅:** live restart-mid-call verification (needs a test call) + 3-5 day soak. Phase 2 (multi-pod active-active) is a separate future plan. |
| 2 | Decompose `voice_pipeline_service.py` + `telephony_bridge.py` + `voice_orchestrator.py` | 🔴 P0 | not started | — | along existing seams; do incrementally |
| 3 | Replace HTTP-to-self in dialer worker → **hardened the internal call instead** | 🔴 P0 | **DEPLOYED to prod** | `1b2279b` (2026-06-02) | Re-scoped: the dialer↔API HTTP boundary is deliberate (ARI adapter must stay in the long-lived API process — a co-located adapter gets its channels reaped by Asterisk). So we hardened the internal call rather than removing it: `X-Internal-Service-Token` CSRF exemption (replaces the Origin-spoof hack) + JSON body (kills the `+`-as-space E.164 encoding class). `INTERNAL_SERVICE_TOKEN` set on prod. Dialer keeps an Origin fallback when the token is unset. Full removal of HTTP-to-self (Redis-queue IPC) is deferred to item 7. |
| 4 | Pick one migration system | 🟠 P1 | **DONE** | `(item-4 commit)` (2026-06-02) | Consolidated onto Alembic. `database/schema/baseline_2026-06-02.sql` = pg_dump of prod (76 tables) as the fresh-install baseline (replaces stale 65-table complete_schema.sql in docker init). Raw `database/migrations/*.sql` (28) archived + frozen. Drifted failure-classification columns reintroduced as idempotent Alembic `0009`. `database/MIGRATIONS.md` documents the one process. Zero prod change (dump read-only; prod converges to 0009 on next `alembic upgrade head`). |
| 5 | Introduce `app/domain/repositories/` | 🟠 P1 | not started | — | start with CallRepository + LeadRepository |
| 6 | Replace `get_container()` with explicit `Depends(ServiceContext)` | 🟠 P1 | not started | — | deprecation warning first, migrate over time |
| 7 | Shard dialer workers (Redis Streams consumer groups) | 🟠 P1 | not started | — | unlocks horizontal scaling of dialing |
| 8 | Staging environment | 🟡 P2 | not started | — | smallest version that mirrors prod stack |
| 9 | CI lint (ruff + mypy --strict) | 🟡 P2 | **DONE (ruff); mypy deferred** | `b31d5a3` (2026-06-02) | Made the previously non-blocking lint step a **blocking pyflakes-F gate** (`ruff check app/ --select F --extend-ignore F401,F841`); F401/F841 cosmetic backlog runs advisory alongside E/W so the gate stays meaningful, not perma-red. The gate immediately paid for itself: it surfaced **4 latent runtime bugs** — `import hashlib` missing in `emergency_access.py`, `import logging` missing in `google_tts.py`, an undefined `user_id` in the passkey clone-detection security log (all F821 = `NameError` at runtime on security paths), and a dead duplicate `require_admin` shadowed by the RBAC version (F811). Also F401/F541 autofix across ~90 files (validated zero new test failures). CI test-DB init + schema-check repointed at `baseline_2026-06-02.sql`. `mypy --strict` deferred to a later pass (large return-type backlog). The 3 F821 fixes were deployed surgically to prod (in-place, backups kept) and `talky-api` restarted clean (2026-06-02). The restart also surfaced a pre-existing prod 500 on `/calls/live` (`$8 || ' seconds'` bound an int as text) — fixed in `1f0f31d` and deployed. **mypy --strict** is the only remaining sub-item. |
| 10 | Fuzz tests for C++ voice gateway | 🟡 P2 | not started | — | libFuzzer harness for RTP packet parsing |

## Already-fixed items from this session (for the record)

| Item | Severity at the time | Where it was fixed | Date |
|---|---|---|---|
| Async-generator decorator on wrong function in dialer worker | 🔴 P0 (worker dead) | `backend/app/workers/dialer_worker.py` | 2026-05-22 |
| Audit logger flush poisoned by FK violation from deleted user | 🔴 P0 (login 500s) | `backend/app/domain/services/audit_logger.py` + `audit_logs.actor_id` schema | 2026-05-22 |
| Error-envelope dict-as-string leakage (Track 1) | 🟠 P1 | `backend/app/core/error_handlers.py` + new `errors.py` | 2026-05-22 |
| Retry classification + smart backoff (Track 2) | 🟠 P1 | new `backend/app/workers/retry_policy.py` + worker rewire + migration | 2026-05-22 |
| `acquire_with_tenant()` RLS GUC helper (Track 3) | 🟠 P1 | new `backend/app/core/db_utils.py` + `tenant_phone_number_service.py` | 2026-05-22 |
| Live-calls panel (Track B) | 🟡 P2 | new `call_status.py` + `/calls/live` endpoint + frontend `LiveCallsPanel` | 2026-05-22 |
| UUID-vs-string tenant compares (4 files) | 🟠 P1 | campaigns/secrets/audit_logs/security_events | earlier session |
| Cookie-only auth gates (settings page, Ask AI popup) | 🟠 P1 | `lib/auth-context.tsx` + `settings/page.tsx` + `voice-agent-popup.tsx` | earlier session |
| CSRF/Origin rejection of dialer's own HTTP-to-self | 🔴 P0 (no calls placed) | `backend/app/workers/dialer_worker.py` (Origin header) | 2026-05-22 |
| caller_id URL-encoding (`+` decoded as space) | 🔴 P0 (no calls placed) | `backend/app/workers/dialer_worker.py` (urllib.parse.quote) | 2026-05-22 |

---

**Next step proposal**: pick item 1 (move telephony state to Redis) and write a concrete migration plan with:
- inventory of every read/write site for the six module dicts,
- chosen Redis key schema + TTL strategy,
- compatibility shim so the change can ship behind a feature flag,
- rollback plan,
- ordered task list with dependencies.

When you're ready, say "let's plan item 1" and we go.
