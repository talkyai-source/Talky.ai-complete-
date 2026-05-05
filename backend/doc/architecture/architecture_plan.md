# Concurrent-Call Scaling Roadmap — 50 now, 1000+ on the same architecture

## Context

Talky.ai today runs a single uvicorn worker on a single VPS (docker-compose: backend + Postgres + Redis). Current code is unusually mature for its stage — it already has Redis-backed global call leases (`global_concurrency.py`), per-tenant Postgres lease policies (`telephony_concurrency_limiter.py`), threshold-based quota throttling (`telephony_rate_limiter.py`), resilient TTS with circuit breakers, OpenTelemetry, Sentry, and a credential resolver for per-tenant keys.

But it has hard ceilings that block both the 50-concurrent target and any future 1000-concurrent ambition:

1. **Single process, in-memory state.** `_telephony_sessions`, `_ringing_warmups`, Cartesia `_call_ws`, `_early_audio_buffers` all live in one Python process. No horizontal scale, no failover.
2. **One API key per provider.** Groq, ElevenLabs, Deepgram, Cartesia each have a single key. ElevenLabs `limit_per_host=50` is already at ceiling at 50 concurrent calls — no headroom.
3. **No per-provider concurrency primitives.** A burst of 50 simultaneous LLM streams hits Groq with no semaphore; the only backpressure is the upstream rate-limit error.
4. **No autoscaling.** `MAX_TELEPHONY_SESSIONS=50` is a hard env cap on a single pod. There is no HPA, no replica controller, no load balancer with call-id affinity.
5. **Single Redis, single Postgres.** Both are SPOFs at 50, lethal at 1000.

**Outcome we want:** an architecture where moving from 50 → 200 → 1000 concurrent calls is a *capacity* operation (more pods, more keys, bigger Redis, more provider quota) — not a re-architecture. No workarounds, no patches, production-ready by 2026 standards.

---

## Capacity Model — what we actually need to buy

The math is built per-call from observed pipeline behavior in the codebase (one STT WS, one LLM stream per turn, one TTS stream per sentence, ~8-12 turns/min per call).

| Resource | Per-call cost | 50 concurrent | 1000 concurrent | Provider tier needed |
|---|---|---|---|---|
| **Deepgram STT** | 1 streaming WS | 50 WS | 1000 WS | Pay-as-you-go default cap = 100 concurrent. Need **Growth/Enterprise concurrency uplift** at 1000. |
| **Groq LLM** | ~1 stream / 6s avg → ~500 RPM @ 50 calls | 500 RPM | 10 000 RPM | Free/Dev tier (30 RPM) is unusable. Need **paid Production tier** for 50; **Enterprise contract w/ dedicated capacity** for 1000. Run **2 keys minimum** for failover at any scale. |
| **ElevenLabs TTS** | ~3-4 streaming requests/min/call (sentence-level) | ~200 RPM, 50 concurrent streams | ~4000 RPM, 1000 concurrent | **Scale plan (~250 concurrent)** for 50-call target with headroom. **Enterprise contract** for 1000. Pool **3-5 keys** with weighted round-robin for graceful degradation when one key throttles. |
| **Cartesia TTS (fallback)** | 1 persistent WS/call | 50 WS | 1000 WS | **Pro plan (100 concurrent)** for 50; **Enterprise** for 1000. **2 keys** for failover. |
| **Google Cloud TTS (secondary fallback)** | ~3-4 RPM/call | 200 RPM | 4000 RPM | Default quota = 1000 RPM/project. **2 GCP projects with separate service accounts** for 50; **5+ projects** for 1000. |
| **Twilio / SIP trunk** | 1 channel/call | 50 channels | 1000 channels | Twilio Elastic SIP Trunking auto-scales; reserve **2× peak** in trunk capacity. Use **2 trunks across regions** for failover. |
| **Postgres connections** | ~2 active queries/call/min (transcripts, metrics, lease heartbeat) | ~100 conns peak | ~2000 conns peak | Direct connections die at ~200 with default RDS. Mandatory: **PgBouncer in transaction mode**, app pool 20/worker, PgBouncer pool 200 → DB. |
| **Redis ops** | ~10 ops/sec/call (lease heartbeat, pub/sub, queue) | 500 ops/sec | 10 000 ops/sec | Single-node Redis fine for 50. **Redis Cluster (3 shards) or ElastiCache with replica** at 1000. |
| **Backend pods** | ~25-30 calls / 2 vCPU / 2 GiB pod (audio resample + asyncio overhead is the binder) | **2 pods** (one is HA spare) | **35-40 pods** | k8s HPA on `active_calls` custom metric. |

**Provisioning summary for the 50-concurrent launch:**
- 1× Twilio Elastic SIP Trunk (100 channel reservation)
- Deepgram: 1 account, request concurrency uplift to 100
- Groq: Production tier, 2 keys
- ElevenLabs: Scale plan, 3 keys in pool
- Cartesia: Pro plan, 2 keys (fallback path)
- GCP TTS: 2 projects, 2 service accounts (secondary fallback)
- 2 backend pods, 1 PgBouncer pod, 1 Redis (with AOF + 1 replica)

**Provisioning summary for 1000-concurrent (no architecture change, just capacity):**
- Twilio: 2 trunks, 2000 channels
- Deepgram: Enterprise, 1500 concurrent uplift
- Groq: Enterprise dedicated, 4-6 keys
- ElevenLabs: Enterprise, 6-8 keys in pool
- Cartesia + GCP: Enterprise tiers, more keys / projects
- 35-40 backend pods, 3 PgBouncer pods, Redis Cluster 3 shards + replicas, Postgres primary + 2 read replicas

---

## Phase 1 — Make a single pod safe for 50 concurrent (Week 1-2)

Goal: kill the bottlenecks that cause cascading failure on a single pod *before* we go horizontal. None of this is a patch — it's the foundation every later phase depends on.

### 1.1 Provider key pools with health-aware routing

Create `backend/app/infrastructure/providers/key_pool.py`:
- Generic `KeyPool[T]` abstraction: list of keys with weight, in-flight counter, error-rate EWMA, cooldown timer.
- Policy: pick lowest-in-flight key whose error-rate < threshold and not in cooldown. On 429/5xx, mark cooldown (exponential 1s → 60s). On success, decay error-rate.
- Wire into `groq.py`, `elevenlabs_tts.py`, `cartesia.py`, `deepgram.py`. Each provider client reads its key list from settings (`GROQ_API_KEYS=key1,key2,...`).
- Tenant-specific keys from `credential_resolver.py` continue to take precedence; pools are the *platform* fallback.

Reuse: integrates with existing `resilient_tts.py` circuit breaker — pool exhaustion trips the breaker, which already handles fallback to Cartesia/Google.

### 1.2 Per-provider concurrency semaphores

In each provider client, add an `asyncio.Semaphore` sized to `floor(account_concurrency_cap × 0.85)`. Semaphore acquire is the FIRST thing in any provider call — guarantees we never overshoot the contracted ceiling regardless of caller burst pattern.
- Settings: `GROQ_MAX_CONCURRENT=80`, `ELEVENLABS_MAX_CONCURRENT=200`, `DEEPGRAM_MAX_CONCURRENT=80`, `CARTESIA_MAX_CONCURRENT=80`.
- Emit Prometheus gauge `provider_inflight{provider="..."}` so saturation is observable before it bites.

### 1.3 Bounded session lifecycle — no leaks ever

In `session_manager.py` and `telephony_bridge.py`:
- Add a single `asyncio.Task` watchdog that scans `_telephony_sessions`, `_ringing_warmups`, `_early_audio_buffers`, Cartesia `_call_ws` every 30s and evicts entries whose underlying call is `ended`, `error`, or has missed heartbeat > 90s. Watchdog runs in `lifespan` startup, cancelled cleanly on shutdown.
- Cartesia `_call_ws` cleanup must move into a `try/finally` around the call lifecycle — never the caller's responsibility.
- Add `weakref.finalize` on `CallSession` so any GC'd session triggers WS / queue cleanup as a backstop.

### 1.4 Graceful overload — reject early, never queue indefinitely

When pod is at capacity:
- New call requests get HTTP 503 with `Retry-After`, body `{"reason": "pod_at_capacity"}` — never block.
- Telephony adapter reads 503 and signals SIP `486 Busy Here` to caller.
- This is the single behavior that makes autoscaling later trivial: the load balancer sees 503s, HPA reads `active_calls` metric, both work.

### 1.5 Eliminate blocking I/O on hot path

Audit one pass with `pytest-asyncio --asyncio-mode=strict` and a sync-detector (e.g., `asyncio` debug mode + `slow_callback_duration=0.1`). Anything that prints a "blocking detected" warning gets fixed: typical suspects are sync `requests` calls in adapters, JSON pickling of large objects, sync DB queries via psycopg2.

### 1.6 Verification (Phase 1)

- Load test harness: `backend/scripts/loadtest_calls.py` using `sipp` for SIP signaling + a synthetic audio generator that streams a real 60-second WAV. Target: 50 concurrent calls sustained 10 minutes.
- Pass criteria: p95 first-audio < 1.2s, p95 turn latency < 1.8s, zero session leaks (watchdog deletions == zero after drain), no provider-inflight gauge crosses 85% of cap.

---

## Phase 2 — Horizontal scale ready (Week 3-4)

Now that one pod is sound, make N pods cooperate without losing call state.

### 2.1 Stateless ingress, stateful backend with call-id affinity

- Put **NGINX or Envoy** in front of the backend, terminating TLS, with `hash $http_x_call_id consistent` (or upstream IP-hash on the SIP→HTTP gateway side) so audio POSTs for a given call always land on the pod that owns the session.
- The C++ telephony gateway already POSTs to `/api/v1/sip/telephony/audio/{session_id}`. Add `X-Call-Id` derivation in nginx from the URL path — consistent hashing on `session_id` gives us affinity without a session table.
- Pod IP changes (rolling restart) are handled by graceful drain (see 2.4).

### 2.2 Cross-pod coordination via Redis Pub/Sub

- **Barge-in** — when STT detects voice activity on pod A, but the previous TTS is being synthesized on pod A, no cross-pod hop is needed. Affinity guarantees this. **No cross-pod barge-in coordination required** unless we ever do call transfer mid-session.
- **Global concurrency** — already implemented in `global_concurrency.py` via Redis leases. Phase 2 work: shorten lease refresh from 60s to 15s (orphan-recovery latency drops from 10min to 60s), and add a Redis-keyspace-notifications listener so freed slots are surfaced instantly to the dispatcher.
- **Quota alerts** — `telephony:quota_alerts` Pub/Sub already exists; subscribe in every pod for cluster-wide enforcement.

### 2.3 PgBouncer + connection discipline

- Deploy PgBouncer (transaction pooling mode) as a sidecar or DaemonSet.
- App `asyncpg` pool: `min_size=5, max_size=20` per pod.
- PgBouncer: `default_pool_size=200, max_client_conn=2000, server_idle_timeout=60`.
- Add Postgres read replica; route campaign read queries (`campaigns.py` list/get) to replica via separate `READ_DATABASE_URL`.

### 2.4 Graceful drain on shutdown

- `lifespan` shutdown handler must:
  1. Mark pod NOT_READY in `/healthz/ready` so LB stops sending new calls.
  2. Wait until `active_calls == 0` or `DRAIN_TIMEOUT=300s` — whichever first.
  3. Active calls naturally complete; new calls go to other pods (LB consistent hash re-buckets only the bucket of the dying pod).
- k8s `terminationGracePeriodSeconds: 360`.

### 2.5 Verification (Phase 2)

- 3-pod cluster behind nginx, 150 concurrent calls. Trigger rolling restart mid-load. Pass: zero call drops, all in-flight calls complete on their original pod, new calls re-balance.

---

## Phase 3 — Production-grade infra (Week 5-6)

### 3.1 Kubernetes deployment

- Migrate from docker-compose to k8s (helm chart at `infra/helm/talky/`). Single source of truth.
- Manifests: `Deployment` (backend), `StatefulSet` (Redis Sentinel cluster, Postgres if self-hosted else RDS/Aiven), `HorizontalPodAutoscaler`, `PodDisruptionBudget` (`minAvailable: 50%`), `NetworkPolicy` (Postgres + Redis only reachable from backend namespace).
- Custom-metric HPA: scale on `active_calls / pod_capacity` (target 70%) — Prometheus Adapter exposes the metric we already emit.

### 3.2 Redis HA

- 50-call target: Redis with one replica + Sentinel (3-node sentinel quorum).
- 1000-call target: migrate to Redis Cluster (3 shards × 2 replicas) or managed (ElastiCache, Upstash). Code changes: replace `redis.asyncio.Redis` instantiation with `RedisCluster`; key access patterns are already cluster-safe (we use hash tags `{tenant_id}` only where atomicity is required, otherwise single keys).

### 3.3 Postgres HA

- Managed (Aiven, RDS, Cloud SQL) with automated failover and 2 read replicas. PITR backups. Monthly restore drill is a calendar item, not a hope.

### 3.4 Observability — make scaling visible

- Existing OTel + Prometheus stays.
- New dashboards (Grafana, JSON in `infra/grafana/dashboards/`):
  - **Capacity** — concurrent calls, per-provider in-flight vs cap, pod count, headroom %.
  - **Pipeline latency** — p50/p95/p99 of STT-first-token, LLM-first-token, TTS-first-audio, end-to-end turn.
  - **Quality** — barge-in detection latency, audio queue overruns, dropped TTS frames.
- Alerting (Alertmanager → PagerDuty):
  - any provider in-flight > 85% cap for > 2min
  - p95 turn latency > 2.5s for > 5min
  - active_calls / total_capacity > 80% for > 10min (autoscaler isn't keeping up)
  - Redis or Postgres replication lag > 5s

### 3.5 Verification (Phase 3)

- 5-pod k8s cluster, 250 concurrent calls sustained 30min. Kill one pod (`kubectl delete pod`) mid-load: HPA replaces it within 90s, calls on other pods unaffected, drained pod's calls complete cleanly.

---

## Phase 4 — Hardening for the road to 1000 (Week 7-8)

### 4.1 Multi-region SIP and provider failover

- Twilio: 2 elastic trunks in 2 regions (us-east + us-west or eu-west). DNS-based failover via Twilio's edge selection.
- Provider failover graph in `resilient_tts.py` is already there — extend it to STT and LLM: STT primary Deepgram → fallback AssemblyAI; LLM primary Groq → fallback OpenAI gpt-4o-mini-realtime (latency-comparable). Activation is automatic on circuit-breaker trip.

### 4.2 Cost & rate-limit governance

- Already have `tenant_telephony_threshold_policies` and `tenant_telephony_quota_events`. Add a per-provider cost ledger (`tenant_provider_cost_events`) that records token/character/second cost from each provider response header. This is what makes "1000 concurrent customer calls" survive budget review — we know exactly what they cost in real time.

### 4.3 Chaos & soak testing

- Weekly soak: 1× peak load for 4 hours.
- Chaos suite (chaos-mesh or litmus): kill a random pod every 10 minutes, partition Redis from one AZ, throttle one provider key to simulate 429 storm. All must result in zero customer-visible drops at the peak load we've certified.

### 4.4 Verification (Phase 4) — the 1000-call certification

- 40-pod cluster, full provider roster on enterprise tiers, 1000 concurrent calls, 1-hour sustained.
- Pass: p95 turn latency ≤ 2.0s, zero call drops attributable to platform (provider-side outages logged but excluded), all alerts green except chaos-induced ones that auto-recover within SLO.

---

## Critical files to modify (Phase 1 priority order)

| File | Change |
|---|---|
| `backend/app/infrastructure/providers/key_pool.py` (new) | Generic key pool with health-aware routing |
| `backend/app/infrastructure/llm/groq.py` | Use KeyPool, add `asyncio.Semaphore` |
| `backend/app/infrastructure/tts/elevenlabs_tts.py` | KeyPool + semaphore; raise `limit_per_host` only after pool routes across keys |
| `backend/app/infrastructure/tts/cartesia.py` | KeyPool + semaphore + try/finally cleanup of `_call_ws` |
| `backend/app/infrastructure/stt/deepgram.py` + `deepgram_flux.py` | KeyPool + semaphore |
| `backend/app/domain/services/session_manager.py` | Watchdog task for orphan eviction; weakref finalizers |
| `backend/app/domain/services/global_concurrency.py` | Lease refresh 60s → 15s; keyspace-notification listener |
| `backend/app/api/v1/endpoints/telephony_bridge.py` | 503 + `Retry-After` on overcap, drained `/healthz/ready` |
| `backend/app/main.py` | `lifespan` registers watchdog; readiness gate during drain |
| `backend/app/core/config.py` (or settings) | New env vars: `*_API_KEYS` (list), `*_MAX_CONCURRENT`, `DRAIN_TIMEOUT` |
| `infra/helm/talky/` (new) | k8s helm chart (Phase 3) |
| `infra/nginx/talky.conf` (new) | Consistent-hash affinity (Phase 2) |
| `backend/scripts/loadtest_calls.py` (new) | sipp + synthetic audio harness |

## Existing code to reuse — do not rewrite

- `resilient_tts.py` — circuit breaker + provider fallback chain. Extend, don't replace.
- `global_concurrency.py` — Redis lease pattern. Already correct, just tune timeouts.
- `telephony_concurrency_limiter.py` + `telephony_rate_limiter.py` — per-tenant policy is solid; lives unchanged.
- `credential_resolver.py` — per-tenant key takes precedence over platform pool. Unchanged.
- `streaming_pipeline.py` — sentence-level streaming. Unchanged.
- `telephony_observability.py` — Prometheus exposition. Add new gauges, don't refactor.

## End-to-end verification

1. **Phase 1 unit/integration:** `pytest backend/tests/unit/ -k "key_pool or semaphore or watchdog"` — 100% pass on new code, no regressions.
2. **Phase 1 load:** `python backend/scripts/loadtest_calls.py --concurrent 50 --duration 600` — single-pod sustained.
3. **Phase 2 load:** same script `--concurrent 150` against 3-pod nginx cluster; mid-test `kubectl rollout restart deployment/backend`.
4. **Phase 3 chaos:** `chaos-mesh` pod-kill experiment during 250-concurrent load.
5. **Phase 4 certification:** 1000-concurrent 60-min soak with full provider roster on enterprise tiers; sign-off requires Grafana screenshots of all SLOs green.

## What this roadmap deliberately is NOT

- Not a list of patches. Every change is a foundation later phases depend on.
- Not a rewrite. The codebase is in good shape; we are filling in the missing platform layers (key pooling, semaphores, watchdog, k8s, HA Redis/Postgres) and turning hard limits into knobs.
- Not premature 1000-call work. Phases 3-4 are paid for at the time they are needed; the *architecture* admits 1000 from day one, the *bill* doesn't.
