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

Use `talky-db-backup.service`, triggered by the existing
`talky-db-backup.timer`, for scheduled and manual Postgres backups. The service
runs [db-backup.sh](../backend/deploy/db-backup.sh) and its adjacent
[backup_snapshot.py](../backend/deploy/backup_snapshot.py); production defaults
are container `talky-postgres-1`, database `talkyai`, and role `talkyai`.
Inspect the installed unit before a manual run: a reviewed backup-only
maintenance release may pin `ExecStart` to an immutable worktree while retaining
`WorkingDirectory=/opt/talky/backend`. Do not substitute a bare `pg_dump` command.

The root-owned configuration `/etc/talky/db-backup.env` must have mode `0600` and
contain an explicitly selected **existing**, active, verified platform-admin
UUID:

```ini
TALKY_BACKUP_PRINCIPAL_ID=<existing-platform-admin-UUID>
```

Use `sudoedit` to configure that file, then verify its ownership and permissions.
It is backup process context, not an account-creation step or a database role
promotion. Do not place a password, token or MFA secret in it. The service's
optional `EnvironmentFile` allows installation before configuration; the backup
itself fails when the principal is missing or invalid.

The helper opens a read-only repeatable-read transaction, sets the tenant bypass
and explicit principal GUCs, validates that principal, and exports the snapshot
used by `pg_dump --enable-row-security`. The keeper holds that same snapshot
until the dump finishes. This includes saved acknowledgement receipts (and
supplier evidence when that schema is installed) whose policies require an
active verified administrator in addition to tenant bypass. A concurrent role
change or deactivation cannot silently filter those rows from the validated
snapshot. Policies and role grants remain unchanged. Invalid principal, dump
failure or keeper failure prevents publication and retention; partial files are
removed. Container and host deadlines bound the operation.

For an on-demand run, first confirm the one-shot service is inactive; use the
service so its reviewed executable and private configuration are applied:

```bash
sudo systemctl show talky-db-backup.service -p FragmentPath -p ExecStart -p ActiveState
sudo systemctl start talky-db-backup.service
sudo systemctl show talky-db-backup.service -p Result -p ExecMainStatus
sudo journalctl -u talky-db-backup.service -n 30 --no-pager
systemctl list-timers talky-db-backup.timer --no-pager
```

Success publishes `/var/backups/talky/talky-<UTC stamp>.dump` and `.dump.sha256`
(default directory), checks the checksum and archive table-data entries, and
retains 30 days by default. A dump below half the previous size is deliberately
published but exits nonzero with a critical shrink alert; investigate it rather
than treating that exit as proof no archive exists. Table-data entry counts do
not prove every row is present. Keep dump contents and logs private.

### Restore test

Test restores monthly and before a release that changes recovery assumptions.
The tested production method uses a **new, isolated PostgreSQL cluster from the
exact production image ID**, not another database in the production container.
In the recorded production drill, the live cluster had no superuser role;
trusted extension restoration failed because its bootstrap role was demoted.
Do not promote the production
application role, use single-user mode, or change production privileges to make
a drill pass.

Use the reviewed, source-bound recovery procedure recorded in the
[remaining remediation session](sessions/2026-10-27-admin-remaining-remediation.md).
Its required boundaries are:

1. Bind the private archive to its exact path, size and SHA-256. Capture source
   schema heads, role flags, memberships, locale, extension versions/member
   ownership and ACLs, and selected counts in a read-only transaction. Record
   that this later observation is separate from the dump snapshot.
2. Create one uniquely owned container and volume with the same image, no
   network or published ports, bounded resources, and a Unix socket under a
   root-only `0700` directory. Mount only its new data volume, private socket,
   temporary initialization secret and read-only archive; never the production
   data volume. Bind evidence to the actual created container identity.
3. Recreate source non-system role metadata without copying passwords. Initialize
   the isolated bootstrap role as `talkyai` to preserve extension ownership;
   create an isolated maintenance role before demotion. Restore the **full**
   custom archive with ownership and ACLs retained, `--exit-on-error` and a
   transaction. Do not filter archive entries or pass restore-time `--no-owner`
   or `--no-acl` to hide differences.
4. Demote the isolated application role to the captured source flags before
   application checks. Compare restored metadata and selected data to the
   captured baseline. Keep populated receipt/decision roundtrips separate from
   empty-table observations; representative counts are not full-content proof.
5. For candidate migration validation, use an immutable matching checkout and
   explicit clone-only `DATABASE_URL`, `READ_DATABASE_URL` and
   `MIGRATION_DATABASE_URL` bound to that same owned database. Prove the
   actual Unix-socket database, role and NOSUPERUSER/NOBYPASSRLS state with each
   client before any migration; do not load a production `.env`. Upgrade and
   repeat only in that clone, then run the candidate runtime privilege and
   [restore compatibility](../backend/scripts/verify_restore_compatibility.py)
   checks. The compatibility checker does not migrate. Preserve selected full
   tenant/billing/receipt hashes across migration and record their row counts.
6. Preserve reports on failure. Close owned connections and remove only the
   recorded container and its exact owned volume after identity checks, plus
   the exact private temporary secret. Record cleanup and unchanged production
   service identities; never use a production-cluster `dropdb --force` shortcut.

Record archive size/checksum, start/end times, image and source/candidate schema
heads, scope of data comparisons, application checks, failures and exact cleanup.
Ship backups and evidence off-host with encryption; local restore success does
not establish that off-host recovery is configured. A daily logical dump gives
at best roughly a 24-hour Postgres RPO. Explicit RPO/RTO targets and measured
recovery times remain release-owner requirements; add WAL-based recovery if
that RPO is insufficient.

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
