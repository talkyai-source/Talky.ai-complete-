# Runbook

Operator playbook for production Talky.ai. For architecture see
[ARCHITECTURE.md](./ARCHITECTURE.md); for deploy steps see
[DEPLOYMENT.md](./DEPLOYMENT.md).

> Production is a single Hetzner box: the API, three Python workers and the C++
> gateway are **systemd units** from the git checkout at `/opt/talky`; Postgres
> and Redis are **docker compose** containers from `/opt/talky/docker-compose.yml`;
> telephony is **Asterisk only**. Reach it with
> `ssh -i ~/.ssh/talky_admin admins@144.76.17.150`.

## Units and their journals

| Unit | Kind | `journalctl` identifier |
|---|---|---|
| `talky-api` | daemon (uvicorn, `--workers 1`) | `talky-api` |
| `talky-dialer-worker` | daemon, `Type=notify`, `WatchdogSec=180` | `talky-dialer` |
| `talky-voice-worker` | daemon, `Type=notify`, `WatchdogSec=180` | `talky-voice` |
| `talky-reminder-worker` | daemon, `Type=notify`, `WatchdogSec=180` | `talky-reminder` |
| `talky-voice-gateway` | daemon (C++), runs as `admins` | `talky-voice-gateway` |
| `talky-migrate` | oneshot, deploy-triggered | `talky-migrate` |
| `talky-cleanup` | oneshot via `talky-cleanup.timer` (03:00 nightly) | `talky-cleanup` |
| `talky-healthwatch` | oneshot via `talky-healthwatch.timer` (every 2 min) | `talky-healthwatch` |
| `talky-trunk-status` | oneshot via `talky-trunk-status.timer` (every 15 s) | — |
| `talky.target` | groups API + the three workers | — |

Timer-activated oneshots sitting `inactive (dead)` between runs is the
**expected** state. Reading that as a fault is the most common misdiagnosis on
this box.

## On-call quick reference

```bash
systemctl status talky-api                       # one unit
systemctl list-units 'talky-*' --all --no-pager --plain

journalctl -u talky-api -f                       # follow one unit
journalctl -u talky-voice-worker --since '15 min ago'
journalctl -p err --since '1 hour ago'           # everything at error and above
```

| | |
|---|---|
| **Liveness** | `curl http://127.0.0.1:8000/health` |
| **Readiness** | `curl http://127.0.0.1:8000/api/v1/healthz/ready` |
| **Deep** | `curl http://127.0.0.1:8000/api/v1/healthz/deep` |
| **Workers** | `curl http://127.0.0.1:8000/api/v1/healthz/workers` |
| **Gateway** | `curl localhost:18080/stats` |
| **Datastores** | `docker compose -f /opt/talky/docker-compose.yml ps` |

Every log line is tagged `[req=<uuid>]`. When a user reports a bug, ask for the
`X-Request-ID` response header and grep the journals for it.

`talky-healthwatch.timer` polls `/api/v1/healthz/workers` every 2 minutes via
`backend/deploy/healthwatch.sh` and writes a `HEALTHWATCH CRITICAL` line at
severity `err` when any worker is unhealthy, so `journalctl -p err` catches it.
A non-200 also fails the unit — a second, independent signal.

## Common incidents

### 🔴 API returning 5xx

```bash
systemctl status talky-api
journalctl -u talky-api -n 500 --no-pager
sudo systemctl restart talky-api          # last resort; drains via lifespan
```

If DB- or Redis-shaped, go to the datastore sections below. Check Sentry for
spike grouping.

### 🔴 A worker is dead or hung

The three Python workers are `Type=notify` with `WatchdogSec=180`: a worker
whose event loop stops petting the watchdog is force-restarted, and crash-loops
are capped at 5 starts per 300 s before the unit enters `failed`.

```bash
systemctl status talky-dialer-worker
journalctl -u talky-dialer-worker -n 300 --no-pager
sudo systemctl reset-failed talky-dialer-worker    # after fixing the cause
sudo systemctl restart talky-dialer-worker
curl http://127.0.0.1:8000/api/v1/healthz/workers
```

A unit stuck in `failed` after hitting the crash-loop cap will **not** come back
on its own. `reset-failed` is required.

### 🔴 Postgres unhealthy

Postgres is the compose container `talky-postgres-1` (`postgres:15-alpine`,
bound to `127.0.0.1:5432`).

```bash
docker compose -f /opt/talky/docker-compose.yml ps
docker compose -f /opt/talky/docker-compose.yml exec postgres pg_isready -U talky
docker compose -f /opt/talky/docker-compose.yml logs --tail=200 postgres
```

Common causes:
- Disk full → `df -h`; the data lives in the `postgres_data` volume
- Pool exhausted → `TimeoutError` from asyncpg in the journals; pool config is
  in `app/core/db.py`
- Lock contention → `SELECT * FROM pg_stat_activity WHERE state='active' ORDER BY query_start;`

### 🔴 Redis unhealthy

```bash
(
  set -Eeuo pipefail
  read -rsp 'Redis password: ' REDISCLI_AUTH && printf '\n'
  export REDISCLI_AUTH
  docker compose -f /opt/talky/docker-compose.yml exec -T \
    -e REDISCLI_AUTH redis redis-cli ping
  docker compose -f /opt/talky/docker-compose.yml exec -T \
    -e REDISCLI_AUTH redis redis-cli info memory
)
docker compose -f /opt/talky/docker-compose.yml logs --tail=200 redis
```

After a password change, restart the API so it reloads the URL:
`sudo systemctl restart talky-api`.

### 🔴 Calls not connecting

Telephony is **Asterisk only** — there is no FreeSWITCH, OpenSIPS, Kamailio or
rtpengine process on this box.

```bash
systemctl status asterisk
pgrep -a -f asterisk
systemctl status talky-voice-gateway && curl localhost:18080/stats
sudo systemctl start talky-trunk-status.service   # refresh SIP trunk evidence
journalctl -u talky-voice-worker -n 300 --no-pager
```

Also check the global cap in
`app/domain/services/telephony_concurrency_limiter.py`.

### 🔴 Migration failed during deploy

`deploy_to_server.sh` starts `talky-migrate.service` **before** restarting the
app, so a failed migration blocks the restart rather than shipping code against
an unmigrated schema.

```bash
systemctl status talky-migrate
journalctl -u talky-migrate -n 200 --no-pager
```

Fix forward if you can. If you must go back, see
[DEPLOYMENT.md → Rollback](./DEPLOYMENT.md#rollback) — and read the warning
there about the schema not rolling back with the code.

### 🔴 Auth requests failing

- `JWT_SECRET` must be stable; rotating it invalidates every active session —
  coordinate a window
- Lockouts firing too aggressively → `app/core/security/lockout.py`
- WebAuthn → check origin / RP ID match between frontend and config

## Backups

Postgres runs in the compose container, so backups go through it:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
backup_dir="${TALKY_BACKUP_DIR:-/var/backups/talky}"
sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 0700 "${backup_dir}"
dump="${backup_dir}/talky-$(date -u +%Y%m%dT%H%M%SZ).dump"
partial="${dump}.partial"
checksum_file="${dump}.sha256"
checksum_partial="${checksum_file}.partial"
for output_path in "$dump" "$partial" "$checksum_file" "$checksum_partial"; do
  if [[ -e "$output_path" ]]; then
    echo "ERROR: refusing to overwrite existing backup artifact: $output_path" >&2
    exit 1
  fi
done
published=0
cleanup_partial() {
  rc=$?
  if [[ -n "${partial:-}" && -e "$partial" ]]; then
    rm -f -- "$partial"
  fi
  if [[ -n "${checksum_partial:-}" && -e "$checksum_partial" ]]; then
    rm -f -- "$checksum_partial"
  fi
  if [[ "$rc" -ne 0 && "${published:-0}" -eq 1 ]]; then
    rm -f -- "$dump" "$checksum_file"
  fi
  exit "$rc"
}
trap cleanup_partial EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker compose -f /opt/talky/docker-compose.yml exec -T postgres \
  pg_dump -U talky -d talky --no-owner --format=custom \
  > "${partial}"
test -s "${partial}"
docker compose -f /opt/talky/docker-compose.yml exec -T postgres \
  pg_restore --list < "${partial}" >/dev/null
digest="$(sha256sum "${partial}" | cut -d ' ' -f1)"
[[ "$digest" =~ ^[0-9a-f]{64}$ ]]
printf '%s  %s\n' "$digest" "$dump" > "${checksum_partial}"
published=1
mv -- "${partial}" "${dump}"
partial=""
mv -- "${checksum_partial}" "${checksum_file}"
checksum_partial=""
sha256sum --check "${checksum_file}"
trap - EXIT INT TERM
```

Retain 30 days locally and ship off-host with server-side encryption. **Test
restores monthly** — an untested backup is a hope, not a backup. Restore only
into a newly created, isolated database; never use the production `talky`
database as the restore target:

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
cd /opt/talky
backup_dir="${TALKY_BACKUP_DIR:-/var/backups/talky}"
backup="${backup_dir}/talky-YYYYMMDDTHHMMSSZ.dump"
restore_db="talky_restore_$(date -u +%Y%m%dT%H%M%SZ)"
evidence="${backup_dir}/${restore_db}-compatibility.txt"
restore_created=0
cleanup_restore() {
  rc=$?
  if [[ "$restore_created" -eq 1 ]]; then
    if ! docker compose -f /opt/talky/docker-compose.yml exec -T postgres \
      dropdb -U talky --if-exists --force "${restore_db}"; then
      echo "ERROR: failed to remove isolated restore DB ${restore_db}" >&2
      rc=1
    fi
  fi
  exit "$rc"
}
trap cleanup_restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

date -u +%FT%TZ | tee "${evidence}"
sha256sum --check "${backup}.sha256" | tee -a "${evidence}"
docker compose -f /opt/talky/docker-compose.yml exec -T postgres \
  createdb -U talky -O talky --template=template0 "${restore_db}"
restore_created=1
docker compose -f /opt/talky/docker-compose.yml exec -T postgres \
  pg_restore -U talky -d "${restore_db}" --exit-on-error --no-owner \
  < "${backup}"
docker compose -f /opt/talky/docker-compose.yml exec -T postgres \
  psql -U talky -d "${restore_db}" -v ON_ERROR_STOP=1 \
  -c 'SELECT version_num FROM alembic_version;' \
  -c 'SELECT count(*) AS calls FROM calls;' | tee -a "${evidence}"

# Uses the current application pool/import path against the isolated DB,
# verifies the exact repository Alembic head and representative query
# contracts, and refuses production-like database names. It never migrates.
backend/venv/bin/python backend/scripts/verify_restore_compatibility.py \
  --database "${restore_db}" | tee -a "${evidence}"
date -u +%FT%TZ | tee -a "${evidence}"
```

Record backup size/checksum, restore start/end time, schema version,
representative table counts, and the application compatibility smoke test
result. The trap drops only the isolated `talky_restore_*` database and makes
cleanup failure a failed drill; preserve the evidence file off-host. A daily
logical dump provides at best a roughly 24-hour Postgres RPO; the release owner
must approve explicit RTO/RPO targets and add WAL-based recovery if that RPO is
insufficient.

Redis AOF at `everysec` gives roughly a 1 s RPO. For a snapshot:

```bash
# REDISCLI_AUTH is inherited into the container by name; its value is never a
# redis-cli/docker argv item or shell-history token.
(
  set -Eeuo pipefail
  read -rsp 'Redis password: ' REDISCLI_AUTH && printf '\n'
  export REDISCLI_AUTH
  docker compose -f /opt/talky/docker-compose.yml exec -T \
    -e REDISCLI_AUTH redis redis-cli BGSAVE
)
```

For tighter RPO than 24 h, enable WAL archiving (`wal-g` / `pgBackRest`) or move
to managed Postgres.

## Data retention

`talky-cleanup.timer` runs `app.workers.cleanup_worker` nightly at 03:00,
trimming `call_events` / `stream_events` / `call_legs`.

**It is in dry-run by default** (`CLEANUP_DRY_RUN=true`). Only flip it to
`false` once the retention windows are signed off.

```bash
systemctl list-timers 'talky-*' --no-pager
journalctl -u talky-cleanup --since yesterday
```

## Rotations

| Secret | Cadence | Procedure |
|---|---|---|
| `JWT_SECRET` | 90 days | Coordinate a window; **all sessions invalidate**. Plan support coverage. |
| `KMS_MASTER_KEY` | 365 days, or on suspected compromise | Re-encrypt sensitive columns (`app/core/kms.py`). **Do not rotate without a tested re-encryption job.** |
| AI provider keys | per provider | Edit `/opt/talky/backend/.env`, then `sudo systemctl restart talky-api talky-voice-worker` |
| DB / Redis passwords | 180 days | Edit the compose env, recreate the containers, restart all units, verify **before** revoking the old credential |
| TLS certs | monitor expiry | Alert at 14 days out or more |

Every unit reads `EnvironmentFile=/opt/talky/backend/.env`, so any `.env` change
requires a restart of the units that consume it — systemd does **not** reload it
on its own.

## Escalation

1. **Sev 1** (full outage): page on-call, then engineering lead, then CTO
2. **Sev 2** (degraded): on-call investigates, opens an incident channel
3. **Sev 3** (single feature): ticket, fix in the next deploy window

Document every Sev 1 in a post-mortem within 5 business days — blameless,
focused on systemic causes, with action items tracked to completion.

---

## Local development

For a laptop stack (full docker compose, including a `backend` container that
does **not** exist in production) see
[DEPLOYMENT.md → Local development](./DEPLOYMENT.md#local-development-docker-compose).
