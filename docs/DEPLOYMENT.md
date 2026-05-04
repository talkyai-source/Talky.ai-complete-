# Deployment

How to get Talky.ai running in production. For architecture context see
[ARCHITECTURE.md](./ARCHITECTURE.md). For incident response see
[RUNBOOK.md](./RUNBOOK.md).

## Targets covered

1. **Docker Compose on a single VPS** — fastest path, suitable for staging
   and small-scale prod. Documented end-to-end below.
2. **Kubernetes** — recommended for multi-node prod. Manifests not yet
   in-tree; see "Migrating to Kubernetes" section for pointers.

## Prerequisites

- Linux host (Ubuntu 22.04 / Debian 12 tested), 4+ vCPU, 8+ GB RAM, 50+ GB disk
- Docker 24+ and Docker Compose v2
- Public DNS pointing at the host; TLS terminated by a reverse proxy
  (Caddy, nginx, Traefik, or a cloud load balancer)
- Outbound network access to your AI providers (Deepgram, Groq, Cartesia, etc.)
- A SIP trunk / carrier connection (if running telephony)

## 1. Docker Compose deploy

### 1.1. Clone & configure

```bash
git clone <repo> talky && cd talky

# Root-level compose env (DB / Redis credentials)
cp .env.example .env
$EDITOR .env                    # set strong POSTGRES_PASSWORD, REDIS_PASSWORD

# Backend application config
cp backend/.env.example backend/.env
$EDITOR backend/.env            # JWT_SECRET, AI provider keys, etc.
```

**Generate strong secrets:**

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use this for `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `JWT_SECRET`, `KMS_MASTER_KEY`.

### 1.2. Pre-flight checks

```bash
# Validate compose file
docker compose config -q

# Ensure .env is NOT tracked by git
git check-ignore .env backend/.env       # both should print the path
```

### 1.3. Bring the stack up

```bash
docker compose up -d --build

# Watch boot logs — backend should pass the prod-gate check then become healthy
docker compose logs -f backend
```

Healthy when `docker compose ps` shows `(healthy)` for postgres, redis, and backend.

### 1.4. Run database migrations

```bash
docker compose exec backend alembic upgrade head
```

### 1.5. Smoke test

```bash
curl -fsS http://localhost:8000/health                   # → {"status":"ok"}
curl -fsS http://localhost:8000/api/v1/health | jq       # → dependency statuses
```

### 1.6. Put a TLS terminator in front

Compose binds postgres and redis to `127.0.0.1` only — they should never be
public. The backend listens on `${BACKEND_PORT:-8000}`. Front it with one of:

**Caddy (simplest, automatic Let's Encrypt):**

```caddy
api.example.com {
    reverse_proxy localhost:8000
}
```

**nginx:** see `RUNBOOK.md → TLS` for a sample server block.

Once HTTPS is live, **bump `ENVIRONMENT=production` in `.env`** so the
backend emits `Strict-Transport-Security` and `prod_gate` enforces strict
config validation.

### 1.7. Telephony (optional)

Telephony media plane runs in a separate compose file:

```bash
docker compose -f backend/docker-compose-freeswitch.yml up -d
```

Configure SIP trunk credentials in `backend/config/sip_config.yaml`.

## 2. Routine operations

| Task | Command |
|---|---|
| Tail logs | `docker compose logs -f backend` |
| Restart backend only | `docker compose restart backend` |
| Update to a new image | `git pull && docker compose up -d --build backend` |
| Run a one-off shell | `docker compose exec backend bash` |
| Take a DB backup | see [RUNBOOK.md → Backups](./RUNBOOK.md#backups) |
| Stop everything | `docker compose down` (volumes preserved) |

## 3. Migrating to Kubernetes

When you outgrow a single host:

1. Push images to a registry (ECR / GHCR / Docker Hub) — your CI already
   builds them; add a push step
2. Move to a managed Postgres (RDS / Cloud SQL / Crunchy Bridge) — single-node
   compose Postgres has no HA
3. Move to a managed Redis (ElastiCache / Memorystore / Upstash)
4. Translate `docker-compose.yml` services into k8s `Deployment` + `Service`
   manifests, or use Helm. Suggested chart structure:
   ```
   deploy/helm/talky/
     ├── Chart.yaml
     ├── values.yaml
     └── templates/{deployment,service,ingress,configmap,secret}.yaml
   ```
5. Replace `.env` with a real secrets manager (AWS Secrets Manager / Vault /
   Sealed Secrets). The application already reads everything via
   `app/core/config.py`, so this is a deployment-side change, not code.
6. Wire the Prometheus `/metrics` endpoint to your monitoring stack
7. Wire OTLP exporter (`opentelemetry-exporter-otlp-proto-grpc`) to Tempo / Jaeger / Datadog

## 4. Pre-production checklist

Before flipping `ENVIRONMENT=production`:

- [ ] All secrets are strong, randomly generated, and **not** committed
- [ ] `.env` and `backend/.env` are in `.gitignore` and not pushed to the registry
- [ ] DB backups configured and tested (`pg_dump` restore dry-run)
- [ ] TLS in front of backend; HSTS verified
- [ ] CORS `allowed_origins` restricted to your real domains
- [ ] Sentry DSN configured — error reporting working end-to-end
- [ ] OTLP exporter pointed at your tracing backend
- [ ] Prometheus scraping `/metrics`; basic alerts (5xx rate, latency, DB pool exhaustion)
- [ ] On-call rotation set up (PagerDuty / Opsgenie)
- [ ] [RUNBOOK.md](./RUNBOOK.md) reviewed by whoever's on call
- [ ] Load test run against staging at expected peak QPS
- [ ] `prod_gate` check passes on boot (no warnings)

## 5. Rollback

```bash
# Quick: redeploy a known-good git ref
git checkout <previous-good-sha>
docker compose up -d --build backend

# Database: only roll back if the previous app version is compatible
docker compose exec backend alembic downgrade -1
```

**Test downgrades in staging first.** A failed migration rollback is much
worse than a failed forward migration.
