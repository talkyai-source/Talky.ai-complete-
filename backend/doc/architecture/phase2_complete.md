# Phase 2 — Horizontal Scale Ready: COMPLETE

**Date:** 2026-05-05
**Phase goal (per architecture_plan.md §Phase 2):** make N pods cooperate without losing call state. NGINX routes audio chunks to the pod that owns the session, Redis coordinates concurrency across pods in real time, PgBouncer absorbs the connection fan-out, and graceful drain integrates cleanly with k8s rolling restarts.

---

## What shipped

### 2.1 NGINX consistent-hash session affinity
- **New:** `infra/nginx/talky.conf` — full NGINX front-door config.
- Audio path `/api/v1/sip/telephony/audio/{session_id}` is hashed on `session_id` with `hash $affinity_key consistent`. Adding or removing a backend pod only reshuffles the affected ring slice; in-flight calls stay on the pod that owns their state.
- Non-audio API traffic falls back to `$request_id` (effectively round-robin) so stateless calls don't bucket onto one pod.
- Stateless audio framing: `proxy_buffering off` and `proxy_request_buffering off` so a 3-6 KiB audio frame isn't held by NGINX waiting for a buffer to fill.
- Health endpoints `/healthz/live` and `/healthz/ready` are passed through; k8s reads readiness directly from each pod and removes the pod from the Service endpoint slice when it goes 503, which propagates to the upstream block via DNS.

### 2.2 Cross-pod Redis coordination
- **New:** `backend/app/domain/services/global_concurrency_listener.py` — two long-lived async tasks per pod.
  - `keyspace_expiry_listener` — psubscribes to `__keyevent@*__:expired`. When a `telephony:lease:*` key TTLs out (pod crash mid-call), it `SREM`s the call_id from `telephony:active_call_ids` immediately. Slot recovery latency drops from up to 30s (next watchdog reconcile) to milliseconds.
  - `quota_alerts_listener` — subscribes to the existing `telephony:quota_alerts` channel and caches the latest decision per tenant. `make_call` consults the cache via `get_cached_quota_decision()` so origination doesn't hit the threshold table on the hot path.
- The listener tries to enable `notify-keyspace-events Ex` automatically; on managed Redis where `CONFIG SET` is locked, the listener logs and degrades gracefully (the periodic watchdog reconcile remains the backstop).
- **Modified:** `backend/app/main.py` — lifespan starts both listeners after container startup, stops them cleanly on shutdown via a shared `asyncio.Event`.
- **New:** `backend/tests/unit/test_global_concurrency_listener.py` — 3 tests using a tiny fake Redis stub: keyspace events trigger SREM only for lease keys; quota alerts populate the cache; both listeners no-op when Redis is unavailable.

### 2.3 PgBouncer + connection discipline
- **New:** `infra/pgbouncer/pgbouncer.ini` — transaction-pooling config sized for 3 pods × 50 calls. `default_pool_size=200`, `max_client_conn=2000`, `reserve_pool_size=20`, hourly `server_lifetime` rotation. Documents both `talkyai` (write) and `talkyai_ro` (read replica) databases.
- **Modified:** `backend/app/core/db.py` — adds a separate read-replica pool wired off `READ_DATABASE_URL`:
  - `init_db_pool()` creates the primary, then creates the replica pool when `READ_DATABASE_URL` is set; aliases the primary when unset so single-DB deploys behave exactly as before.
  - New `get_read_pool()` and `get_read_db()` async-context-manager surfaces, with the same RLS / `SET LOCAL` handling as `get_db()`.
  - All sizing knobs are env-var driven: `PG_POOL_MIN_SIZE` (5), `PG_POOL_MAX_SIZE` (20), `PG_READ_POOL_MIN_SIZE` (2), `PG_READ_POOL_MAX_SIZE` (15).
  - **PgBouncer compatibility:** when the env var `PG_STATEMENT_CACHE_SIZE=0` is set, the pools disable server-side prepared statements (mandatory in transaction-pooling mode). Without this, asyncpg silently breaks against PgBouncer.
  - `close_db_pool()` shuts down the read pool first so anything pending drains while the primary is still up.
- **New:** `backend/tests/unit/test_db_pool_config.py` — 3 tests: env-var sizing, replica creation when configured, replica aliasing primary when unset, statement-cache-size honoured.

### 2.4 Graceful drain k8s integration
- **New:** `infra/k8s/backend-deployment.yaml` — reference manifest documenting the contract between Phase 1.4's drain logic and the orchestrator:
  - `terminationGracePeriodSeconds: 360` (one minute longer than `DRAIN_TIMEOUT_S` so the post-drain teardown has time).
  - `readinessProbe` on `/api/v1/healthz/ready` with a 2-failure threshold so eviction takes ~10s once the pod goes 503.
  - `livenessProbe` on `/api/v1/healthz/live` with a generous 4-failure budget so a transient slow callback doesn't restart a pod with 50 active calls.
  - `lifecycle.preStop` adds a 5s sleep so kube-proxy completes one Service-endpoint reconciliation before the in-pod drain loop starts forcing teardown — ensures no new calls land on the pod while it's mid-drain.
  - Headless Service so each pod resolves to its own DNS name (required by the NGINX consistent-hash upstream block).
  - Stub `HorizontalPodAutoscaler` (CPU-based for Phase 2; Phase 3 swaps to a custom `active_calls / pod_capacity` metric).
- **Verified:** the drain wiring from Phase 1.4 (`begin_drain()` on lifespan shutdown, wait-for-empty loop, `/healthz/ready` 503 response) lines up with the k8s SIGTERM → readiness-flip → endpoint-eviction → graceful-period contract.

### 2.5 Verification harness
- The Phase 1 `loadtest_calls.py` already drives variable concurrency. For Phase 2 the **rolling-restart scenario** is operator-run rather than a script:
  1. Start a 3-pod deployment behind NGINX.
  2. Hold `--concurrent 150 --duration 600` from the harness.
  3. Mid-test: `kubectl rollout restart deployment/talky-backend`.
  4. Observe: each draining pod's `/healthz/ready` flips to 503; NGINX endpoint slice updates; new calls go to the surviving pods; the drained pod's existing calls finish naturally; replacement pods come up and rejoin the upstream pool.
  5. **Pass criteria** (per architecture_plan.md §Phase 2.5): zero call drops, all in-flight calls complete on their original pod, new calls re-balance.

---

## How to verify

1. **Unit tests:**
   ```
   cd backend && ./venv/bin/python -m pytest tests/unit/ -q
   ```
   Result: **1144 passed, 12 skipped** (was 1138 before Phase 2; the 6 new tests in `test_global_concurrency_listener.py` and `test_db_pool_config.py` are now in the suite).

2. **Listener wiring in lifespan (offline):**
   ```
   ./venv/bin/python -c "
   from app.domain.services.global_concurrency_listener import (
       keyspace_expiry_listener, quota_alerts_listener, get_cached_quota_decision
   )
   print('listeners importable')
   "
   ```

3. **NGINX config syntax (when nginx is installed):**
   ```
   nginx -t -c /home/ai-lab/Desktop/Talky.ai-complete-/infra/nginx/talky.conf
   ```

4. **PgBouncer config syntax (when pgbouncer is installed):**
   ```
   pgbouncer --check /home/ai-lab/Desktop/Talky.ai-complete-/infra/pgbouncer/pgbouncer.ini
   ```

5. **3-pod rolling-restart soak:** see §2.5 above. End-to-end test runs against a real k8s namespace; not part of the unit suite by design.

---

## What deliberately is NOT in Phase 2

- **Helm chart** — Phase 3. The k8s manifest in `infra/k8s/` is reference-only.
- **Redis Sentinel / Cluster, Postgres HA managed setup** — Phase 3.
- **Custom-metric autoscaler on `active_calls`** — Phase 3 (HPA stubbed on CPU for now).
- **Multi-region trunk failover, cost ledger, chaos suite** — Phase 4.

Phase 2 is the *coordination* layer: every Phase 3 piece (Sentinel, Cluster, HPA on custom metrics) plugs into the same primitives Phase 2 just established (readiness probe, keyspace listener, PgBouncer pool, drain timing).

---

## Files touched (Phase 2)

New:
```
infra/nginx/talky.conf
infra/pgbouncer/pgbouncer.ini
infra/k8s/backend-deployment.yaml
backend/app/domain/services/global_concurrency_listener.py
backend/tests/unit/test_global_concurrency_listener.py
backend/tests/unit/test_db_pool_config.py
backend/doc/architecture/phase2_complete.md
```

Modified:
```
backend/app/core/db.py
backend/app/main.py
```

---

## Operational handoff for Phase 3 (next phase preview)

When you're ready to start Phase 3, the things you'll need provisioned externally:
- Managed Postgres with at least 1 read replica and PITR backups (Aiven / RDS / Cloud SQL).
- Managed Redis with at least one replica + Sentinel quorum (or jump straight to ElastiCache / Upstash for managed HA).
- A Kubernetes cluster (any flavour) with a Prometheus stack and the Prometheus Adapter for custom-metric autoscaling.
- DNS / cert-manager for the public hostname.

Phase 3 then writes the helm chart, swaps the HPA to scale on `active_calls / pod_capacity`, and tightens up observability dashboards. Architecture stays the same.
