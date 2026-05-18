# Phase 4 — Hardening for the road to 1000: COMPLETE

**Date:** 2026-05-05
**Phase goal (per architecture_plan.md §Phase 4):** make the system survive a regional outage, a key-pool 429 storm, a Redis quorum partition, and a random pod kill — without dropping a single customer-attributable call. The architecture stays the same as Phase 3; what's new is the failover graphs, cost governance, chaos suite, and the certification harness that produces the 1000-concurrent sign-off.

---

## What shipped

### 4.1 Multi-region SIP + STT/LLM provider failover
- **New:** `infra/twilio/multi-region.md` — full operator runbook for two Elastic SIP Trunks across two Twilio Edge Locations, DNS configuration with 60s TTL, Asterisk `pjsip.conf` + `extensions.conf` two-step Dial, and the quarterly drill procedure. The application code already routes to whichever trunk is healthy via the existing Asterisk dialplan; this doc captures the operator-side configuration.
- **New:** `backend/app/domain/services/resilient_llm.py` — primary + secondary LLM wrapper with circuit-breaker-gated handshake failover. Shape mirrors `resilient_tts.py`: clean failover on handshake failure (no tokens yet emitted), re-raise on mid-stream failure (never interleave two LLMs in one response).
- **Verified existing:** `backend/app/domain/services/resilient_stt.py` is already in place from a prior phase (289 lines, more sophisticated than a fresh draft would have been). Phase 4.1 reuses it as-is.
- **New:** `backend/tests/unit/test_resilient_llm.py` — 6 tests: success path, handshake failover, mid-stream re-raise, no-secondary re-raise, both-providers-init, secondary-init failure disowns the secondary cleanly.

The two failover wrappers (LLM + STT) plus the existing TTS one give a complete provider-fallback graph: Groq → OpenAI; Deepgram → AssemblyAI; ElevenLabs → Cartesia → Google. The factory wires the secondaries when their config is present; absence preserves single-provider behaviour exactly.

### 4.2 Cost & rate-limit governance
- **New:** `backend/database/migrations/20260505_add_tenant_provider_cost_events.sql` — `tenant_provider_cost_events` ledger table. Per-call, per-provider, per-key-fingerprint event rows with `unit` / `quantity` / `unit_price_usd` / `cost_usd` / `model` / `voice_id` / `latency_ms` / `status`. RLS-isolated by tenant. Three indexes for the cost-by-tenant, cost-by-provider, and cost-by-key dashboards.
- **New:** `backend/app/domain/services/provider_cost_ledger.py`:
  - `CostEvent` dataclass + `record(event)` non-blocking append to an in-process buffer.
  - Background flusher (`start_flusher`, `stop_flusher`) batches buffered events every `COST_LEDGER_FLUSH_INTERVAL_S` (default 5s) using asyncpg's `copy_records_to_table` for throughput.
  - On DB failure, events stay buffered up to `COST_LEDGER_MAX_BUFFER` (default 5000); past that, oldest is dropped with a sampled warning so a stalled DB never memory-leaks.
  - `redact_key_fp()` produces the same key fingerprint shape as `KeyPool.redacted()` so the ledger and the operational logs cross-reference cleanly.
  - Per-provider quantity extractors (`parse_groq_usage`, `parse_elevenlabs_usage`, `parse_cartesia_usage`, `parse_deepgram_usage`) — each takes the response object/headers a provider client already has.
- **Modified:** `backend/app/main.py` — lifespan starts the flusher after container startup and stops + drains the buffer on shutdown.
- **New:** `backend/tests/unit/test_provider_cost_ledger.py` — 9 tests: append, drop-oldest at capacity, disabled-mode no-op, key fingerprint shape, all four parsers, no-pool keeps buffer, COPY path writes via fake conn.

Unit pricing is a separate ops job (sample `UPDATE` in the migration comments) so historical roll-ups stay correct after pricing renegotiations.

### 4.3 Chaos & soak suite
- **New:** `infra/chaos/pod-kill.yaml` — chaos-mesh `PodChaos` action killing one random Talky pod every 10 minutes during the soak window. Verifies HPA recovery within 90s, NGINX rebalancing only the dead pod's hash slice, and the lifespan drain loop running clean.
- **New:** `infra/chaos/redis-partition.yaml` — chaos-mesh `NetworkChaos` partitioning the Redis Sentinel master from backend pods for 2 minutes every 6 hours. Verifies Sentinel quorum failover, the Phase 2.2 keyspace listener reconnects after the partition heals, and `acquire_lease` returns `redis_unavailable_fallback` during partition (calls aren't refused).
- **New:** `infra/chaos/provider-429-storm.yaml` — application-layer throttle-proxy sidecar that returns 429 on 30% of requests for 5 minutes, simulating one provider-key getting rate-limited. Verifies KeyPool cooldown routing, ResilientLLM/TTS not tripping the circuit until ALL keys cool, and SLO holding through the storm.
- **New:** `backend/scripts/soak_runner.sh` — weekly 4-hour soak driver. Applies chaos manifests, runs `loadtest_calls.py` in the background, snapshots Prometheus every 5 minutes, captures results to `./soak-results/<timestamp>/`, exits non-zero on failure (suitable for cron / CI).

### 4.4 1000-concurrent certification harness
- **New:** `backend/scripts/certify_1000.py` — drives the 60-minute 1000-concurrent soak via `loadtest_calls.py`, queries Prometheus for the three pass criteria (p95 turn ≤ 2.0s, max saturation ≤ 90 %, zero platform-attributable drops), prints a pass/fail table, writes `certify-results/certify_<timestamp>.json` for the audit trail. Returns 0 on full pass, non-zero on any failure — can be called from CI.

The harness deliberately doesn't manage chaos experiments — run them in parallel via `soak_runner.sh` for chaos-during-certify, or run pristine for a clean architectural number.

---

## How to verify

1. **Unit tests:**
   ```
   cd backend && ./venv/bin/python -m pytest tests/unit/ -q
   ```
   Result: **1164 passed, 12 skipped** (up from 1149 — 6 resilient LLM + 9 cost ledger tests integrated cleanly).

2. **Migration syntax:**
   ```
   psql -d talkyai_dev -f backend/database/migrations/20260505_add_tenant_provider_cost_events.sql --dry-run
   ```

3. **Cost ledger end-to-end (live DB):** start the backend, originate a call, observe rows arrive in `tenant_provider_cost_events` within the flush interval (default 5s).

4. **Chaos manifests:** apply individually against a Phase 3 cluster; verify each scenario's pass criteria from the comments.

5. **Final certification:**
   ```
   PROM_URL=https://prom.talky.example.com:9090 \
   BASE_URL=https://nginx.talky.example.com    \
       ./venv/bin/python backend/scripts/certify_1000.py \
           --concurrent 1000 --duration 3600
   ```
   Pass = exit 0 + all three criteria green in the report.

---

## What 1000 concurrent looks like operationally

Following architecture_plan.md §Capacity Model (1000-concurrent column) the production deployment is:

| Resource | Provisioned | Source of capacity |
|---|---|---|
| Backend pods | 35-40 | helm chart `replicas` + HPA on `talky_call_saturation_pct` |
| Postgres | Managed primary + 2 read replicas, PITR | RDS / Aiven / Cloud SQL |
| PgBouncer | 3 pods, transaction pool 200 each | k8s Deployment |
| Redis | Cluster mode, 3 shards × 2 replicas | ElastiCache / Upstash / managed |
| Twilio trunks | 2× 2000 channels in 2 Edge Locations | infra/twilio/multi-region.md |
| Groq | 4-6 keys, Enterprise dedicated | infra/helm secret talky-providers |
| ElevenLabs | 6-8 keys, Enterprise contract | same |
| Cartesia | 4 keys, Enterprise tier | same |
| Deepgram | 1 key, concurrency uplift to 1500 | same |
| Google TTS | 5 GCP projects with rotating service accounts | external |
| Observability | Prometheus + Grafana + PagerDuty | Phase 3.4 dashboards + alerts |

Architecture is unchanged from Phase 3 — only capacity numbers and provider tiers move. **That was the point of the whole roadmap.**

---

## Files touched (Phase 4)

New (10):
```
infra/twilio/multi-region.md
infra/chaos/pod-kill.yaml
infra/chaos/redis-partition.yaml
infra/chaos/provider-429-storm.yaml
backend/scripts/soak_runner.sh
backend/scripts/certify_1000.py
backend/app/domain/services/resilient_llm.py
backend/app/domain/services/provider_cost_ledger.py
backend/database/migrations/20260505_add_tenant_provider_cost_events.sql
backend/tests/unit/test_resilient_llm.py
backend/tests/unit/test_provider_cost_ledger.py
backend/doc/architecture/phase4_complete.md
```

Modified (1):
```
backend/app/main.py        (cost ledger flusher in lifespan startup/shutdown)
```

---

## Final status across all phases

| Phase | Goal | Tests added | Status |
|---|---|---|---|
| 1 | Single-pod foundations | +18 | done |
| 2 | Horizontal scale ready | +6 | done |
| 3 | Production-grade infra | +5 | done |
| 4 | 1000-call hardening | +15 | done |

**Total new tests across all phases: 44.** **Suite: 1164 passed, 12 skipped.**

The system as it stands satisfies the architectural roadmap from `architecture_plan.md`. Moving from 50 → 1000 concurrent is now a *capacity* operation — keys, pods, plan tiers — not a re-architecture.

---

## What remains operational, not code

These are the sign-off items that need real infrastructure to verify; they're outside the scope of an offline phase:

1. Provision the managed Postgres / Redis / k8s cluster.
2. Deploy the helm chart, secrets, NGINX, PgBouncer.
3. Buy provider keys per `provider_key_provisioning.md` and feed them via the `talky-providers` k8s secret.
4. Configure Twilio trunks per `infra/twilio/multi-region.md`.
5. Wire alerts to PagerDuty / Opsgenie integration key.
6. Run `certify_1000.py` for the canonical sign-off run; archive the JSON report.

Each of these has a one-page runbook in `infra/`; together they're the production-readiness handover.
