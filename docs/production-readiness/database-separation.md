# Talklee production database: separation, protection and recovery

Production deliverable 4 ("Separate Production Database & Recovery") and the database part of
deliverable 5 ("Database location, backup policy and restore instructions"). This is the single
reference for how the production PostgreSQL database is placed, connected, protected, backed up,
monitored, moved and recovered. It is written for the owner, for whoever operates the servers,
and for an engineer who has to change any of it later.

| | |
|---|---|
| Status | Built and rehearsed end to end on local disposable clusters; **not yet deployed**. Production was not touched. |
| Branch | `feat/db-separation-20261028` (local commits, not pushed) |
| Tools | [`scripts/database/`](../../scripts/database/README.md) |
| Background | [Database recovery](database-recovery.md) (restore rehearsal design) |
| Owner decisions still open | Database host, off-host backup store, alert receiver, recovery targets, cut-over window (section 19) |
| Secrets in this document | None. Section 9 says where each secret lives and how to rotate it. |

Contents

1. Summary for the owner
2. Today and target
3. Requirement traceability
4. Architecture and deployment diagrams
5. Design decisions
6. Failure modes and how each is handled
7. What changed in the application
8. Tool reference
9. Configuration, files and secrets
10. Roles and privileges
11. Capacity planning
12. Runbook: provisioning
13. Runbook: preflight, rehearsal and cut-over
14. Backups and retention
15. Recovery procedures
16. Monitoring and alerts
17. Routine operations
18. Testing and evidence
19. Owner decisions and go-live checklist
20. Known limits and what was not verified
21. Findings made during this work
22. Frequently asked questions
23. Glossary
24. Appendices

---

## 1. Summary for the owner

**The problem.** Today the production database is a Docker container on the same Hetzner server
that runs the application (`talky-postgres-1`, `127.0.0.1:5432`). The nightly backup is written to
the same disk. If that one server fails, is rebuilt or is lost, the customer data and its backups go
with it. Nobody is alerted when the database or its backups go wrong.

**What now exists.**

* A tested way to move the data to a separate database server, preferably a managed PostgreSQL
  service, without writing anything to today's database and with every row counted on arrival.
* The application connects to that server only over TLS with certificate verification, and refuses
  to start in production if a remote database URL does not require it.
* The application runs as a dedicated database user (`talklee_app`) that can read and write rows but
  cannot create, alter, truncate or drop anything, owns nothing, and is subject to tenant isolation
  (row-level security). The application refuses to start as an over-privileged user when told to.
* Migrations run as a separate owner user (`talklee_owner`) that the running application never has.
* Daily checked backups, an encrypted copy outside the server, a retention policy (30 days, never
  fewer than 7 copies), point-in-time recovery, and monitoring with alerts every five minutes.
* A preflight check that tells you, days before the switch, whether the new server is big enough,
  close enough, correctly secured and compatible.
* Written procedures for the switch, the rollback, every recovery case and routine operations such
  as password rotation.

**How it was proven.** A rehearsal script builds a copy of today's production database shape (the
real schema and every migration, a role that owns all tables like production), then performs the
whole sequence on separate local PostgreSQL servers: preflight, cut-over, migrations, the
application's own database code as the restricted user, a server restart under a running
application, a password rotation, backups with an encrypted off-host copy, a restore drill from that
copy into a staging application, point-in-time recovery and monitoring. It finishes by proving the
original database is byte-for-byte unchanged. Section 18 has the numbers.

**What it does not prove yet.** It ran on one machine with synthetic data. The real provider, the
real network, production data volume and timings, and alert delivery to your real channel are
checked during the rehearsal step of the runbook (section 13), before the switch.

**What you need to decide** (section 19): where the database goes, where off-host backups go, where
alerts go, the recovery targets, and the maintenance window.

---

## 2. Today and target

| Area | Today (2026-10-28) | Target |
|---|---|---|
| Location | Docker container `talky-postgres-1` on the application server, `127.0.0.1:5432`, PostgreSQL 15.17 | Managed PostgreSQL (preferred) or a dedicated database server in the same data centre |
| Network | Loopback on the application server | Private network or an IP allow-list that admits only the application server(s) |
| Transport | Plaintext (loopback) | TLS 1.2+ with certificate and host-name verification (`sslmode=verify-full`) |
| Runtime user | `talkyai`: not a superuser and no RLS bypass, but it **owns every table**, so it can alter or drop them | `talklee_app`: row reads and writes only, owns nothing, capped connections |
| Migration user | The same `talkyai` | `talklee_owner`, used only by `talky-migrate.service` |
| Backup user | `talkyai` through `docker exec` | `talklee_backup` (read-only), or a provider snapshot |
| Backups | Nightly `pg_dump` on the same disk; one admin-only table dumped empty (section 21.1) | Provider PITR or WAL archive + base backups, plus a daily checked logical bundle and an encrypted off-host copy |
| Retention | 30 days of local files | 30 days, never fewer than the newest 7, locally and off-host |
| Point-in-time recovery | None | Provider PITR (7 days or more) or `pitr.py` on a dedicated server |
| Monitoring | Journal only; nobody is alerted | `monitor.py` every 5 minutes plus provider alerts, to a webhook |
| Host change | Requires editing `DATABASE_URL` (already the only setting) | The same: change `DATABASE_URL`, no code change |

---

## 3. Requirement traceability

Every line of deliverable 4, what implements it, how it was proven, and what is left.

| # | Requirement (deliverable 4) | Implementation | Evidence | Left for go-live |
|---|---|---|---|---|
| R1 | Database must not live only on the application server | `cutover.py load` moves a verified bundle into a new database on a different server; refuses the source server, an existing database or any row-count mismatch | Rehearsal: 109 tables loaded with exact counts; legacy database unchanged | Choose the host; run the cut-over |
| R2 | Managed PostgreSQL preferred; dedicated server only if backup, monitoring and recovery are managed | Tools work with a non-superuser provider administrator; dedicated option has `install_db_server.sh`, `pitr.py`, monitoring and off-host copies | Rehearsal used a CREATEROLE/CREATEDB administrator, not a superuser | Owner decision |
| R3 | `DATABASE_URL` so the host can change without code changes | Already the only database setting; production now refuses to start without it | `test_database_policy.py`, production gate | None |
| R4 | Private networking / IP allow-listing | Provider network rules, or `render_config.py`: `pg_hba.conf` with `hostssl` only for the allow-listed addresses plus `ufw` in `install_db_server.sh` | Plaintext and non-allow-listed clients refused in the server-config rehearsal | Configure at the provider |
| R5 | TLS for database connections | `sslmode=verify-full` with the provider CA; the application refuses a remote URL without `verify-full`/`verify-ca` in production | Rehearsal: TLS 1.3 to the new server; wrong CA refused | Install the CA file |
| R6 | Dedicated application user with only the permissions it needs | `roles.py`: `talklee_owner`, `talklee_app`, `talklee_backup`; verification of 16 privilege rules | Rehearsal: CREATE, TRUNCATE, DROP, disabling RLS and cross-tenant writes refused for the runtime user | Run `roles.py apply` during the cut-over |
| R7 | No superuser for normal runtime | Runtime user is NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE and owns nothing; startup posture check | Application refused to boot as the owner when the restricted role was required | Set `DATABASE_REQUIRE_RESTRICTED_ROLE=true` |
| R8 | Automated backups with documented retention | `talklee-db-backup.timer` daily: checked bundle, encrypted off-host copy, retention 30 days / minimum 7 | Rehearsal: backup, encrypted replica, retention and health all passed | Off-host store and passphrase |
| R9 | Point-in-time recovery if supported | Provider PITR; for a dedicated server `pitr.py` (WAL archive + base backups) | Rehearsal: restored to a chosen second over TLS | Enable at the provider |
| R10 | Database/storage health monitoring and capacity alerts | `talklee-db-monitor.timer` every 5 minutes: 9 checks with thresholds, webhook alerts, Prometheus metrics | Rehearsal: every check ok; alert, repeat and recovery messages tested | Alert receiver |
| R11 | Document the recovery procedure | Sections 13 to 15 of this document | This document | None |
| T1 | Take a production backup/snapshot | `recovery.py backup` (read-only snapshot) or provider snapshot | Rehearsal source and external backups | Run against production |
| T2 | Restore it into a temporary/test database | `recovery.py restore` into a new scratch database on a separate server | Rehearsal restore drill | Run against production |
| T3 | Point a staging/test Talklee instance at it | `cutover.py load` into `talky_restore_*`, then the application's own pool as `talklee_app` | Rehearsal: application read tenants, campaigns, leads and calls from the restored copy | Run with production data |
| T4 | Verify tenant, campaign, call and other critical records | Exact per-table counts for every table, Alembic heads, tenant-isolation probe | Rehearsal: exact counts for 109 tables | Run with production data |
| T5 | Document the restore process and expected recovery time | Section 15, with measured times in section 18 | Rehearsal timings | Measure with production data |

---

## 4. Architecture and deployment diagrams

### 4.1 Target architecture

```
                         Internet (HTTPS, SIP)
                                  │
  ┌───────────────────────────────▼────────────────────────────────┐
  │ Application server (Hetzner, 144.76.17.150)                     │
  │                                                                 │
  │  talky-api (:8000)        talky-api-web (:8001)                 │
  │  talky-dialer-worker      talky-voice-worker                    │
  │  talky-reminder-worker    timers (cleanup, trunk status, ...)   │
  │        │  DATABASE_URL = talklee_app, sslmode=verify-full       │
  │        │                                                        │
  │  talky-migrate.service ── MIGRATION_DATABASE_URL = talklee_owner │
  │  talklee-db-backup.timer ─ talklee_backup (read-only)           │
  │  talklee-db-monitor.timer ─ talklee_app (or a pg_monitor role)  │
  │                                                                 │
  │  /var/backups/talklee (daily bundles, 30 days)                  │
  └──────────┬──────────────────────────────────┬──────────────────┘
             │ TLS 1.2+, private network         │ encrypted copies (AES-256)
             │ or allow-listed source IP         │
  ┌──────────▼──────────────────────┐   ┌───────▼─────────────────────────┐
  │ PostgreSQL (managed service,    │   │ Off-host backup store           │
  │ or dedicated server)            │   │ (e.g. Hetzner Storage Box mount │
  │  database talkyai               │   │  at /mnt/talklee-offhost)       │
  │  roles talklee_owner/app/backup │   │  database.dump.gpg + receipts   │
  │  provider PITR / WAL archive    │   │  30 days, minimum 7             │
  │  provider storage/CPU alerts    │   └─────────────────────────────────┘
  └─────────────────────────────────┘
```

### 4.2 Deployment view: who connects with which user

| Process | Runs on | Connects as | Setting | Purpose |
|---|---|---|---|---|
| `talky-api`, `talky-api-web`, workers, timers | Application server | `talklee_app` | `DATABASE_URL` in `/opt/talky/backend/.env` | Normal operation |
| `talky-migrate.service` | Application server | `talklee_owner` | `MIGRATION_DATABASE_URL` in `/etc/talklee/secrets/migration.env` | Schema changes during deploys |
| `talklee-db-backup.service` | Application server | `talklee_backup` | `talklee_backup` service in `pg_service.conf` | Daily logical backup |
| `talklee-db-monitor.service` | Application server | `talklee_app` (or a role with `pg_monitor`) | `talklee_runtime` service | Health and capacity checks |
| `talklee-db-basebackup.service` | Dedicated database server only | `talklee_replication` | `talklee_replication` service | Daily base backups for PITR, then retention |
| Operator tools (`preflight.py`, `cutover.py`, `roles.py`) | Application server | Provider administrator | `talklee_admin`, `talklee_admin_db` services | Preparation, cut-over, rotation |

### 4.3 Data flow of a backup

```
 database ──(read-only snapshot, pg_dump)──▶ staging dir ──verify──▶ /var/backups/talklee/<UTC stamp>/
                                                                        database.dump
                                                                        database.dump.sha256
                                                                        receipt.json (counts, heads)
        /var/backups/talklee/<stamp> ──gpg AES-256──▶ staging on off-host mount
                                     ──decrypt-and-compare (streamed, nothing written)──▶ publish
                                                                        database.dump.gpg
                                                                        replica.json
                                                                        receipt.json, .sha256
        then retention on the off-host root, then retention locally
```

---

## 5. Design decisions

Each decision with the reason and the alternative that was rejected.

### 5.1 Managed PostgreSQL first

A managed service supplies the parts that are hardest to run well: tested point-in-time recovery,
replication, storage alerts, patching and failover. A dedicated server is supported because the
deliverable allows it, but then the WAL archive, base backups, monitoring and off-host copies are
ours to run (all scripted here). Whatever is chosen must offer PostgreSQL 15 or 16, TLS with a
downloadable CA, private networking or source-IP allow-lists, at least 7 days of PITR and an
administrator with CREATEROLE and CREATEDB. Superuser is not needed and was not used.

### 5.2 Three roles with one job each

* `talklee_owner` owns the schema. Migrations need ownership to change tables, so only the migration
  unit gets these credentials.
* `talklee_app` is what the running application uses. It has SELECT, INSERT, UPDATE and DELETE on
  tables, sequence use and function EXECUTE, and nothing else. Because it is not the owner, the
  tables' forced row-level security always applies to it, and it cannot switch RLS off, add columns
  or drop data in bulk even if the application is compromised.
* `talklee_backup` can only read.

Rejected: keeping one role that owns everything (today's `talkyai`). An owner can `ALTER TABLE ...
DISABLE ROW LEVEL SECURITY`, `TRUNCATE` or `DROP`, so a single SQL injection could remove tenant
isolation or erase data.

Default privileges are set so a table created by a future migration (as the owner) is immediately
usable by the runtime and backup roles. The rehearsal proves this with a table created after the
cut-over.

### 5.3 TLS with verification, enforced by the application

`sslmode=require` encrypts but does not check who answers, so a network attacker could impersonate
the database. `verify-full` checks the certificate chain and the host name. The application refuses
to start in production when a remote `DATABASE_URL` or `READ_DATABASE_URL` does not use `verify-full`
or `verify-ca`. Loopback addresses (`127.0.0.1`, `localhost`, `::1`) and Unix sockets are exempt, so
today's configuration keeps working unchanged until the cut-over.

### 5.4 A startup posture check instead of trust

Each pool start logs one `db_runtime_posture` line: role, superuser, bypass_rls, create_role,
create_database, owned_relations and tls. With `DATABASE_REQUIRE_RESTRICTED_ROLE=true` the
application refuses to start when the role is a superuser, bypasses RLS, can create roles or
databases, or owns any relation. This catches a wrong password file or a copied URL before the
first request, not after an incident.

### 5.5 Two kinds of backup

* Physical (provider PITR, or WAL archive + base backups): restore to any second in the window.
  This is the fast path for "undo the last hour".
* Logical bundles (`pg_dump`, daily): portable between providers and versions, checksummed, every
  table counted, copied off-host. This is the path that survives losing the provider account or
  moving providers.

Rejected: relying on provider snapshots alone. They live in the same provider account, so an account
problem or a deleted instance can take them too.

### 5.6 Complete backups under row-level security

The backup user is not a superuser, so RLS would filter what it reads. Two supported answers: a
backup role with BYPASSRLS (when the provider allows granting it), or the application's own bypass
setting `app.bypass_rls` plus a verified platform-admin identity in `app.current_user_id`. The second
is needed because one table (`assistant_action_resolutions`, migration 0062) deliberately ignores the
bypass and admits only platform admins. Before dumping, the tool proves every policy is covered and
refuses otherwise, so a silently filtered backup cannot be produced.

### 5.7 Encrypt everything that leaves the server

The bundles hold every customer's data: phone numbers, transcripts, contact details. The off-host
copy is encrypted with OpenPGP symmetric AES-256 (GnuPG, integrity-protected) using a passphrase file.
gpg was chosen because it is installed on every Ubuntu server and is a standard, audited format that
can be decrypted without our tools. The copy is proven to decrypt to the original before it is
published. Unencrypted copying is only possible with an explicit `--allow-unencrypted-replica`, for a
destination that already encrypts. The passphrase must also be kept outside the server (section 9.3):
an encrypted backup whose key died with the server is no backup.

Rejected: writing our own encryption with a Python library (custom cryptographic formats are a
common source of flaws), and relying on the storage provider's encryption alone (the provider can
read it).

### 5.8 Move data without touching the source

The cut-over reads today's database through a read-only snapshot (`default_transaction_read_only`),
loads into a database that must not exist yet on a server that must be a different cluster, and
counts every table on both sides. The rollback is therefore simply pointing the application back at
the old database, which is exactly as it was when the window started.

### 5.9 Check readiness before the window, not during it

`preflight.py` checks the target and the source days ahead: version, TLS, administrator rights,
connection capacity against the application's pool budget, extensions, collation, time zone,
latency, client tool versions and row-level-security drift. Anything that would stop the cut-over
halfway is a fail, found while there is still time.

### 5.10 Cap the runtime role's connections

If the application leaked connections it could use every slot and lock out the backup, the monitor
and the operator exactly when they are needed. The runtime role gets a `CONNECTION LIMIT` below the
server's usable maximum (preflight recommends the number), leaving room for everyone else.

---

## 6. Failure modes and how each is handled

| Event | Impact without this work | Protection now | Recovery | Data loss (target) | Time to recover (target) |
|---|---|---|---|---|---|
| Application server lost or rebuilt | All data and backups lost | Data and backups are elsewhere | Rebuild the server with the installer, point `DATABASE_URL` at the database | None | Server rebuild time |
| Database server/instance lost | Total loss | Provider PITR and snapshots; daily encrypted off-host bundles | 15.2 / 15.3 | ≤ 5 min (PITR), ≤ 24 h (bundles alone) | ≤ 1 h (proposed) |
| Bad deploy or operator mistake corrupts data | Restore from last night at best | PITR to the second before the mistake | 15.4 | Seconds to minutes | ≤ 1 h |
| One tenant's rows deleted by mistake | Hard to recover without affecting others | Restore a copy to a scratch database, copy the tenant's rows back | 15.5 | Since the restore point | Hours (careful work) |
| Both provider and application server lost | Total loss | Encrypted off-host bundles plus escrowed passphrase | 15.6 | ≤ 24 h | Provision time + restore time |
| Provider maintenance restarts the database | Errors until manual restart | The application pool reconnects by itself | Automatic; rehearsal measured 0.5 s after the server was back | None | Seconds |
| Database password leaked | Attacker can connect | TLS + allow-list limit who can connect; rotation without grants changes | 17.1 | None | Minutes |
| Connections exhausted | Backups/monitoring fail too | Runtime role connection cap; monitor alerts at 80 % / 95 % | 16.3 | None | Minutes |
| Disk/plan fills up | Writes fail, outage | Monitor alerts at 80 % / 90 %; provider alerts | 16.3 | None | Minutes to resize |
| Backup silently incomplete | Discovered during a disaster | RLS coverage proof, size plausibility check, per-table counts, monthly restore drill | 14.6 | None | — |
| Off-host copy tampered with or corrupted | Restore fails | Checksums in `replica.json`; gpg integrity check refuses any change | Use the previous copy | One day | — |
| TLS certificate expires | Application cannot connect | Monitor checks TLS on every run; certificate dates in the runbook | 17.3 | None | Minutes |

---

## 7. What changed in the application

These reach production only when this branch is deployed. Each one is inert with today's loopback
configuration unless stated.

### 7.1 `backend/app/core/database_policy.py` (new)

* `database_url_problems()` and `enforce_database_url_policy()`: in production (`ENVIRONMENT=production`)
  `DATABASE_URL` must be set, and any `DATABASE_URL`/`READ_DATABASE_URL` whose host is not loopback must
  carry `sslmode=verify-full` or `verify-ca`. Outside production the same problems only log a warning.
  The URL is never printed.
* `enforce_runtime_posture(pool, pool_name=...)`: runs one read-only query on a new pool and logs
  `db_runtime_posture`. With `DATABASE_REQUIRE_RESTRICTED_ROLE=true` it raises `DatabasePolicyError` for
  an unsafe role. If the probe itself fails it warns, unless the restricted role is required.

### 7.2 `backend/app/core/db.py`

`init_db_pool()` checks the URL policy before creating pools, then the runtime posture of the primary
pool and, when configured, the read pool. On a policy error the pools are closed and startup fails.

### 7.3 `backend/app/core/prod_gate.py`

A `database_policy` rule joins the production gate, so the same URL problems block the gate report.

### 7.4 `backend/deploy/infra/wait-for-deps`

When `DATABASE_URL` points at a remote host, the dependency wait checks that host's TCP port instead
of the local `talky-postgres-1` container's health. With a loopback URL it behaves exactly as before.
Redis is still checked through Docker.

### 7.5 `backend/systemd/talky-migrate.service`

Adds `EnvironmentFile=-/etc/talklee/secrets/migration.env` after `.env`. When that file exists and sets
`MIGRATION_DATABASE_URL`, Alembic connects as the owner role while every other service keeps the
restricted `DATABASE_URL`. The leading `-` makes the file optional, so nothing changes until it exists.

### 7.6 `backend/deploy/db-backup.sh` (today's nightly backup)

Accepts `TALKY_BACKUP_PLATFORM_ADMIN_ID` (validated as a UUID). When set, the dump session also carries
`app.current_user_id=<that id>`, so the platform-admin-only table is included (section 21.1). When it is
unset the script prints a warning on every run.

### 7.7 `backend/app/domain/services/provider_cost_ledger.py`

The cost ledger now writes its batch with one `INSERT` (asyncpg `executemany`) instead of `COPY`.
PostgreSQL refuses `COPY FROM` whenever row-level security applies to the connecting role, and it
applies to `talklee_app` on every tenant table (section 21.2).

---

## 8. Tool reference

All tools are in `scripts/database/`, need only Python 3.11+ and the PostgreSQL client programs
(`psql`, `pg_dump`, `pg_restore`; `pg_basebackup` for PITR; `gpg` for encrypted copies), and connect
only through named services in a protected `pg_service.conf` with passwords in a protected `PGPASSFILE`.
No tool accepts a password on the command line or writes one into a receipt. Every tool writes a JSON
receipt and refuses to overwrite an existing one, so evidence cannot be replaced by mistake.
`--allow-local-plaintext` is accepted only for a loopback address and exists for today's source
database and local rehearsals.

### 8.1 `preflight.py`: is the new server ready?

Read-only on both servers. Exit 0 when nothing fails (warnings allowed), 1 when anything fails.

```bash
python3 scripts/database/preflight.py --service-file /etc/talklee/secrets/pg_service.conf \
  --target-service talklee_admin --source-service talklee_source --allow-local-plaintext \
  --database talkyai --capacity-bytes <plan storage in bytes> \
  --receipt /var/backups/talklee-evidence/preflight.json
```

| Option | Default | Meaning |
|---|---|---|
| `--target-service` | required | New server's administrator service (`dbname=postgres`) |
| `--source-service` | none | Today's database, read-only |
| `--database` | `talkyai` | Name the cut-over will create; must not exist yet |
| `--owner`, `--runtime`, `--backup` | `talklee_owner`, `talklee_app`, `talklee_backup` | Managed role names |
| `--pool-budget` / `--systemd-dir` | read from `backend/systemd/talky-*.service` | Sum of the services' `PG_POOL_MAX_SIZE` |
| `--headroom` | 20 | Connections kept for timers, backup, monitor, migrations and operators |
| `--require-extension` | the source's extensions | Extensions the schema needs |
| `--latency-samples` | 50 | `SELECT 1` round trips measured on one connection |
| `--max-p95-ms` | 2.0 | Latency fail threshold (warning above half of it) |
| `--capacity-bytes` | none | Plan storage, compared with the source size |

| Check | Fails when | Warns when |
|---|---|---|
| `target_version` | Major not 15/16/17, or older than the source | — |
| `target_tls` | The administrator connection is not encrypted | — |
| `administrator` | Missing CREATEROLE or CREATEDB | It is a superuser (works, but not the rehearsed shape) |
| `connection_capacity` | Pool budget + headroom exceeds usable connections | Less than 20 % spare |
| `extensions` | A required extension is missing, or untrusted for a non-superuser administrator | — |
| `encoding_and_collation` | New databases would not be UTF8 | Collation differs from the source |
| `time_zone` | — | Server time zone differs from the source |
| `target_database_absent` | The database already exists | — |
| `existing_roles` | — | Managed roles already exist (they will be verified, not altered) |
| `round_trip_latency` | p95 above `--max-p95-ms` | p95 above half of it |
| `client_tools` | Local `pg_dump` older than the source | Local client older than the target |
| `storage_capacity` | Source size ≥ 50 % of the plan | ≥ 30 % |
| `row_level_security` | Any table has RLS enabled but not forced | — |
| `tenant_tables_without_rls` | — | Tables with `tenant_id` and no RLS at all |

The receipt also carries `recommendations.runtime_connection_limit`: usable connections minus half the
headroom (at least 5). Pass it to `roles.py apply --runtime-connection-limit`.

### 8.2 `recovery.py`: backups, copies, retention, restore

| Command | What it does |
|---|---|
| `audit --service S --receipt R [--app-address IP]` | Read-only check of the runtime connection: TLS, role attributes, endpoint separation |
| `backup --service S --destination DIR [--rls-bypass-guc] [--rls-setting app.NAME=VALUE] [--previous DIR] [--minimum-bytes N] [--minimum-ratio F]` | Snapshot-consistent `pg_dump` from a read-only transaction; refuses a session RLS would filter; checks size plausibility against the previous bundle; writes `database.dump`, `.sha256` and `receipt.json` (per-table counts, Alembic heads) to a staging directory and publishes it by rename |
| `replicate --bundle DIR --destination-root DIR (--passphrase-file F \| --allow-unencrypted) [--gpg PATH]` | Copies a verified bundle off-host; with a passphrase only the ciphertext leaves, proven to decrypt to the original before publishing |
| `decrypt-replica --replica DIR --destination DIR --passphrase-file F [--gpg PATH]` | Turns an encrypted replica back into an ordinary verified bundle |
| `prune --backup-root DIR [--keep-days 30] [--keep-minimum 7] [--dry-run]` | Retention; deletes nothing unless the newest bundle verifies; never touches unknown directories |
| `health --backup-root DIR [--maximum-age-hours 26]` | Newest bundle (by snapshot time) verifies and is fresh; an unreadable receipt is a failure, never skipped |
| `restore --service S --bundle DIR --database talky_restore_X --runtime-role R --expected-head H --receipt R` | Restores only into a new `talky_restore_*` database on a server that is not the source; exact counts, heads and a tenant-isolation probe |

Refusals worth knowing: an existing destination, a bundle with symlinks, a receipt or checksum
mismatch, a filtered RLS session, a dump smaller than half the previous one, a restore target named
anything but `talky_restore_*`, restoring onto the source cluster, a replica that already exists.

### 8.3 `cutover.py`: load into the new server

```bash
python3 scripts/database/cutover.py --service-file /etc/talklee/secrets/pg_service.conf \
  load --service talklee_admin --bundle /var/backups/talklee/cutover --database talkyai \
  --expected-head <alembic head> --rls-setting app.current_user_id=<platform admin id> \
  --receipt /var/backups/talklee-evidence/cutover.json
```

Creates the three roles if missing, `CREATE DATABASE <name> OWNER talklee_owner`, restores with
`pg_restore --role talklee_owner`, counts every table on the new database and compares with the
bundle, applies grants and default privileges, then runs the full role verification. It refuses the
source cluster or endpoint, an existing database, wrong Alembic heads, a bundle without per-table
counts, and a non-maintenance service. The receipt contains connection templates without passwords.

### 8.4 `roles.py`: provision, verify, rotate

| Command | What it does |
|---|---|
| `generate-password --output FILE` | Writes a 40-character random password to a new file (0600). Never prints it. |
| `plan --database D [--from-owner OLD]` | Prints the SQL `apply` would run (no passwords) |
| `apply --service S --database D --receipt R [--owner-password-file F] [--runtime-password-file F] [--backup-password-file F] [--runtime-connection-limit N] [--from-owner OLD] [--backup-bypassrls]` | One transaction: create missing roles, set passwords as SCRAM verifiers, optional connection cap, ownership to the owner role, grants and default privileges; then verify |
| `verify --service S --database D --receipt R` | 16 read-only privilege rules; exit 1 if any fails |
| `rotate-password --service S --database D --role R --password-file F --receipt R` | Changes one managed role's password and nothing else, then proves the new password logs in |

Passwords never travel in clear: the tool computes the SCRAM-SHA-256 verifier locally (like psql's
`\password`) and sends only that. On PostgreSQL 16 a CREATEROLE administrator receives only ADMIN
OPTION on roles it creates, so `apply` grants it a usable membership of the owner role (needed for
`SET ROLE` and ownership transfer). The runtime role is never a member of the owner.

### 8.5 `scheduled_backup.py`: the daily job

Takes a lock, runs `recovery.py backup` against the previous bundle, copies the new bundle off-host
(encrypted by default), applies retention to the off-host root, then to the local root. Retention
runs only after a successful backup and copy, so a failing schedule never deletes the last good
archive. Options: `--service-file`, `--service`, `--backup-root`, `--minimum-bytes`, `--minimum-ratio`,
`--rls-bypass-guc`, `--rls-setting`, `--replica-root`, `--replica-passphrase-file`,
`--allow-unencrypted-replica`, `--gpg`, `--keep-days`, `--keep-minimum`. The encryption choice is
checked before the dump starts.

### 8.6 `monitor.py`: health and capacity

`monitor.py --service talklee_runtime check --capacity-bytes N [options]`. Checks and default thresholds
are in section 16. Exit 0 ok, 1 warning, 2 critical. Writes a Prometheus textfile when asked, posts to
`TALKLEE_ALERT_WEBHOOK_URL` (Slack-compatible) and keeps alert state in `--state-file`.

### 8.7 `pitr.py`: point-in-time recovery for a dedicated server

| Command | What it does |
|---|---|
| `archive-settings --archive-dir DIR [--platform linux]` | Prints the `postgresql.conf` lines for WAL archiving (copy-if-absent, fsync) |
| `base-backup --service talklee_replication --destination DIR` | `pg_basebackup` over TLS replication, verified and published |
| `restore --base-backup DIR --archive-dir DIR --target-data-dir NEW (--target-time ISO \| --target-latest) [--port N] [--start]` | Restores into a new data directory and replays WAL to the target; waits for promotion |
| `archive-health --archive-dir DIR --max-age-minutes N` | Fails when the archive is empty or stale |
| `prune --base-root DIR --archive-dir DIR [--keep 7] [--dry-run]` | Keeps the newest base backups and the WAL needed to replay from the oldest of them; deletes nothing unless the newest base backup verifies; compares segments without their timeline like `pg_archivecleanup`; keeps history and `.backup` files and anything it does not recognise |

### 8.8 `db_server/render_config.py` and `db_server/install_db_server.sh`

For the dedicated-server option. `render_config.py` writes `talklee.conf` and `pg_hba.conf`: TLS only
(`hostnossl` rejected), SCRAM authentication, listen only on the private address, admit only the
allow-listed CIDRs, memory-based settings, optional replication and administrator entries. It refuses
public listen addresses or allow-lists unless explicitly acknowledged. `install_db_server.sh` (Ubuntu
24.04, run as root) installs PostgreSQL from the PGDG repository, installs the certificate, applies the
rendered configuration and a `ufw` allow-list. It never handles database passwords.

Environment for the installer: `LISTEN_ADDRESS`, `APP_ALLOW_CIDRS`, `CERT_SRC`, `KEY_SRC` (required);
`CA_SRC`, `DB_NAME` (`talkyai`), `PG_MAJOR` (16), `MEMORY_MB`, `ALLOW_REPLICATION=1`, `ADMIN_ROLE`.

### 8.9 systemd units

| Unit | Schedule | Runs as | What |
|---|---|---|---|
| `talklee-db-backup.service` / `.timer` | Daily 02:00 UTC (±5 min) | `talklee-backup` | `scheduled_backup.py`: bundle, encrypted off-host copy, retention |
| `talklee-db-backup-health.service` / `.timer` | Hourly | `talklee-backup` | `recovery.py health` on the local root (26 h) |
| `talklee-db-monitor.service` / `.timer` | Every 5 min (from 2 min after boot) | `talklee-monitor` | `monitor.py check` with alerts and metrics |
| `talklee-db-basebackup.service` / `.timer` | Daily 03:00 UTC, dedicated server only | `talklee-backup` | `pitr.py base-backup`, then `archive-health`, then `prune --keep 7` |

All four run sandboxed (`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `NoNewPrivileges`, write
access only to their own directories). A guard test parses each unit's command line with the tool's
own argument parser, so a renamed flag fails CI instead of failing at 02:00.

---

## 9. Configuration, files and secrets

### 9.1 Environment variables

| Variable | Where | Value after cut-over | Purpose |
|---|---|---|---|
| `DATABASE_URL` | `/opt/talky/backend/.env` | `postgresql://talklee_app:<url-encoded password>@<host>:5432/talkyai?sslmode=verify-full&sslrootcert=/etc/talklee/secrets/database-ca.pem` | Runtime connection for every service |
| `READ_DATABASE_URL` | `.env` (optional) | Same format, pointing at a read replica | Read pool; same TLS rule |
| `MIGRATION_DATABASE_URL` | `/etc/talklee/secrets/migration.env` only | Same format with `talklee_owner` | Alembic during deploys |
| `DATABASE_REQUIRE_RESTRICTED_ROLE` | `.env` | `true` | Refuse to start as an unsafe role |
| `ENVIRONMENT` | `.env` | `production` | Turns policy warnings into refusals |
| `PG_POOL_MIN_SIZE`, `PG_POOL_MAX_SIZE` | systemd units | API 2/15, web 2/12, dialer 1/10, voice 1/10, reminder 1/3 | Connection budget (section 11) |
| `PG_READ_POOL_MIN_SIZE`, `PG_READ_POOL_MAX_SIZE` | `.env` | 2 / 15 by default | Read pool size |
| `PG_POOL_ACQUIRE_TIMEOUT` | `.env` | 10 (seconds) | Wait for a pooled connection |
| `TALKY_BACKUP_PLATFORM_ADMIN_ID` | drop-in for today's `talky-db-backup.service` (`systemctl edit`) | UUID of an active, verified platform admin | Complete legacy nightly backups |
| `TALKY_BACKUP_DIR`, `TALKY_BACKUP_KEEP_DAYS` | same | `/var/backups/talky`, 30 | Legacy nightly backup location and retention |
| `TALKLEE_BACKUP_RLS_OPTIONS` | `/etc/talklee/secrets/backup/backup.env` | empty for a BYPASSRLS backup role, else `--rls-bypass-guc --rls-setting app.current_user_id=<id>` | Complete backups under RLS |
| `TALKLEE_ALERT_WEBHOOK_URL` | `/etc/talklee/secrets/monitor/monitor.env` | Slack-compatible incoming webhook | Alerts |
| `PGSERVICEFILE`, `PGPASSFILE` | units, operator shell | the unit user's own `pg_service.conf` and `pgpass` (section 9.2) | Tool connections |

Passwords in a URL must be URL-encoded (for example `/` becomes `%2F`). Passwords generated by
`roles.py generate-password` use only letters, digits, `-` and `_`, which need no encoding.

### 9.2 Files under `/etc/talklee/secrets` (application server)

PostgreSQL's client library and the tools refuse a password or service file that any other user can
read. One file therefore cannot serve two system users, so each timer user has its own directory with
only what it needs. The monitor user cannot read the backup passphrase or the backup role's password.

| Path | Owner, mode | Contents | Used by |
|---|---|---|---|
| `/etc/talklee/secrets/` | root, 0711 | Directory: others may pass through to their own subdirectory but not list it | — |
| `database-ca.pem` | root, 0644 | The provider's (or our) CA certificate. Not secret. | Application, tools |
| `pg_service.conf` | root, 0600 | Services `talklee_admin`, `talklee_admin_db`, `talklee_source` (+ others for manual runs). No passwords. | Operator tools run as root |
| `pgpass` | root, 0600 | `host:port:database:user:password` lines for the administrator and source | Operator tools run as root |
| `talklee_owner.password`, `talklee_app.password`, `talklee_backup.password` | root, 0600 | One generated password each | `roles.py apply`, `rotate-password` |
| `migration.env` | root, 0600 | `MIGRATION_DATABASE_URL=...` | `talky-migrate.service` |
| `backup/` | `talklee-backup`, 0700 | Directory | — |
| `backup/pg_service.conf` | `talklee-backup`, 0600 | Service `talklee_backup` (and `talklee_replication` on a dedicated server) | Backup and base-backup units |
| `backup/pgpass` | `talklee-backup`, 0600 | The backup role's (and replication role's) password line | Same |
| `backup/backup.env` | `talklee-backup`, 0600 | `TALKLEE_BACKUP_RLS_OPTIONS=...` | `talklee-db-backup.service` |
| `backup/replica.passphrase` | `talklee-backup`, 0600 | Off-host encryption passphrase (at least 24 characters) | `talklee-db-backup.service`, decrypting replicas |
| `monitor/` | `talklee-monitor`, 0700 | Directory | — |
| `monitor/pg_service.conf` | `talklee-monitor`, 0600 | Service `talklee_runtime` | `talklee-db-monitor.service` |
| `monitor/pgpass` | `talklee-monitor`, 0600 | The monitoring role's password line | Same |
| `monitor/monitor.env` | `talklee-monitor`, 0600 | `TALKLEE_ALERT_WEBHOOK_URL=...` | Same |

A guard test (`test_systemd_units.py`) fails if a unit reads a secret outside its own user's directory.

### 9.3 Where secrets are kept and who may see them

* **Primary copy:** the files above on the application server, readable only by root or the unit's
  user. The `.env` file holds the runtime URL.
* **Escrow copy (required):** the three role passwords, the provider administrator credentials and the
  **backup passphrase** in the owner's password manager, in a vault entry named for the environment.
  Without the escrowed passphrase the off-host copies cannot be decrypted after losing the server.
* **Never:** in git, in this or any handover document, in chat, in tickets, in receipts or logs. The
  tools never print secrets; receipts contain role names and checksums only.
* **Who may rotate:** the owner or an operator the owner authorises, with root on the application
  server and administrator access at the provider. Procedures are in section 17.

### 9.4 Named services (`pg_service.conf`)

The template is [`scripts/database/pg_service.conf.example`](../../scripts/database/pg_service.conf.example).

| Service | Database | User | Used for |
|---|---|---|---|
| `talklee_admin` | `postgres` | provider administrator | Preflight, cut-over, role provisioning |
| `talklee_admin_db` | `talkyai` | provider administrator | `roles.py apply/verify/rotate-password` |
| `talklee_runtime` | `talkyai` | `talklee_app` | Monitor |
| `talklee_backup` | `talkyai` | `talklee_backup` | Daily backup |
| `talklee_source` | `talkyai` on `127.0.0.1` | `talkyai` | Today's database, read-only source (plaintext loopback) |
| `talklee_replication` | `postgres` | `talklee_replication` | Base backups (dedicated server only) |
| `talklee_restore` | `postgres` on a scratch server | restore operator | Monthly restore drill |

Use direct PostgreSQL endpoints, not a transaction-mode connection pooler: the backup's snapshot and
the application's session settings need a real session.

---

## 10. Roles and privileges

### 10.1 Privilege matrix (target)

| Capability | `talklee_owner` | `talklee_app` | `talklee_backup` | Provider admin |
|---|---|---|---|---|
| Log in | yes | yes | yes | yes |
| Superuser / BYPASSRLS | no / no | no / no | no / only if `--backup-bypassrls` | no |
| Create roles or databases | no | no | no | yes |
| Own schema and objects | yes | no | no | no |
| CREATE in schema `public` | yes (owner) | no | no | via owner membership |
| SELECT/INSERT/UPDATE/DELETE | yes | yes, every table | SELECT only | via owner membership |
| TRUNCATE, REFERENCES, TRIGGER | yes | **no** | no | — |
| Sequences | yes | USAGE, SELECT, UPDATE | SELECT | — |
| Functions | yes | EXECUTE | — | — |
| ALTER/DROP tables, disable RLS | yes | **no** | no | — |
| Subject to forced RLS | yes (FORCE) | yes | unless BYPASSRLS | — |
| Connection limit | none | set at cut-over (preflight recommends) | none | provider default |

`PUBLIC` loses CONNECT on the database and CREATE on schema `public`.

### 10.2 What `roles.py verify` checks

`owner_role_restricted`, `runtime_role_restricted`, `backup_role_restricted`,
`backup_bypass_matches_request`, `database_and_schema_owned_by_owner`, `every_object_owned_by_owner`,
`runtime_and_backup_own_nothing`, `runtime_has_row_dml_everywhere`,
`runtime_has_no_truncate_references_trigger`, `runtime_can_use_every_sequence`,
`runtime_cannot_create_objects`, `public_cannot_create_or_connect`, `runtime_not_member_of_owner`,
`backup_can_read_every_table`, `default_privileges_cover_future_migrations`,
`tenant_tables_force_rls`. The receipt also records each role's connection limit.

### 10.3 Row-level security and the runtime role

Ninety of the 109 tables (at migration `0073`) have row-level security, and every one of them has
it forced. Forced RLS applies even to the table owner, so today's owning role and tomorrow's
restricted role see exactly the same rows. That was checked on the real schema, and the preflight
re-checks it on the real production database (`row_level_security`), because production also holds
objects created by standalone SQL files outside the migration history. A table with RLS enabled but
not forced would change behaviour at the cut-over: today's owner skips its policies, the runtime
role would not. The application code must still always add an explicit `tenant_id` predicate; RLS is
the second line, not the first.

Runtime SQL was searched for statements that need ownership (DDL, TRUNCATE, VACUUM/ANALYZE, REFRESH
MATERIALIZED VIEW, LOCK TABLE, COPY FROM). Only advisory locks (any role may take them) and one
`COPY FROM` (the cost ledger, now an INSERT, section 21.2) were found. The three views are ordinary
views owned by the owner, there are no SECURITY DEFINER functions, and the 71 triggers run with the
caller's rights, which the runtime role has.

---

## 11. Capacity planning

### 11.1 Connections

| Service | `PG_POOL_MAX_SIZE` |
|---|---|
| `talky-api` (calls) | 15 |
| `talky-api-web` (dashboard API) | 12 |
| `talky-dialer-worker` | 10 |
| `talky-voice-worker` | 10 |
| `talky-reminder-worker` | 3 |
| **Long-running services** | **50** |
| Headroom: timers (cleanup, trunk status, health watch, reconcile), backup, monitor, migrations, operator sessions | 20 |
| **Required** | **70** |

Usable connections are `max_connections - superuser_reserved_connections - reserved_connections`.
Small managed plans often allow 22 to 25 in total, which is **not enough**; choose a plan with at least
100 (preflight fails otherwise). Then cap the runtime role at the preflight's recommendation so it
can never take the last slots.

### 11.2 Storage

Plan storage should be at least three times the current database size (data, indexes rebuilt during
a restore, WAL, growth). The preflight warns at 30 % and fails at 50 % of the plan. The monitor warns at
80 % and goes critical at 90 % of `--capacity-bytes`, which must be set to the real plan size.

### 11.3 Latency

A call turn makes several database round trips (state, transcript, events). In the same data centre
over a private network, round trips are typically well under 1 ms; across regions they are 10 to 30 ms
each and become audible. Preflight measures the real p50/p95 from the application server and fails
above 2 ms p95. Choose a provider region in the same data centre as the application server
(Falkenstein for the current server) or run a dedicated server on the same private network.

### 11.4 CPU and memory

The current container runs on a shared host with the application. Size the first plan at no less
than 2 vCPU and 4 GB RAM, then let the monitor and provider graphs decide. `render_config.py` derives
`shared_buffers` and `effective_cache_size` from `--memory-mb` for a dedicated server.

---

## 12. Runbook: provisioning

### 12.1 Managed PostgreSQL checklist

1. Create the instance: PostgreSQL 16 (15 is acceptable), same region/data centre as the application
   server, plan with ≥ 100 connections and storage ≥ 3× today's database.
2. Networking: private network attachment with the application server, or trusted sources limited to
   the application server's address. No public access from anywhere else.
3. TLS: require TLS; download the CA certificate to `/etc/talklee/secrets/database-ca.pem`.
4. Backups: automated daily backups on, PITR retention ≥ 7 days (longer if the plan allows).
5. Alerts at the provider: storage ≥ 80 %, CPU ≥ 80 % sustained, connections ≥ 80 %, instance down;
   send them to the same receiver as the monitor.
6. Administrator: note the administrator user (it must have CREATEROLE and CREATEDB). Store its
   password in the password manager and in `pgpass`.
7. Extensions: confirm `pgcrypto`, `uuid-ossp` and `pg_trgm` are allowed (preflight checks this).
8. Record the provider, region, plan, host name and console URL (never credentials) in
   [infrastructure-handover.example.json](infrastructure-handover.example.json).

### 12.2 Dedicated server

1. Order a server on the same private network (vSwitch) as the application server.
2. Issue a server certificate for its private DNS name (from your CA), copy the certificate and key to
   the new server.
3. Run as root:
   ```bash
   LISTEN_ADDRESS=10.0.0.5 APP_ALLOW_CIDRS=10.0.0.2/32 CERT_SRC=/root/server.crt KEY_SRC=/root/server.key \
   CA_SRC=/root/ca.crt ALLOW_REPLICATION=1 ADMIN_ROLE=talklee_dbadmin \
   bash scripts/database/db_server/install_db_server.sh
   ```
4. Create the administrator role (CREATEROLE CREATEDB, not superuser) and the `talklee_replication`
   role (REPLICATION) with passwords from `roles.py generate-password`.
5. Configure WAL archiving with `pitr.py archive-settings --archive-dir /var/backups/talklee-pitr/wal`
   and make that directory a mount of storage outside the server, or copy it off-host.
6. Install and enable `talklee-db-basebackup.timer` and `talklee-db-monitor.timer` on the right hosts.
7. Run the server-config rehearsal locally if anything in `render_config.py` changes:
   `python scripts/database/tests/local_db_server_rehearsal.py`.

### 12.3 Application-server preparation (any day before)

1. Install the PostgreSQL client matching the target major (`postgresql-client-16`) and `gnupg`.
2. Create the system users and the secrets directories:
   ```bash
   useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin talklee-backup
   useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin talklee-monitor
   install -d -o root -g root -m 0711 /etc/talklee/secrets
   install -d -o talklee-backup -g talklee-backup -m 0700 /etc/talklee/secrets/backup
   install -d -o talklee-monitor -g talklee-monitor -m 0700 /etc/talklee/secrets/monitor
   install -d -o talklee-backup -g talklee-backup -m 0700 /var/backups/talklee
   install -d -o root -g root -m 0700 /var/backups/talklee-evidence
   ```
3. Write the files of section 9.2 (each file owned by the user of its directory, mode 0600) from the
   template `scripts/database/pg_service.conf.example`.
4. Generate the role passwords:
   ```bash
   for role in talklee_owner talklee_app talklee_backup; do
     python3 scripts/database/roles.py generate-password --output /etc/talklee/secrets/$role.password
   done
   ```
5. Create the backup passphrase, store it in the password manager immediately, and keep it at
   `/etc/talklee/secrets/backup/replica.passphrase` (owner `talklee-backup`, 0600):
   `sudo -u talklee-backup sh -c 'umask 077; head -c 48 /dev/urandom | base64 > /etc/talklee/secrets/backup/replica.passphrase'`.
6. Mount the off-host store at `/mnt/talklee-offhost` (for example a Hetzner Storage Box over SSHFS or
   CIFS) with a `backups` directory writable by `talklee-backup`.
7. Choose the platform-admin user id for complete backups: an active, verified `platform_admin` in
   `user_profiles`. Put it in `backup/backup.env` as part of `TALKLEE_BACKUP_RLS_OPTIONS`.
8. Install the units from `scripts/database/systemd/` into `/etc/systemd/system/` and run
   `systemctl daemon-reload`. Do not enable the timers yet (that is step 9 of the cut-over).

---

## 13. Runbook: preflight, rehearsal and cut-over

Nothing in this section writes to today's database. The source is read through a read-only snapshot;
the target database must not exist; the tools check the target is a different cluster.

### 13.1 Preflight (days before)

```bash
python3 scripts/database/preflight.py --service-file /etc/talklee/secrets/pg_service.conf \
  --target-service talklee_admin --source-service talklee_source --allow-local-plaintext \
  --database talkyai --capacity-bytes <plan bytes> --receipt /var/backups/talklee-evidence/preflight.json
```

Every check must be `ok`, or a reviewed `warning`. Fix any `fail` before going further. Note the
recommended runtime connection limit.

### 13.2 Rehearsal (the day before, no downtime)

1. Take a source bundle while production runs (read-only):
   ```bash
   python3 scripts/database/recovery.py --service-file /etc/talklee/secrets/pg_service.conf \
     --allow-local-plaintext backup --service talklee_source \
     --rls-bypass-guc --rls-setting app.current_user_id=<platform admin id> \
     --destination /var/backups/talklee/cutover-rehearsal
   ```
2. Load it into a throwaway database on the new server and time it:
   ```bash
   time python3 scripts/database/cutover.py --service-file /etc/talklee/secrets/pg_service.conf \
     load --service talklee_admin --bundle /var/backups/talklee/cutover-rehearsal \
     --database talky_restore_rehearsal --expected-head <head> \
     --rls-setting app.current_user_id=<platform admin id> \
     --receipt /var/backups/talklee-evidence/cutover-rehearsal.json
   ```
3. Optionally point a staging instance at `talky_restore_rehearsal` and click through.
4. Drop `talky_restore_rehearsal` at the provider. The measured backup + load time, plus 20 minutes
   for stopping, verification and starting, is the maintenance window.

### 13.3 Cut-over window

| Step | Action | Go/no-go |
|---|---|---|
| 1 | Announce the window. Pause campaigns, stop new calls, wait until `/api/v1/calls/live` is empty. | No live calls |
| 2 | Stop `talky-dialer-worker`, `talky-voice-worker`, `talky-reminder-worker`, `talky-api-web`, `talky-api` and the timers that write (cleanup, trunk status, reconcile). | All stopped |
| 3 | Final source bundle: section 13.2 step 1 with `--destination /var/backups/talklee/cutover`. | `"status": "archive_verified"` |
| 4 | Load: section 13.2 step 2 with `--database talkyai` and `--receipt .../cutover.json`. | `"status": "loaded_and_verified"` |
| 5 | Passwords, cap and verification: see below. | `"status": "passed"` |
| 6 | Back up `/opt/talky/backend/.env`, then set `DATABASE_URL` (runtime template from the cut-over receipt with the `talklee_app` password) and `DATABASE_REQUIRE_RESTRICTED_ROLE=true`. Write `MIGRATION_DATABASE_URL` (owner template) only into `/etc/talklee/secrets/migration.env`. | Files saved |
| 7 | Start the services. | All active |
| 8 | Verify (section 13.4). | All pass |
| 9 | Enable `talklee-db-backup.timer`, `talklee-db-backup-health.timer`, `talklee-db-monitor.timer`; disable the old `talky-db-backup.timer`. Run the backup service once by hand. | First bundle and replica verified |
| 10 | Resume campaigns, announce the end of the window. | — |

Step 5:

```bash
python3 scripts/database/roles.py --service-file /etc/talklee/secrets/pg_service.conf apply \
  --service talklee_admin_db --database talkyai \
  --owner-password-file /etc/talklee/secrets/talklee_owner.password \
  --runtime-password-file /etc/talklee/secrets/talklee_app.password \
  --backup-password-file /etc/talklee/secrets/talklee_backup.password \
  --runtime-connection-limit <preflight recommendation> \
  --receipt /var/backups/talklee-evidence/roles.json
```

### 13.4 Verification after the switch

* `journalctl -u talky-api -b | grep db_runtime_posture` shows `role=talklee_app superuser=False
  bypass_rls=False ... owned_relations=0 tls=True`.
* `curl -fsS http://127.0.0.1:8000/health` and `/api/v1/healthz/deep` return 200.
* Log in to the dashboard, open campaigns, calls and billing; place one controlled test call to an
  internal number and confirm the call, transcript and recording appear.
* `python3 scripts/database/monitor.py --service talklee_runtime check --capacity-bytes <plan bytes>`
  returns status ok.
* `python3 scripts/database/roles.py ... verify --service talklee_admin_db --database talkyai --receipt ...`
  passes.

### 13.5 Rollback

Possible until new writes matter (decide go/no-go during step 8):

1. Stop the services.
2. Restore the backed-up `.env` and remove `/etc/talklee/secrets/migration.env`.
3. Start the services. They use the old container, which was never written, so it is exactly as at
   step 2.

Rows written to the new database after step 7 are not on the old one. If rollback happens later, export
them (by `created_at` since the cut-over) and reconcile by hand before switching back. Keep the old
container stopped but intact for at least 14 days, then take one final archived dump and remove it.

---

## 14. Backups and retention

### 14.1 Layers

| Layer | What | Where | Retention | Restores to |
|---|---|---|---|---|
| Provider PITR (managed) | Continuous WAL + snapshots | Provider | ≥ 7 days (plan dependent) | Any second in the window |
| WAL archive + base backups (dedicated) | `pitr.py`, daily 03:00 UTC | Off-host mount | 7 daily base backups; WAL back to the start of the oldest kept one (`pitr.py prune`) | Any second in the last 7 days |
| Daily logical bundle | `scheduled_backup.py` 02:00 UTC | `/var/backups/talklee` | 30 days, minimum 7 | That day's snapshot |
| Off-host encrypted copy | Same job | `/mnt/talklee-offhost/backups` | 30 days, minimum 7 | That day's snapshot, after any loss |

### 14.2 What a bundle contains

`database.dump` (pg_dump custom format, compressed), `database.dump.sha256`, and `receipt.json`:
creation time, size, SHA-256, snapshot time, Alembic heads, row counts of the four critical tables
and of every table, source database name, a fingerprint of the source endpoint, the cluster id, the
dump client version and which RLS mode was used. The receipt holds no customer data.

### 14.3 What an off-host copy contains

`database.dump.gpg` (OpenPGP symmetric, AES-256, S2K SHA-512 with the maximum iteration count,
integrity-protected), `database.dump.sha256`, `receipt.json` (copied in clear so retention and health
work without the passphrase) and `replica.json` (the ciphertext's size and SHA-256 and the plaintext's
SHA-256). Before a copy is published the tool decrypts it as a stream, without writing the plaintext
anywhere, and compares the result with the original checksum.

### 14.4 Retention rules

* A bundle is deleted only when it is older than `--keep-days` and at least `--keep-minimum` newer
  bundles exist.
* Nothing is deleted unless the newest bundle verifies, so a broken schedule never ages out the last
  good archive.
* Unknown directories and staging directories are never touched.
* Ordering uses the snapshot time recorded in the receipt, not file dates.

### 14.5 Today's nightly backup until the cut-over

`talky-db-backup.timer` keeps running `backend/deploy/db-backup.sh` on the current server. After this
branch is deployed, give it the platform-admin id (section 21.1) with a systemd drop-in, then run it once
and check its log no longer warns:

```bash
sudo systemctl edit talky-db-backup.service      # add the two lines below, save
#   [Service]
#   Environment=TALKY_BACKUP_PLATFORM_ADMIN_ID=<uuid of an active, verified platform admin>
sudo systemctl start talky-db-backup.service && journalctl -u talky-db-backup.service -n 20
```

Copy its output off the server until the new timers replace it.

### 14.6 How a backup is proven

* At creation: read-only snapshot, RLS coverage proof, `pg_restore --list` succeeds, size plausibility
  against the previous bundle, checksum, receipt.
* Hourly: `talklee-db-backup-health.timer` verifies the newest bundle's checksum and age (26 h).
* Off-host: decrypted and compared before publishing; ciphertext checksum verified by retention and
  health.
* Monthly: the restore drill in section 15.7. A backup is not considered proven until a restore has
  been tested.

---

## 15. Recovery procedures

Times in brackets are targets for the owner to approve; section 18 has measured rehearsal times on
a small synthetic database.

### 15.1 Application server lost (data is safe)

1. Rebuild the server with the installation script (deliverable 2).
2. Restore `/etc/talklee/secrets/` from the password manager (files of section 9.2) and the CA file.
3. Set `DATABASE_URL` and `DATABASE_REQUIRE_RESTRICTED_ROLE=true` in `.env`; start the services.
4. Re-mount the off-host store and re-enable the backup and monitor timers.

No data restore is involved. [Recovery time = rebuild time.]

### 15.2 Database instance lost or broken (managed)

1. In the provider console, restore the instance (or a new one) to the latest point in time.
2. If the host name changed, update `DATABASE_URL` (and the `pg_service.conf` entries) and restart the
   services.
3. Run `roles.py verify` and the monitor; confirm `db_runtime_posture` after the restart.

[Data loss ≤ 5 minutes; recovery ≤ 1 hour.]

### 15.3 Database server lost (dedicated)

1. Build a new server (section 12.2).
2. Either restore physically:
   ```bash
   python3 scripts/database/pitr.py restore --base-backup <newest base> \
     --archive-dir /mnt/talklee-offhost/wal --target-data-dir /var/lib/postgresql/16/restored \
     --target-latest --start
   ```
   or logically from the newest bundle with `cutover.py load` (data up to the last daily bundle).
3. Point `DATABASE_URL` at the new server, restart, verify as in 15.2.

### 15.4 Undo a bad change (point in time)

1. Find the moment just before the mistake (deploy time, audit log, the operator's note), in UTC.
2. Restore to a **new** instance or data directory at that time; never over the live one:
   managed console "restore to point in time", or
   `pitr.py restore ... --target-time 2026-11-03T14:05:00+00:00 --target-data-dir <new> --start`.
3. Compare the restored copy with production for the affected tables.
4. Either switch `DATABASE_URL` to the restored instance (everything after that moment is lost), or
   copy the damaged rows back from the restored copy into production with reviewed SQL.

### 15.5 Restore one tenant's data

1. Restore the newest suitable bundle into a scratch database:
   `recovery.py restore --service talklee_restore --bundle <bundle> --database talky_restore_tenant ...`.
2. Export that tenant's rows table by table (`WHERE tenant_id = '<id>'`), following foreign keys from
   `tenants` downwards.
3. Import into production as the owner role inside one transaction, after review.
4. Drop the scratch database.

### 15.6 Both servers lost: restore from the off-host copy

1. Provision a new database (section 12) and a new application server.
2. Fetch the newest replica directory from the off-host store and the passphrase from the password
   manager.
3. Decrypt it into an ordinary verified bundle:
   ```bash
   python3 scripts/database/recovery.py decrypt-replica --replica /restore/offhost/<stamp> \
     --destination /var/backups/talklee/recovered --passphrase-file /etc/talklee/secrets/backup/replica.passphrase
   ```
4. Load it: `cutover.py load --bundle /var/backups/talklee/recovered --database talkyai ...`, then
   `roles.py apply` with new passwords.
5. Set `DATABASE_URL`, start, verify (section 13.4).

[Data loss ≤ 24 hours; recovery = provisioning + decrypt + load time.]

### 15.7 Monthly restore drill (deliverable 4 "Backup & restore test")

| Deliverable step | Command | Pass criterion |
|---|---|---|
| 1. Take a backup | The newest daily bundle, or `recovery.py backup` | `archive_verified` |
| 2. Restore into a temporary database | `recovery.py restore --service talklee_restore --bundle <b> --database talky_restore_YYYYMM --runtime-role <probe role> --expected-head <head> --receipt <r>` | `sql_restore_verified`, tenant isolation `passed` |
| 3. Point a staging instance at it | `cutover.py load` into `talky_restore_app_YYYYMM`, `roles.py apply`, start a staging instance with that `DATABASE_URL` | Application boots as `talklee_app` |
| 4. Verify tenant, campaign, call and other records | Receipt's per-table counts equal the bundle's; open tenants, campaigns, calls in the staging dashboard | Exact counts; records readable |
| 5. Record the process and time | Keep the receipts in `/var/backups/talklee-evidence/` and note the elapsed time | Within the approved recovery time |

Do the drill from the **off-host** copy at least once a quarter (decrypt first), so the passphrase
escrow is tested too. Drop the scratch databases afterwards.

### 15.8 Expected recovery times

| Scenario | Rehearsal (synthetic, small) | Production estimate |
|---|---|---|
| Logical bundle → verified application | 14.9 s | Measured at 13.2 step 2; scales with data size |
| Off-host decrypt | 0.7 s | Roughly the time to read the file twice |
| Point-in-time restore (dedicated) | 35.0 s | Base backup copy + WAL replay since it |
| Application recovery after a server restart | 0.5 s | Seconds |
| Managed PITR | n/a | Provider-dependent, usually 10–60 min |

---

## 16. Monitoring and alerts

### 16.1 Checks (every five minutes)

| Check | Warning | Critical | Meaning |
|---|---|---|---|
| `connect` | latency above 250 ms | cannot connect | The database answers `SELECT 1` |
| `tls` | — | not encrypted | The connection uses TLS |
| `restricted_role` | — | superuser, bypass RLS or owner | The runtime role is still restricted (skip with `--skip-role-check` for a monitoring role) |
| `database_size` | ≥ 80 % of capacity | ≥ 90 % | Size against `--capacity-bytes` |
| `connections` | ≥ 80 % of `max_connections` | ≥ 95 % | Connection use |
| `long_transactions` | open > 5 min | open > 30 min | Transactions that block vacuum and hold locks |
| `wraparound` | ≥ 50 % of the limit | ≥ 75 % | Transaction-ID age; vacuum is not keeping up |
| `wal_archiving` | WAL waiting and nothing archived for 30 min | the archive command is failing, or archiving is off while `--require-archiving` is set | Dedicated server PITR health (ok when archiving is off and not required, e.g. provider PITR) |
| `backup_freshness` | — | newest bundle older than 26 h or fails verification | Daily backups are running |

A restricted role cannot see other sessions' start times; grant the monitor's role `pg_monitor`
(read-only statistics) for a complete long-transaction check.

### 16.2 Alert lifecycle

* An alert is sent when the overall status becomes worse than before (ok → warning → critical).
* While a problem lasts, it is repeated every `--realert-minutes` (60).
* One "recovered" message is sent when everything is ok again.
* The state is kept in `--state-file`; if the webhook is unreachable the state is kept so the next run
  retries.
* A Prometheus textfile (`talklee_db_up`, `talklee_db_check_status{check=...}`, `talklee_db_size_bytes`,
  `talklee_db_capacity_ratio`, `talklee_db_connections`) is written for a node exporter.
* Unit failures (backup, health) must also reach the receiver: point systemd `OnFailure=` or the host's
  existing health watch at it.

### 16.3 What to do when an alert fires

| Alert | First actions |
|---|---|
| `connect` critical | Check the provider status page and console; `pg_isready -h <host>`; check the allow-list still contains the application server's address; check the CA file and certificate dates |
| `tls` critical | Someone changed the URL or service file to a non-TLS mode; restore `sslmode=verify-full` |
| `restricted_role` critical | The application is using the wrong credentials; compare `.env` with the runbook and restart; review who changed it |
| `database_size` warning/critical | Look for unexpected growth (`pg_total_relation_size` per table); plan a resize; check retention jobs in the application |
| `connections` warning/critical | Look for a stuck worker (`pg_stat_activity` by `application_name`); restart that service; review pool sizes |
| `long_transactions` | Identify the session in `pg_stat_activity`; if it is idle in a transaction, end it with `pg_terminate_backend` as the administrator |
| `wraparound` | Check autovacuum is running; run `VACUUM (FREEZE)` on the oldest tables as the owner in a quiet period |
| `wal_archiving` | Check the archive mount and disk; `pitr.py archive-health`; fix and take a new base backup |
| `backup_freshness` | `journalctl -u talklee-db-backup`; run the service by hand; check the off-host mount and passphrase file |

---

## 17. Routine operations

### 17.1 Rotate a database password

For `talklee_app` (repeat the pattern for `talklee_backup` or `talklee_owner`):

1. Generate the new password into a new file:
   `python3 scripts/database/roles.py generate-password --output /etc/talklee/secrets/talklee_app.password.new`
2. Prepare the new `DATABASE_URL` in a copy of `.env` (do not restart yet), and the new `pgpass` line.
3. Change the password and prove it:
   ```bash
   python3 scripts/database/roles.py --service-file /etc/talklee/secrets/pg_service.conf rotate-password \
     --service talklee_admin_db --database talkyai --role talklee_app \
     --password-file /etc/talklee/secrets/talklee_app.password.new \
     --receipt /var/backups/talklee-evidence/rotate-$(date -u +%Y%m%dT%H%M%SZ).json
   ```
   It changes only the password (no grants, no ownership, no table locks) and logs in with it.
4. Immediately put the new `.env` and `pgpass` in place and restart the services. Connections already
   open keep working until then; new connections need the new password, so keep this gap short.
5. Update the password manager; replace the old password file with the new one.

For `talklee_owner`, update `migration.env`; no service restart is needed. For `talklee_backup`, update
`pgpass`; the next timer run uses it.

### 17.2 Rotate the provider administrator password

Change it in the provider console, then update `pgpass` and the password manager. Nothing in the
application uses it.

### 17.3 Rotate or renew the TLS CA certificate

1. When the provider announces a new CA, download it and append it to `database-ca.pem` (a bundle
   with both old and new certificates is valid).
2. Restart the services; confirm `tls=True` in `db_runtime_posture` and a passing monitor run.
3. After the provider switches, remove the old certificate from the bundle.

For a dedicated server, issue a new server certificate before the old one expires, install it with the
installer (or copy and reload PostgreSQL), and keep the CA unchanged.

### 17.4 Rotate the backup passphrase

1. Create a new passphrase, store it in the password manager **next to** the old one.
2. Replace `/etc/talklee/secrets/backup/replica.passphrase`. New off-host copies use the new passphrase.
3. Keep the old passphrase in the password manager until the last copy made with it has aged out
   (30 days plus the minimum-copy rule). `decrypt-replica` takes whichever passphrase file you give it.

### 17.5 A migration adds a table

Nothing to do: migrations run as `talklee_owner`, and default privileges give `talklee_app` row access
and `talklee_backup` read access to the new table. New tenant tables must enable and force RLS, as
migration 0013 does, or `roles.py verify` fails (`tenant_tables_force_rls`).

### 17.6 Resize the plan or server

Managed: change the plan in the console (most providers resize with a short restart; the application
reconnects by itself). Then update `--capacity-bytes` in the monitor unit and, if `max_connections`
changed, re-run preflight's capacity check and adjust the runtime connection limit with
`roles.py apply --runtime-connection-limit`.

### 17.7 Upgrade the PostgreSQL major version

Use the provider's in-place upgrade or a logical move: take a bundle, `cutover.py load` into a new
instance on the new major, switch `DATABASE_URL`. Run preflight against the new instance first. Install
the matching client on the application server.

### 17.8 Run the preflight or monitor by hand

Both are read-only and safe at any time (section 8).

---

## 18. Testing and evidence

### 18.1 End-to-end rehearsal

`scripts/database/tests/local_separation_rehearsal.py` (run with the backend's Python and PostgreSQL 16
binaries) builds three disposable loopback clusters and runs these stages:

| Stage | What is proven |
|---|---|
| Legacy | Today's shape: real schema (complete schema + every migration to `0073`), a NOSUPERUSER NOBYPASSRLS role owning 730 relations, synthetic tenants, campaigns, leads, calls and a platform-admin-only row |
| Source backup | Backups that RLS would filter are refused; a bypass-only backup is refused because of `assistant_action_resolutions`; the complete read-only bundle holds every row of all 109 tables; source unchanged |
| External server | Rendered TLS-only, allow-listed configuration; non-superuser administrator; plaintext refused |
| Preflight | Every check ok on the real pair; an oversized pool budget is refused; source unchanged |
| Cut-over | 109 tables loaded with exact counts; least-privilege verification passed; SCRAM passwords; runtime connection cap applied; source unchanged |
| Migrations | Alembic as the owner via `MIGRATION_DATABASE_URL` over verify-full; a later table is usable without new grants |
| Application | The application's own pool boots as `talklee_app` over TLS 1.3 with the restricted role required; tenant isolation; cross-tenant writes, CREATE, TRUNCATE, DROP and disabling RLS refused; refuses to boot as the owner |
| Cost ledger | The application's flush writes as `talklee_app` into a forced-RLS table |
| Restart | The same application process serves queries again on fresh connections after a server restart |
| Rotation | `rotate-password` changes the runtime password; the old one is refused; the application works with the new one |
| Backups | Backup role captures every row; off-host copy only as AES-256 ciphertext, proven to decrypt; retention and health on the encrypted root; local retention and health |
| Restore drill | Off-host copy decrypted into a verified bundle; SQL restore with exact counts, heads and tenant isolation; application reads from the restored copy over TLS |
| Point in time | Base backup over TLS replication; WAL retention removes segments older than the base backup (real file names); the pruned archive still restores to a chosen second between two changes |
| Monitoring | All checks ok against the new server; Prometheus textfile |
| Final | The legacy database is identical to the start (rows, schema, policies, owners, roles) |

Run it:

```bash
python scripts/database/tests/local_separation_rehearsal.py \
  --bin-dir "C:/Program Files/PostgreSQL/16/bin" --run --receipt test-output/database-separation/receipt.json
```

Latest result (2026-10-28): see section 18.4.

### 18.2 Other rehearsals

| Script | Proves |
|---|---|
| `local_pitr_rehearsal.py` | WAL archiving, base backup, restore to a time and to latest |
| `local_monitor_rehearsal.py` | Monitor checks, alert, repeat and recovery messages to a local receiver |
| `local_db_server_rehearsal.py` | Rendered server config: TLS only, allow-list, wrong CA and wrong host name refused |
| `local_postgres_rehearsal.py` | Original backup/restore rehearsal of `recovery.py` |

### 18.3 Unit tests

`python -m unittest discover -s scripts/database/tests -p "test_*.py"` (standard library and gpg, no
database). They cover every refusal of every tool: role SQL and verification rules, SCRAM verifiers,
password rotation, cut-over refusals, RLS coverage, retention, encrypted replicas (wrong passphrase,
tampering, retention without the passphrase), preflight rules, PITR, monitor thresholds and alerts,
server-config rendering, and the systemd units' command lines. They run in CI on Ubuntu (job
"Database Separation & Recovery Tools"), and were run locally on both Windows and Ubuntu, because the
protected-file rules only apply on Linux.

Backend tests: `tests/unit/test_database_policy.py`, the production-gate fixtures,
`test_infra_prod_config.py` (dependency wait), `test_bh_ops_scripts_backup.py` (nightly backup),
`test_production_migration_deploy_contract.py` (migration unit) and `test_provider_cost_ledger.py`.

### 18.4 Results

Runs of 2026-10-28 (local Windows 11, PostgreSQL 16.1 client and server; Ubuntu 24.04 for the
Linux unit-test run):

| Check | Result |
|---|---|
| End-to-end rehearsal (before PITR retention was added) | Passed, 39 checks, 204.6 s: preflight all `ok`; cut-over 109 tables / 279 rows exact; application recovered 0.5 s after a server restart with 0 failed attempts; rotation; encrypted off-host copy; restore drill from it 14.9 s; PITR 35.0 s; legacy database unchanged |
| Rehearsal stage for PITR retention on real WAL | Written, **not yet run** (the machine ran out of memory); unit tests cover the rule with synthetic segment names |
| Database tool unit tests | 136 passed on Windows and 136 passed on Ubuntu 24.04 |
| Guard tests | systemd units and documented commands parse with the tools' own parsers; both fail on the previous versions |
| Backend tests for the changed code (`test_provider_cost_ledger.py`) | 9 passed; the new test fails on the previous code |
| CI contract (`test_ci_workflow_contract.py`) | Every string it requires is present in `ci.yml` (checked with grep; the test run itself was stopped for lack of memory) |
| Ruff gate (`ruff check app/ --select F --extend-ignore F401,F841`) | All checks passed |
| Backend canonical suite on this commit | **Not completed**: the host was swapping (≈ 640 MB free, 23.8 GB committed by other sessions) and the run was stopped at 14 %. The previous commit's full run passed (14,454 passed, 238 skipped); the only backend code changed since is `provider_cost_ledger.py` |

Re-run before merging: `python -m pytest tests/unit tests/security -q` in `backend/` and the end-to-end
rehearsal (section 18.1), on a machine with free memory or in CI.

---

## 19. Owner decisions and go-live checklist

### 19.1 Decisions

| Decision | Recommendation | Why |
|---|---|---|
| Database host | Managed PostgreSQL 16 in the same data centre as the application server, ≥ 100 connections, PITR ≥ 7 days | Backups, PITR, failover and patching handled by the provider; latency stays sub-millisecond |
| Off-host backup store | A Hetzner Storage Box (or another provider's object storage) mounted at `/mnt/talklee-offhost` | Separate from both servers; copies are encrypted, so the store does not need to be trusted |
| Alert receiver | A Slack incoming webhook to an operations channel someone watches | The monitor and provider alerts need a human |
| Recovery targets | Data loss ≤ 5 minutes (PITR), ≤ 24 hours (off-host bundles); recovery ≤ 1 hour | Achievable with the chosen layers |
| Maintenance window | Rehearsed load time + 20 minutes, outside calling hours | Measured in 13.2 |
| Platform-admin id for complete backups | One active, verified platform admin | Needed by both today's nightly script and the new backups |

### 19.2 Go-live checklist

- [ ] Branch reviewed, merged and deployed (the application changes are inert with the loopback URL).
- [ ] Confirmed production's current `DATABASE_URL` host is `127.0.0.1` or `localhost` before deploying.
- [ ] `TALKY_BACKUP_PLATFORM_ADMIN_ID` set for today's nightly backup.
- [ ] Database provisioned (section 12), CA file installed, allow-list set.
- [ ] Secrets created and escrowed in the password manager, including the backup passphrase.
- [ ] Off-host store mounted; alert webhook configured.
- [ ] Preflight passed; recommended connection limit noted.
- [ ] Rehearsal load done; window agreed and announced.
- [ ] Cut-over done; verification of 13.4 passed; receipts kept.
- [ ] New timers enabled, old backup timer disabled; first backup and off-host copy verified.
- [ ] First restore drill from the off-host copy done within a week.
- [ ] Old container removed after 14 days, with a final archived dump.

---

## 20. Known limits and what was not verified

* **Not run against a real provider or second server.** All rehearsals used local loopback clusters.
  The preflight and the rehearsal load (13.1, 13.2) are where the real provider is checked.
* **Not run on a real network.** TLS, certificate verification and allow-lists were proven locally;
  provider firewalls and private networks are checked by the preflight.
* **`install_db_server.sh` was not run on Ubuntu.** It is syntax-checked and its rendered configuration
  is proven by the server-config rehearsal. It matters only for the dedicated-server option.
* **Production data volume and timings are unknown.** The rehearsal database is small; measure with the
  rehearsal load.
* **Alert delivery to the real receiver** was tested only against a local receiver.
* **Hard network failover** (the database's address disappears without closing connections) was not
  simulated. The restart test proves clean reconnection; a dead TCP connection is detected by the
  pool's command timeout (60 s) or the operating system's keepalive. Lower the host's
  `net.ipv4.tcp_keepalive_time` on the application server if the provider documents abrupt failovers.
* **Schema objects outside the migration history.** Production may hold tables created by standalone
  SQL files (`backend/database/migrations/*.sql`). They move with the dump; the preflight checks their
  row-level security, but their content was not seen here.
* **The runtime role cannot see other sessions' query details** without `pg_monitor`; the monitor's
  long-transaction check is then partial.

---

## 21. Findings made during this work

### 21.1 Today's nightly backups miss one table's rows

`backend/deploy/db-backup.sh` dumps through `app.bypass_rls`. Migration 0062 deliberately keeps
`assistant_action_resolutions` out of that bypass (only platform admins may read it). So every nightly
backup so far contains that table with **no rows**, and the script's checks cannot notice. Proven on
the real schema: bypass only gives 0 rows, bypass plus a platform-admin identity gives the row.

* Fixed in the new tools: a backup that RLS would filter is refused, naming the table.
* Fixed in the old script (when deployed): set `TALKY_BACKUP_PLATFORM_ADMIN_ID`; until then it warns
  every night.

### 21.2 The provider cost ledger cannot write under row-level security

`provider_cost_ledger.py` wrote its batch with `COPY`. PostgreSQL refuses `COPY FROM` whenever
row-level security applies to the role, even when the policy would admit every row. Proven locally:

| Role | RLS | `COPY` | `INSERT` |
|---|---|---|---|
| Table owner | enabled, not forced | works | works |
| Table owner | forced | refused | works |
| Non-owner (the restricted runtime role) | either | refused | works |

The table `tenant_provider_cost_events` comes from a standalone SQL file, not from a migration. If
production has it and migration 0013 forced its RLS, the ledger has been failing on every flush
(logged as `cost_ledger_flush_failed`); after the cut-over it would fail in every case. Fixed: the batch
is written with `INSERT` inside the same bypass transaction, proven by the rehearsal's cost-ledger stage
as the restricted role. Still open (owner decision): whether the table exists in production, and adding
it to the migration history so fresh installations have it.

### 21.3 The database tool tests had never run on Linux

Eleven tests failed on Ubuntu because their fixtures created password and service files that other
users could read; the tools correctly refuse those on Linux. The fixtures now create 0600 files, the
tests pass on both systems, and CI runs them on Ubuntu.

### 21.4 Three timers would have failed on their first run

* `talklee-db-basebackup.service` read `/etc/talklee/pg_service.conf` (no such file in the layout) and
  had no password file.
* `talklee-db-backup.service` (user `talklee-backup`) and `talklee-db-monitor.service` (user
  `talklee-monitor`) read the same `pg_service.conf` and `pgpass`. libpq and the tools accept only an
  owner-only (0600) password file, so one of the two users could never read it.
* `talklee-db-monitor.service` wrote metrics to `/var/lib/node_exporter/textfile` and allowed writes only
  there; without a node exporter that directory does not exist and systemd refuses to start the unit.

Fixed with per-user secrets directories (section 9.2) and metrics in the monitor's state directory. A
guard test now parses every unit's command line with the tool's own argument parser and checks that
each unit reads only its own user's secrets and writes only where systemd allows; it fails on the
previous units.

### 21.5 The health check could pick the wrong "newest" backup

`recovery.py health` chose the newest bundle by directory modification time. An older bundle copied in
later would be taken for the newest. It now orders by the snapshot time in the receipt, like
retention, and fails on any unreadable receipt instead of skipping it.

---

## 22. Frequently asked questions

**Will deploying this branch change the current database?**
No. There is no migration. The application changes only act on a remote database URL or when
`DATABASE_REQUIRE_RESTRICTED_ROLE=true` is set; with today's loopback URL they log a warning about the
owning role and nothing else. Confirm the current URL's host is `127.0.0.1` or `localhost` first.

**Why does the application still log a warning about its role today?**
Because today's role owns every table. The warning stops after the cut-over to `talklee_app`.

**Can the application run DDL after the cut-over?**
No, by design. Schema changes run in `talky-migrate.service` as `talklee_owner`. If a code path ever
needs DDL at runtime, it fails with a permission error, which is the intended signal.

**What if the provider does not allow BYPASSRLS for the backup role?**
Use `--rls-bypass-guc --rls-setting app.current_user_id=<platform admin id>`; that is the default
documented here and is what the rehearsal uses.

**Can I use a connection pooler such as PgBouncer?**
Only in session mode, and never for backups. The application relies on session settings inside
transactions; transaction-mode pooling would break the RLS context.

**What happens to running calls during the cut-over?**
None should be running: step 1 waits until there are no live calls. Calls cannot be moved between
databases mid-call.

**How do I know a backup is good without restoring it?**
You do not, fully. Checksums and counts catch most problems, which is why the monthly drill restores a
copy and opens it in a staging application.

**Where is the backup passphrase?**
On the application server at `/etc/talklee/secrets/backup/replica.passphrase` and in the owner's password
manager. Never anywhere else.

**What if the off-host store is full?**
The off-host copy fails, so the backup job fails and alerts. Local bundles keep being protected by the
retention rule (nothing is deleted while the newest copy is not verified). Free space or enlarge the
store, then run the backup service by hand.

**Does encryption slow the backup down?**
gpg with compression off processes hundreds of megabytes per second; the decrypt-and-compare step
reads the copy once more. For today's database size this is seconds.

---

## 23. Glossary

| Term | Meaning |
|---|---|
| Bundle | One published backup directory: dump, checksum, receipt |
| Replica | The off-host copy of a bundle (encrypted by default) |
| Receipt | JSON evidence a tool writes; never overwritten, never contains secrets |
| Cut-over | The maintenance window in which production moves to the new database |
| PITR | Point-in-time recovery: restore to any moment using base backups and WAL |
| WAL | PostgreSQL's write-ahead log; archived for PITR |
| RLS | Row-level security: policies that limit rows per tenant |
| FORCE RLS | RLS that applies to the table owner too |
| `app.bypass_rls` | The application's own setting that its policies accept for platform-internal work |
| Restricted role | A role that is not a superuser, does not bypass RLS, cannot create roles or databases and owns nothing |
| SCRAM verifier | The salted, hashed form of a password that PostgreSQL stores; computed locally so the password is never sent |
| `verify-full` | libpq TLS mode that checks the certificate chain and the host name |
| Preflight | Read-only readiness check of the target (and source) before the window |
| Headroom | Connections kept free for timers, backups, monitoring, migrations and operators |

---

## 24. Appendices

### A. Read-only verification queries

Run as the administrator against `talkyai`. None of these write.

```sql
-- Role attributes and connection limits
SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolconnlimit
  FROM pg_roles WHERE rolname LIKE 'talklee%';

-- Objects not owned by the owner role (expect 0 outside extensions)
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND pg_get_userbyid(c.relowner) <> 'talklee_owner';

-- Tables with RLS enabled but not forced (expect none)
SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND c.relrowsecurity AND NOT c.relforcerowsecurity;

-- Connections by application
SELECT application_name, usename, state, count(*) FROM pg_stat_activity GROUP BY 1, 2, 3 ORDER BY 4 DESC;

-- Is this session encrypted?
SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid();

-- Largest tables
SELECT relname, pg_size_pretty(pg_total_relation_size(c.oid)) FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind = 'r'
 ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 10;
```

### B. Receipt shapes (abridged)

```json
{"operation": "preflight", "status": "passed",
 "checks": {"connection_capacity": {"status": "ok", "detail": "97 usable connections ..."}},
 "recommendations": {"runtime_connection_limit": 87},
 "pool_budget": {"talky-api.service": 15, "talky-api-web.service": 12}}

{"operation": "replicate", "status": "replica_verified", "encrypted": true,
 "bundle": "20261028T020000Z", "sha256": "<plaintext sha256>", "ciphertext_sha256": "<sha256>"}

{"operation": "rotate_password", "status": "passed", "role": "talklee_app",
 "database": "talkyai", "new_password_login": true}
```

### C. Files in this change

| Path | Role |
|---|---|
| `backend/app/core/database_policy.py` | URL TLS policy and runtime posture check |
| `backend/app/core/db.py`, `backend/app/core/prod_gate.py` | Wiring of the policy |
| `backend/app/domain/services/provider_cost_ledger.py` | INSERT instead of COPY |
| `backend/deploy/infra/wait-for-deps` | Remote database wait |
| `backend/deploy/db-backup.sh` | Platform-admin identity for complete nightly dumps |
| `backend/systemd/talky-migrate.service` | Owner credentials for migrations |
| `backend/.env.example`, `scripts/installation/backend.env.example` | New settings |
| `scripts/database/preflight.py` | Readiness check |
| `scripts/database/roles.py` | Roles, verification, rotation, connection cap |
| `scripts/database/cutover.py` | Load into the new server |
| `scripts/database/recovery.py` | Backups, encrypted replicas, retention, restore |
| `scripts/database/scheduled_backup.py` | Daily job |
| `scripts/database/monitor.py`, `scripts/database/pitr.py` | Monitoring, PITR |
| `scripts/database/db_server/` | Dedicated-server configuration and installer |
| `scripts/database/systemd/` | Timers and services |
| `scripts/database/pg_service.conf.example` | Connection services template |
| `scripts/database/tests/` | Unit tests and rehearsals |
| `.github/workflows/ci.yml` | CI job for the tools |
| `docs/production-readiness/database-separation.md` | This document |
