# Separate production database: architecture, cut-over and recovery

Production deliverable 4. The tools are in [scripts/database](../../scripts/database/README.md);
the restore background is in [database recovery](database-recovery.md). This page is the
runbook: where the database goes, how the application is protected, how to move
today's data without touching today's database, and how to recover.

## Today and target

| | Today (2026-10-28) | Target |
|---|---|---|
| Location | Docker container `talky-postgres-1` on the application server, `127.0.0.1:5432` (PostgreSQL 15.17) | A managed PostgreSQL service (preferred) or a dedicated database server, reached over a private network or IP allow-list |
| Transport | Plaintext on loopback | TLS with certificate verification (`sslmode=verify-full`) |
| Application role | `talkyai`: NOSUPERUSER NOBYPASSRLS, but **owns every table** (can ALTER/DROP them or switch RLS off) | `talklee_app`: row DML only, owns nothing; `talklee_owner` owns the schema and is used only by migrations |
| Backups | Nightly `pg_dump` on the **same disk**; `assistant_action_resolutions` dumped **empty** (see below) | Provider PITR/snapshots (or WAL archive + base backups) **plus** checked logical bundles copied off-host, 30-day retention |
| Monitoring | Journal only, nobody alerted | `monitor.py` every 5 min + backup health, alerts to a webhook; provider alerts |

```
  Application server(s)                         Database server / managed service
  ┌──────────────────────────┐   TLS 1.2+       ┌─────────────────────────────────┐
  │ talky-api, workers       │  verify-full     │ PostgreSQL                      │
  │  DATABASE_URL ──────────────────────────────▶  talklee_app   (runtime, DML)   │
  │ operator only:           │  allow-listed    │  talklee_owner (migrations)     │
  │  MIGRATION_DATABASE_URL ────────────────────▶  talklee_backup (SELECT)        │
  │ backup + monitor timers ────────────────────▶  WAL archive / provider PITR    │
  │ off-host mount ◀── bundles│                  └─────────────────────────────────┘
  └──────────────────────────┘
```

## Choosing the database host (owner decision)

**Managed PostgreSQL (preferred).** Requirements: PostgreSQL 15 or 16; private
networking or trusted-source IP allow-listing; TLS with a downloadable CA
certificate; automated daily backups with at least 7 days of point-in-time
recovery; storage, CPU and connection alerts; an administrator that can create
roles and databases (CREATEROLE/CREATEDB is enough; superuser is not needed).
The tools were rehearsed with exactly that kind of non-superuser administrator.

**Dedicated server (acceptable).** A second machine in the same region on a
private network. `scripts/database/db_server/install_db_server.sh` installs
PostgreSQL 16 with the rendered TLS-only, allow-listed configuration and a `ufw`
allow-list. PITR comes from `pitr.py` (WAL archive + weekly base backups); the
archive and bundles must be copied to storage outside that server.

Either way, record the choice and references (never secrets) in
[infrastructure-handover.example.json](infrastructure-handover.example.json).

## What the application now enforces

`backend/app/core/database_policy.py`, wired into pool start (API and workers)
and the production gate:

* In production, `DATABASE_URL` must be set (the pool otherwise falls back to a
  development default), and a `DATABASE_URL` / `READ_DATABASE_URL` whose host is
  not this machine must use `sslmode=verify-full` or `verify-ca`. Loopback and
  Unix sockets are exempt, so today's configuration is unaffected.
* Every pool start logs `db_runtime_posture` (role, superuser, bypass_rls,
  create_role, create_database, owned_relations, tls). With
  `DATABASE_REQUIRE_RESTRICTED_ROLE=true` it refuses to boot as a role that is a
  superuser, bypasses RLS, can create roles/databases or owns relations. Today it
  only warns (the current role owns the tables).
* `backend/deploy/infra/wait-for-deps` waits for the remote server's TCP port
  instead of the local container once `DATABASE_URL` points elsewhere.
* `talky-migrate.service` also loads the optional operator-only
  `/etc/talklee/secrets/migration.env`, so migrations run as the owner role.

## Cut-over runbook

Nothing below writes to today's database. The source is read through a
read-only snapshot (`default_transaction_read_only=on`); the target database must
not exist; the tools check the target is a different cluster.

**Preparation (any day before)**

1. Provision the database host (above). Put its CA certificate at
   `/etc/talklee/secrets/database-ca.pem` on the application server.
2. Create `/etc/talklee/secrets/pg_service.conf` from
   [the template](../../scripts/database/pg_service.conf.example) with services
   `talklee_admin` (administrator, `dbname=postgres`), `talklee_admin_db`
   (administrator, `dbname=talkyai`), `talklee_runtime`, `talklee_backup` and
   `talklee_source` (today's database: `host=127.0.0.1 port=5432 dbname=talkyai
   user=talkyai sslmode=disable`, loopback only). Passwords go in a 0600
   `PGPASSFILE`, never in commands.
3. Generate the three role passwords:
   `python3 scripts/database/roles.py generate-password --output /etc/talklee/secrets/<role>.password`.
4. Pick the platform-admin user id used to read the admin-only table: an
   active, verified `platform_admin` in `user_profiles`.

**Rehearsal (the day before, no downtime)**

5. Take a source bundle while production runs:
   ```bash
   python3 scripts/database/recovery.py --allow-local-plaintext backup --service talklee_source \
     --rls-bypass-guc --rls-setting app.current_user_id=<platform admin id> \
     --destination /var/backups/talklee/cutover-rehearsal
   ```
6. Load it into a rehearsal database on the new host, then drop it after review:
   `cutover.py load --database talky_restore_rehearsal ...` (same flags as step 9).
   Record the measured duration: it sets the maintenance window.

**Cut-over window**

7. Announce the window. Pause campaigns, stop new calls and wait until no call
   is active (`/api/v1/calls/live` empty), then stop `talky-dialer-worker`,
   `talky-voice-worker`, `talky-reminder-worker`, `talky-api-web` and `talky-api`.
8. Final source bundle (step 5 with `--destination /var/backups/talklee/cutover`).
9. Load and verify:
   ```bash
   python3 scripts/database/cutover.py --service talklee_admin load \
     --bundle /var/backups/talklee/cutover --database talkyai \
     --expected-head <alembic head of the deployed release> \
     --rls-setting app.current_user_id=<platform admin id> \
     --receipt /var/backups/talklee-evidence/cutover.json
   python3 scripts/database/roles.py --service talklee_admin_db apply --database talkyai \
     --owner-password-file ... --runtime-password-file ... --backup-password-file ... \
     --receipt /var/backups/talklee-evidence/roles.json
   ```
   Both must print `"status": "loaded_and_verified"` / `"passed"`.
10. In `/opt/talky/backend/.env` (back it up first) set
    `DATABASE_URL=<cutover receipt template with the talklee_app password, URL-encoded>`
    and `DATABASE_REQUIRE_RESTRICTED_ROLE=true`. Put `MIGRATION_DATABASE_URL`
    (the owner template) only in `/etc/talklee/secrets/migration.env` (root,
    0600): `talky-migrate.service` layers it over `.env`, so deploys keep
    migrating as the owner while the services run as the restricted role.
11. Start the services. Check: `journalctl -u talky-api | grep db_runtime_posture`
    shows `restricted=True ... tls=True`; `/health` and `/api/v1/healthz/deep`
    return 200; log in, open campaigns and calls, place one controlled test call.
12. Enable the new timers: `talklee-db-backup`, `talklee-db-monitor` (and
    `talklee-db-basebackup` on a dedicated server), and disable the old
    `talky-db-backup` timer.

**Rollback** (until new writes matter): stop the services, restore the backed-up
`.env`, start them. The old database was never written, so it is exactly as at
step 7. Rows written on the new server after step 11 must be reconciled by hand
before rolling back, so decide go/no-go during step 11. Keep the old container
(stopped) for at least 14 days before removing it.

## Backups and retention

| Layer | What | Retention |
|---|---|---|
| Provider PITR (managed) or WAL archive + base backup (`pitr.py`) | Physical, any second in the window | 7 days PITR (proposed); weekly base backups, 4 kept |
| Logical bundles (`scheduled_backup.py`, daily 02:00 UTC) | Snapshot-consistent `pg_dump`, every table counted, checksummed | 30 days, never fewer than the newest 7 |
| Off-host replica (`--replica-root`) | Byte-verified copy of each bundle | Same policy, applied separately |

Complete backups under row-level security need either a BYPASSRLS backup role or
`--rls-bypass-guc --rls-setting app.current_user_id=<platform admin id>`; the tool
refuses rather than write a filtered dump.

**Finding (today's server):** `backend/deploy/db-backup.sh` dumps through
`app.bypass_rls` only. Migration 0062 deliberately keeps
`assistant_action_resolutions` out of that bypass, so every nightly dump so far
contains that table with **no rows**, and its checks cannot notice. The script now
accepts `TALKY_BACKUP_PLATFORM_ADMIN_ID` to include it and warns every night
until that is set.

## Monitoring and alerts

`talklee-db-monitor.timer` runs `monitor.py` every five minutes as the runtime
role: connection and latency, TLS, restricted role, size against the provisioned
capacity (warning 80 %, critical 90 %), connections (80 % / 95 %), transactions
open longer than 5 / 30 minutes, wraparound, WAL archiving and backup freshness
(26 h). Alerts go to `TALKLEE_ALERT_WEBHOOK_URL` (Slack-compatible) on a new or
worse problem, hourly while it lasts and once when it clears; a Prometheus
textfile is written for the node exporter. Set `--capacity-bytes` to the real
plan or disk size. Also enable the provider's own storage/CPU/connection alerts.
A restricted role cannot see other sessions' transaction start times; grant the
monitor's role `pg_monitor` (read-only statistics) for a complete
long-transaction check.

## Recovery procedures

* **Application server lost:** rebuild it with the installation script and point
  it at the database (unchanged). No data restore.
* **Database server lost or data corrupted:** managed: restore the instance to a
  point in time in the provider console, then update `DATABASE_URL` if the host
  changed. Dedicated: `pitr.py restore --target-time ...` onto a new data
  directory, or load the latest logical bundle with `cutover.py load` into a new
  server (data up to the last nightly bundle).
* **Prove a backup (monthly):** restore the newest bundle into a scratch database
  on a separate server with `recovery.py restore` (exact counts, heads,
  tenant-isolation probe), then `cutover.py load` it as an application-ready copy
  and point a staging instance at it.

Recovery objectives (proposed, owner to approve): data loss ≤ 5 minutes with
PITR (≤ 24 h from bundles alone); recovery ≤ 1 hour.

## Evidence and limits

Local evidence: `scripts/database/tests/local_separation_rehearsal.py` builds
today's production shape with the real schema and every migration, then performs
the whole sequence above on disposable loopback clusters (TLS-only allow-listed
target administered by a non-superuser, the application's own pool code as the
restricted role, backups, replica, retention, restore drill, PITR, monitoring)
and proves the source database is byte-for-byte unchanged in rows, schema,
policies, owners and roles. Unit tests cover each tool's refusals.

Not verified here: a real managed provider or second server, a real private
network or firewall, `install_db_server.sh` on Ubuntu, production data volume
and timings, and alert delivery to the real receiver. Those happen during the
rehearsal and cut-over above.
