# Runbook

Operator playbook for production Talky.ai. For architecture see
[ARCHITECTURE.md](./ARCHITECTURE.md). For first-time deploy see
[DEPLOYMENT.md](./DEPLOYMENT.md).

## On-call quick reference

| | |
|---|---|
| **Liveness** | `curl https://api.example.com/health` |
| **Readiness** | `curl https://api.example.com/api/v1/health \| jq` |
| **Deep health** | `/api/v1/admin/health/{detailed,workers,queues,database}` |
| **Metrics** | `https://api.example.com/metrics` (auth-gated) |
| **Logs** | `docker compose logs -f --tail=200 backend` |
| **Traces** | Tempo / Jaeger UI (filter by `request_id` from logs) |
| **Errors** | Sentry project dashboard |

Every log line is tagged with `[req=<uuid>]`. When a user files a bug,
ask them to copy the `X-Request-ID` response header — that ID grep'd
across logs gives you the full request path.

## Common incidents

### 🔴 Backend returning 5xx

1. `docker compose ps` — is `backend` healthy?
2. `docker compose logs --tail=500 backend` — look for stack traces
3. Check Sentry for spike grouping
4. If DB-related, jump to "Postgres unhealthy" below
5. If Redis-related, jump to "Redis unhealthy" below
6. Last resort: `docker compose restart backend` (drains gracefully via lifespan)

### 🔴 Postgres unhealthy

```bash
docker compose exec postgres pg_isready -U talky
docker compose exec postgres psql -U talky -d talky -c "SELECT 1"
docker compose logs --tail=200 postgres
```

Common causes:
- Disk full → `df -h` on host; postgres data volume at `talky_postgres_data`
- Connection pool exhausted → app logs show `TimeoutError` from asyncpg.
  Pool is configured in `app/core/db.py` (default max=20). Tune up if
  consistently saturated, or hunt for a leaked connection.
- Long-running query holding locks → `SELECT * FROM pg_stat_activity WHERE state='active' ORDER BY query_start;`

### 🔴 Redis unhealthy

```bash
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" ping
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" info memory
```

Common causes:
- Memory full + `allkeys-lru` evicting hot keys → bump `maxmemory` or scale up
- AOF file corrupted → `redis-check-aof --fix`
- Auth failure after password change → restart backend so it reloads `REDIS_URL`

### 🔴 High latency

1. Check OTel traces — which span is slow? (DB? LLM? STT?)
2. If DB: check pool wait time + slow query log
3. If LLM/STT/TTS provider: check provider status page; consider failover
   provider in `app/core/providers/` if one is configured
4. If across the board: host CPU / memory pressure — `docker stats`

### 🔴 Calls not connecting

1. Telephony stack health: `docker compose -f backend/docker-compose-freeswitch.yml ps`
2. SIP trunk reachable? `app/api/v1/endpoints/telephony_sip.py` exposes a probe
3. Check `app/domain/services/telephony_concurrency_limiter.py` — global cap may be hit
4. RTPengine media flow: check span errors for the call's `request_id`

### 🔴 Auth requests failing

- Token signing key change? `JWT_SECRET` must be stable; rotating invalidates
  every active session — coordinate via maintenance window
- Account lockouts firing too aggressively → see `app/core/security/lockout.py`
- WebAuthn issues → check origin / RP ID match between frontend & `webauthn` config

## Backups

### Postgres

**Daily logical backup (cron on the host):**

```bash
docker compose exec -T postgres \
  pg_dump -U talky -d talky --no-owner --format=custom \
  > "backups/talky-$(date +%Y%m%d).dump"
```

Retain 30 days locally + ship to off-host (S3 / R2 / Backblaze) with
server-side encryption. Test restores **monthly** — an untested backup is a
hope, not a backup:

```bash
docker compose exec -T postgres \
  pg_restore -U talky -d talky_restore_test --clean --if-exists \
  < backups/talky-YYYYMMDD.dump
```

**Point-in-time recovery (PITR):** for tighter RPO than 24 hours, enable
WAL archiving and use `wal-g` or `pgBackRest`. Out of scope for compose
deploys — move to managed Postgres when you need this.

### Redis

AOF on `everysec` already gives ~1s RPO. For full backups:

```bash
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" BGSAVE
docker compose cp redis:/data/dump.rdb backups/redis-$(date +%Y%m%d).rdb
```

## Rotations

| Secret | Cadence | Procedure |
|---|---|---|
| `JWT_SECRET` | 90 days | Coordinate window; rotate; **all sessions invalidate**. Plan support coverage. |
| `KMS_MASTER_KEY` | 365 days or on suspected compromise | Re-encrypt sensitive columns; see `app/core/kms.py`. **Do not rotate without a tested re-encryption job.** |
| AI provider keys | per provider policy | Update `backend/.env`, `docker compose up -d backend` (graceful restart) |
| DB & Redis passwords | 180 days | Update `.env`; full stack restart; verify before tearing down old creds |
| TLS certs | auto via Caddy / cert-manager | If manual, monitor expiry — alert ≥14 days out |

## Scaling

- **Backend horizontally**: stateless; add replicas behind a load balancer.
  Sticky sessions only needed for WebSocket bridges (call audio).
- **Postgres**: vertical first; then read replicas for analytics; then
  managed Postgres with HA before sharding.
- **Redis**: vertical first; then Redis Cluster if you outgrow one node.
- **Workers**: see `app/workers/`; scale separately from the API tier.

## TLS sample (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate     /etc/letsencrypt/live/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    # WebSocket upgrade for /api/v1/ws/voice/*
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host       $host;
        proxy_set_header X-Real-IP  $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID      $request_id;     # propagate to backend
        proxy_read_timeout 3600s;                           # long-lived call WS
    }
}
```

## Escalation

1. **Severity 1** (full outage): page on-call → engineering lead → CTO
2. **Severity 2** (degraded): on-call investigates, opens incident channel
3. **Severity 3** (single feature): ticket, fix in next deploy window

Document every Sev1 in a post-mortem within 5 business days. Blameless,
focused on systemic causes, with action items tracked to completion.
