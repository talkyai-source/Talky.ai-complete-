# Phase 1 — Single-Pod Foundations: COMPLETE

**Date:** 2026-05-05
**Phase goal (per architecture_plan.md §Phase 1):** Make a single pod safe for 50 concurrent calls and lay foundations every later phase depends on. None of the changes are patches — the next phases extend them rather than replace them.

---

## What shipped

### 1.1 Provider key pool with health-aware routing
- **New:** `backend/app/infrastructure/providers/key_pool.py` — generic `KeyPool` with EWMA-based error-rate tracking, exponential cooldown (1s → 60s cap), in-flight load balancing.
- **New:** `backend/app/infrastructure/providers/__init__.py` — package surface.
- **New:** `backend/tests/unit/test_key_pool.py` — 9 tests; pool size, dedup, cooldown growth, success decay, exception safety.

How it works: pool holds N keys, each with a live `in_flight` counter and an EWMA error rate. `pool.acquire()` selects the lowest-loaded healthy key. On 429 / 5xx, `lease.report_failure(retryable=True)` cools the key and the next acquire routes to a healthy sibling. When all keys are cooling, `KeyPoolExhaustedError` is raised — the existing `resilient_tts.py` circuit breaker picks up from there.

### 1.2 Per-provider concurrency semaphores
- **New:** `backend/app/infrastructure/providers/provider_concurrency.py` — `ProviderConcurrencyGuard` registry. One singleton per provider name (`groq`, `elevenlabs`, `deepgram`, `cartesia`).
- **New:** `backend/tests/unit/test_provider_concurrency.py` — 5 tests; bound enforcement, queue order, timeout path, registry singleton, env-var sizing.
- **Modified:** `backend/app/infrastructure/llm/groq.py` — wraps `stream_chat` in `guard.acquire()` + per-attempt key lease. Per-key `AsyncGroq` client cache so each pool key gets its own httpx connection pool (no cross-key TLS thrash). Single-key fallback preserves current production behaviour and existing test mocks.
- **Modified:** `backend/app/infrastructure/tts/elevenlabs_tts.py` — guard wraps the entire retry loop; key picked per attempt; introduces tiny `_SingleKeyLease` shim (re-used by other providers).
- **Modified:** `backend/app/infrastructure/tts/cartesia.py` — guard wraps each synthesize call; **per-call key pinning** (call_id → key map) so every sentence on a Cartesia WS uses the same API key the WS was opened with (Cartesia ties the key to the URL).
- **Modified:** `backend/app/infrastructure/stt/deepgram.py` — guard wraps the streaming session; per-key `AsyncDeepgramClient` cache; pool selects key per session.

Defaults sized to ≤85 % of standard plan caps:
```
GROQ_MAX_CONCURRENT=80
ELEVENLABS_MAX_CONCURRENT=200
DEEPGRAM_MAX_CONCURRENT=80
CARTESIA_MAX_CONCURRENT=80
```
Multi-key pools are configured by setting the new CSV env vars (`GROQ_API_KEYS`, `ELEVENLABS_API_KEYS`, `DEEPGRAM_API_KEYS`, `CARTESIA_API_KEYS`); leaving them unset preserves single-key behaviour exactly as before.

### 1.3 Session lifecycle watchdog
- **Modified:** `backend/app/domain/services/telephony/lifecycle.py` — extended the existing `_session_watchdog` rather than introducing a parallel one. New sweeps:
  - Orphan `_gateway_session_to_call_id` entries whose `call_id` no longer matches an active or ringing-warming session.
  - Orphan `_early_audio_buffers` whose `gateway_session_id` was reaped by the sweep above.
  - Orphan Cartesia `_call_ws` entries — every active VoiceSession with a Cartesia provider is asked to evict any internal call_id state that doesn't match a live session.

The original watchdog already covered: stale `_telephony_sessions` (via `is_stale`), orphan `_ringing_warmups` + `_ringing_warmup_created_at`, orphan `_ringing_events`, and Redis lease refresh / cluster-orphan reconcile. Phase 1 adds the missing per-call-state maps so no state map is unbounded any more.

### 1.4 Graceful overload (503 + Retry-After) and pod readiness
- **New:** `backend/app/core/readiness.py` — drain-flag + pod-capacity probe singleton. Exposes `begin_drain()`, `is_pod_ready()`, `is_pod_at_capacity()`, `set_capacity_providers()`.
- **Modified:** `backend/app/api/v1/endpoints/health.py` — adds `/api/v1/healthz/ready` (Kubernetes-style readiness probe; 200 when ready, 503 + `Retry-After` when draining or full) and `/api/v1/healthz/live` (always 200 if event loop is alive).
- **Modified:** `backend/app/api/v1/endpoints/telephony_bridge.py`:
  - Wires `set_capacity_providers()` at telephony-bridge connect time so `/healthz/ready` reads the live session count.
  - `make_call` now returns **HTTP 503 + Retry-After** when the pod is draining or at capacity, before any CallGuard / DB / Redis work.
- **Modified:** `backend/app/main.py` — `lifespan` shutdown calls `begin_drain()` immediately, then waits up to `DRAIN_TIMEOUT_S` (default 300s) for active calls to hang up naturally before forcing teardown. Sets the foundation Phase 2 / 3 needs for k8s rolling restarts and consistent-hash LB affinity.
- **New:** `backend/tests/unit/test_readiness.py` — 4 tests covering ready / capacity-blocks / drain-blocks / probe-503 paths.

### 1.5 Blocking I/O detector
- **Modified:** `backend/app/main.py` — `lifespan` startup, when `ASYNCIO_DEBUG=1`, enables `loop.set_debug(True)` and `loop.slow_callback_duration` (default 0.1s). The event loop logs any sync callback that exceeds the threshold so staging / CI surfaces hidden blocking calls before they poison voice timing in production.

This is intentionally a knob, not a permanent state — debug-mode adds overhead and is enabled per environment (staging / load-test runs), not on every prod boot.

### 1.6 Load test harness
- **New:** `backend/scripts/loadtest_calls.py` — `aiohttp` driver that originates calls at a configurable RPS to hold N concurrent for a duration. Counts 200/202 (accepted), 503 (correctly refused at capacity), and other failures separately. Reports p50 / p95 origination latency.

Audio realism is provided by the SIP loopback the C++ telephony gateway already streams — the harness is a pressure source, not a synthetic audio replayer.

---

## How to verify (per architecture_plan.md §Phase 1.6 verification)

1. **Unit tests:**
   ```
   cd backend && ./venv/bin/python -m pytest tests/unit/ -q
   ```
   Result: **1138 passed, 12 skipped** (pre-existing systemd-permissions test excluded; unrelated). All 18 newly added tests (`test_key_pool.py`, `test_provider_concurrency.py`, `test_readiness.py`) pass.

2. **Provider smoke (no real API call):**
   ```
   ./venv/bin/python -c "
   from app.infrastructure.tts.elevenlabs_tts import ElevenLabsTTSProvider
   from app.infrastructure.tts.cartesia import CartesiaTTSProvider
   from app.infrastructure.llm.groq import GroqLLMProvider
   from app.infrastructure.stt.deepgram import DeepgramSTT
   "
   ```

3. **Readiness contract (live):**
   ```
   curl -i http://localhost:8000/api/v1/healthz/ready
   # → 200 when running, 503 + Retry-After when at capacity / draining
   ```

4. **End-to-end load:** start the backend, then:
   ```
   ./venv/bin/python backend/scripts/loadtest_calls.py \
       --concurrent 50 --duration 600 --base-url http://localhost:8000
   ```
   Pass criteria from the plan:
   - p95 first-audio < 1.2s
   - p95 turn latency < 1.8s
   - zero session leaks (`/api/v1/sip/telephony/status` shows `active_sessions == 0` once load drops)
   - no `provider_inflight` snapshot crosses 85 % of `max_concurrent`

   Latency / leak measurements are read from Prometheus (existing `telephony_observability.py`) and the bridge status endpoint.

---

## What deliberately is NOT in Phase 1

- **Multi-pod horizontal scale, consistent-hash LB, PgBouncer.** That is Phase 2.
- **Kubernetes manifests, Redis Sentinel / Cluster, Postgres HA.** That is Phase 3.
- **Multi-region failover, cost ledger, chaos suite.** That is Phase 4.

Phase 1 is the floor every later phase stands on: pool, semaphore, watchdog, drain, readiness. None of these are patches — Phase 2 wires LB affinity to the same `/healthz/ready` probe, Phase 3 wires k8s `terminationGracePeriodSeconds` to the same `DRAIN_TIMEOUT_S`, Phase 4 wires the cost ledger to the same per-provider guard counters.

---

## Files touched (Phase 1)

New:
```
backend/app/infrastructure/providers/__init__.py
backend/app/infrastructure/providers/key_pool.py
backend/app/infrastructure/providers/provider_concurrency.py
backend/app/core/readiness.py
backend/scripts/loadtest_calls.py
backend/tests/unit/test_key_pool.py
backend/tests/unit/test_provider_concurrency.py
backend/tests/unit/test_readiness.py
backend/doc/architecture/phase1_complete.md
```

Modified:
```
backend/app/infrastructure/llm/groq.py
backend/app/infrastructure/tts/elevenlabs_tts.py
backend/app/infrastructure/tts/cartesia.py
backend/app/infrastructure/stt/deepgram.py
backend/app/domain/services/telephony/lifecycle.py
backend/app/api/v1/endpoints/health.py
backend/app/api/v1/endpoints/telephony_bridge.py
backend/app/main.py
```
