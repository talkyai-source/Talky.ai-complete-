# Database recovery tools

These tools prepare and verify the database part of the production deliverables.
They do not move production data automatically, change the application endpoint,
run migrations, replay queues or start calls. A local test receipt is not a
production restore acceptance receipt.

## Configuration

Use Python 3.11 or newer and PostgreSQL `psql`, `pg_dump` and `pg_restore` clients.
The dump client must support the server version, and the restore client must
support the archive version. Keep the exact versions in the release manifest.

Copy `pg_service.conf.example` to a protected location outside the repository.
Use separate services for the restricted runtime role, the backup role and a
restore operator on a disposable database server. Supply credentials through a
protected `PGPASSFILE`; PostgreSQL expects one `host:port:database:user:password`
record per entry, with its usual escaping rules. Service and password files
must have mode 0600 on Linux; on Windows restrict their ACL to the operator.
Never put passwords in the service file, commands or receipts.

Every service must specify its host, port, database, user and `sslmode=verify-full`.
Set `sslrootcert` to the trusted database CA and use a hostname covered by the
certificate. Use direct PostgreSQL connections; exported snapshots require a
session that stays on one server. An existing transaction-pooling endpoint is
not the appropriate backup endpoint.

```bash
export PGSERVICEFILE=/etc/talklee/secrets/pg_service.conf
export PGPASSFILE=/etc/talklee/secrets/pgpass
```

The runtime role should be NOSUPERUSER, NOBYPASSRLS, NOCREATEDB and NOCREATEROLE.
The backup role needs permission to read every included table despite RLS;
`pg_dump` and the inventory deliberately fail if a role would produce a
filtered backup. Keep that credential separate from the application. The
restore operator needs CREATE DATABASE and permission to use the restricted
probe role on the isolated server. The backup and restore roles also need
EXECUTE on the read-only `pg_catalog.pg_control_system()` function so the tool
can compare cluster identifiers rather than trust a hostname or second NIC.
Have the database owner grant that permission where supported; if a managed
provider cannot expose cluster identity, this rehearsal remains blocked until
an equivalent isolation check is implemented and reviewed. Provision required
extension support on the destination.

## Audit without changing the database

Supply every application-server address when comparing the live database
endpoint. Omit that argument if the addresses are not yet known; the receipt
then records separation as unverified and exits nonzero.

```bash
python3 scripts/database/recovery.py audit \
  --service talklee_runtime \
  --app-address 192.0.2.10 \
  --receipt /var/backups/talklee-evidence/runtime-audit.json
```

The query runs read-only and records role capabilities, negotiated TLS, server
version, extension versions and database size. Connection address comparison
does not establish independent physical hosting; the server inventory must
also demonstrate that the database survives loss of the application host.

## Backup

Use a new directory for every bundle. For subsequent backups, pass the previous
successful bundle so the size guard catches unexpected reductions. The default
absolute floor is 1 MiB and the comparison floor is 50 percent; approve stronger
production thresholds from actual database size and expected change history.
A failed guard needs investigation, not a lower threshold to conceal data loss.

```bash
python3 scripts/database/recovery.py backup \
  --service talklee_backup \
  --destination /var/backups/talklee/20261027T020000Z \
  --previous /var/backups/talklee/20261026T020000Z
```

For the first baseline only, omit `--previous`. The tool keeps a repeatable-read
transaction open, exports its snapshot, counts every table (`table_counts`, with
tenants/campaigns/calls/leads repeated in `counts`) and records Alembic heads,
then runs `pg_dump` against that same snapshot. Commits after the snapshot cannot
create false count mismatches. A custom archive must pass `pg_restore --list`,
size checks and SHA-256 validation before its complete directory is published.
Receipts contain counts and metadata, not row contents.

### Row-level security and complete backups

Every tenant table has FORCE row-level security, so a role without BYPASSRLS
sees only what the policies allow. Two ways to take a complete backup:

1. **A BYPASSRLS backup role** (needs a superuser to create; possible on a
   self-managed server). Default mode: the dump runs with `row_security=off`,
   so any table it could not read in full is an error, never a filtered dump.
2. **Session settings** when no BYPASSRLS role can exist (today's cluster has no
   superuser; most managed providers): `--rls-bypass-guc` reads through the
   application's `app.bypass_rls`. Migration 0062 deliberately keeps
   `assistant_action_resolutions` out of that bypass (platform admins only), so
   also name a verified, active platform admin:
   `--rls-setting app.current_user_id=<platform admin user id>`.
   Before the snapshot the tool proves every policy admits every row in this
   session: canonical bypass policies, or row-independent policies (no column
   of their own table, per `pg_depend`) that evaluate true. Otherwise it refuses
   and names the tables. Nothing is written to the database.

The nightly `backend/deploy/db-backup.sh` on today's server uses `app.bypass_rls`
only, so it dumps `assistant_action_resolutions` **empty**. Set
`TALKY_BACKUP_PLATFORM_ADMIN_ID` for that unit to include it; until then it
prints a warning every night.

A successful bundle contains `database.dump`, `database.dump.sha256` and
`receipt.json`. It is a database logical dump, not a cluster backup: role
definitions, original object ownership/ACLs, configuration, encryption keys and
recording objects require their own approved recovery procedure. Ownership and
privilege grants are deliberately reapplied on the isolated destination.

### Off-host copy and retention

`replicate` copies one verified bundle to a second root (a mounted off-host
store, e.g. a Hetzner Storage Box or an object-store mount) through a staging
directory and publishes it only after verification; replicas are never
overwritten. With `--passphrase-file` only `database.dump.gpg` leaves the server
(OpenPGP symmetric AES-256 via gpg, integrity protected, private throwaway
keyring), and the copy is decrypted as a stream and compared with the original
checksum before it is published; receipts stay in clear so retention and
health work without the passphrase. Copying in clear needs the explicit
`--allow-unencrypted` (for storage that encrypts by itself). `decrypt-replica`
turns an encrypted replica back into an ordinary verified bundle for `restore`
or `cutover.py`. Keep a copy of the passphrase outside the server. `prune` applies the retention policy: bundles older than
`--keep-days` (default 30) are deleted, the newest `--keep-minimum` (default 7)
are always kept, unknown directories are never touched, and nothing is deleted
unless the newest bundle verifies (a failing schedule never ages out the last
good archive). `--dry-run` lists what would go.

```bash
python3 scripts/database/recovery.py replicate --bundle /var/backups/talklee/20261028T020000Z \
  --destination-root /mnt/talklee-offhost/backups --passphrase-file /etc/talklee/secrets/backup/replica.passphrase
python3 scripts/database/recovery.py decrypt-replica --replica /mnt/talklee-offhost/backups/20261028T020000Z \
  --destination /var/backups/talklee/recovered --passphrase-file /etc/talklee/secrets/backup/replica.passphrase
python3 scripts/database/recovery.py prune --backup-root /var/backups/talklee --keep-days 30 --keep-minimum 7
```

`scheduled_backup.py` (the systemd timer's entry point) runs backup, then
`replicate` + `prune` on `--replica-root`, then `prune` locally; it accepts the
same `--rls-bypass-guc` / `--rls-setting` options and needs
`--replica-passphrase-file` (or `--allow-unencrypted-replica`) with a replica
root, checked before the dump starts. It does not choose a storage
provider or assert that PITR is enabled; record retention, expiry, capacity,
PITR capability and recoverable key ownership in the operating handover.

## Isolated restore rehearsal

The restore service must connect to `dbname=postgres` on a different PostgreSQL
cluster and actual TCP endpoint from the backup source. Both identities are
checked, including the source reached through another address. The tool creates a new `talky_restore_*`
database and refuses an existing target. Only use an archive from an authorized,
trusted source: PostgreSQL restores execute the archive's database definitions.

```bash
python3 scripts/database/recovery.py restore \
  --service talklee_restore \
  --bundle /var/backups/talklee/20261027T020000Z \
  --database talky_restore_20261027 \
  --runtime-role talklee_restore_reader \
  --expected-head APPROVED_ALEMBIC_REVISION \
  --receipt /var/backups/talklee-evidence/restore-20261027.json
```

Derive `--expected-head` from the intended source release's actual Alembic
history; repeat it for each head if a reviewed release has multiple heads. A
different archive head fails before database creation. Rehearse a required
migration separately on an isolated restored copy; never stamp an old snapshot
at `head` to bypass the mismatch.

Restore verifies exact snapshot counts and schema heads, then grants the
existing restricted probe role SELECT on the three scoped tables in the scratch
database. It checks own-tenant visibility and foreign-tenant denial for two
restored tenants using the application's `app.current_tenant_id` and
`app.bypass_rls` settings. The probe requires RLS on those tables, a distinct
NOSUPERUSER/NOBYPASSRLS/NOCREATEDB/NOCREATEROLE role and two restored tenants with
rows in every covered table. Empty tables return insufficient coverage instead
of a false isolation pass.
It also compiles representative campaign/call read contracts.

The receipt records elapsed SQL restore time and either failure or
`sql_restore_verified`. This does not claim full application recovery, measured
RPO, caller acceptance, complete permissions or recording/key recovery. Restore
preserves the scratch database on success and failure for investigation; the
operator owns its later explicit cleanup. No existing database is dropped.

After SQL verification, follow the installation handover to connect an isolated
application using its restricted runtime credential, reapply reviewed complete
grants, and verify critical records without enabling outbound jobs or provider
effects. The existing `backend/scripts/verify_restore_compatibility.py` offers
an additional application-pool/import check when its DATABASE_URL is configured
to the isolated server. It is a separate check and requires exact code/schema
compatibility; the recovery CLI does not silently run it.

## Scheduling and health

The files in `systemd/` are opt-in templates. Each unit user has its own
secrets directory (`/etc/talklee/secrets/backup/` for `talklee-backup`,
`/etc/talklee/secrets/monitor/` for `talklee-monitor`; 0700, files 0600):
libpq and these tools refuse a password file another user can read, so one
file cannot serve both. Backups go to `/var/backups/talklee`. Create the users
and directories before installing the units. The scheduled
entry point locks the backup root and compares to the previous bundle. It does
not enable itself or alter existing units.

The daily timer proposes 02:00 UTC; the hourly health timer checks the latest
bundle's checksum and a maximum age of 26 hours. Change those values only to
match the approved recovery objectives. A successful daily job does not prove
an achieved 24-hour RPO.

```bash
python3 scripts/database/recovery.py health \
  --backup-root /var/backups/talklee --maximum-age-hours 26
```

Failures return nonzero. Connect both failed backup jobs and stale/corrupt
archive checks to the existing approved alert receiver, and demonstrate actual
delivery. Systemd failure state alone is not an operator notification.

## Least-privilege roles (`roles.py`)

Three roles, one job each. **owner** (`talklee_owner`) owns the schema and every
object and is used only for migrations (`MIGRATION_DATABASE_URL`). **runtime**
(`talklee_app`, the application's `DATABASE_URL`) gets CONNECT/TEMP, schema
USAGE, row DML, sequence use and function EXECUTE: no DDL, no TRUNCATE, owns
nothing, so FORCE row-level security applies to it. **backup**
(`talklee_backup`) gets SELECT on everything. Default privileges grant the
runtime and backup roles every object a later migration creates.

```bash
python3 scripts/database/roles.py generate-password --output /etc/talklee/secrets/talklee_app.password
python3 scripts/database/roles.py apply --service talklee_admin_db --database talkyai \
  --owner-password-file ... --runtime-password-file ... --backup-password-file ... \
  --runtime-connection-limit 87 --receipt /secure/evidence/roles-apply.json
python3 scripts/database/roles.py verify --service talklee_admin_db --database talkyai \
  --receipt /secure/evidence/roles-verify.json
python3 scripts/database/roles.py rotate-password --service talklee_admin_db --database talkyai \
  --role talklee_app --password-file /etc/talklee/secrets/talklee_app.password.new \
  --receipt /secure/evidence/rotate.json
python3 scripts/database/roles.py plan --database talkyai     # print the SQL, no connection
```

`rotate-password` changes one managed role's password and nothing else (no
grants, no ownership, no table locks), then proves the new password logs in;
existing connections keep working until the services restart with the new
URL. `--runtime-connection-limit` caps the runtime role below the server's
usable connections so a leaking pool cannot lock out backups, monitoring or
operators (`preflight.py` recommends the number).

The administrator service may be a superuser or a CREATEROLE/CREATEDB
administrator (a managed provider's admin). Passwords are read from protected
files and sent as client-side SCRAM-SHA-256 verifiers, never in clear. `apply`
runs in one transaction and ends with `verify`; `--from-owner OLD` moves an
existing non-superuser owner's objects (in-place split; refuses superusers).
`--backup-bypassrls` creates the backup role with BYPASSRLS where the
administrator may grant it. The application logs this posture on every pool
start and, with `DATABASE_REQUIRE_RESTRICTED_ROLE=true`, refuses to boot as a
role that is a superuser, bypasses RLS, can create roles/databases or owns
relations.

## Preflight before the cut-over (`preflight.py`)

Read-only on the new server and on today's database. Days before the window it
fails on what would stop the cut-over halfway: an unsupported or older target
version, no TLS, an administrator without CREATEROLE/CREATEDB, too few
connections for the application's pool budget (read from
`backend/systemd/talky-*.service`) plus headroom, missing or untrusted
extensions, non-UTF8 databases, an existing target database, round-trip
latency above 2 ms p95, a `pg_dump` older than the source, a source using half
of the planned storage, and tables whose row-level security is enabled but not
forced. It recommends the runtime connection limit.

```bash
python3 scripts/database/preflight.py --service-file /etc/talklee/secrets/pg_service.conf \
  --target-service talklee_admin --source-service talklee_source --allow-local-plaintext \
  --database talkyai --capacity-bytes 85899345920 --receipt /secure/evidence/preflight.json
```

## Cut-over to the separate server (`cutover.py`)

`load` restores a verified backup bundle into a database that does not exist
yet on a **different** cluster (cluster identity and endpoint are checked, so
the source can never be the target), as the owner role (`pg_restore --role`),
then requires every table's row count and the Alembic heads to match the
snapshot exactly, applies the least-privilege grants and verifies them. The
source database is never contacted; the bundle was taken read-only.

```bash
python3 scripts/database/cutover.py load --service talklee_admin \
  --bundle /var/backups/talklee/cutover --database talkyai --expected-head 0073_payment_test_plan \
  --rls-setting app.current_user_id=<platform admin user id> --receipt /secure/evidence/cutover.json
```

Then set the role passwords with `roles.py apply` and configure the printed
`DATABASE_URL` / `MIGRATION_DATABASE_URL` templates. The full runbook is in
[database separation](../../docs/production-readiness/database-separation.md).

## Monitoring (`monitor.py`)

One read-only round trip checks connection and latency, TLS, the restricted
runtime role, database size against the provisioned capacity, connection use,
long transactions, transaction-ID wraparound, WAL archiving and (with
`--backup-root`) backup freshness. Exit 0/1/2 = ok/warning/critical. It writes
an optional Prometheus textfile and posts Slack-compatible alerts to
`--alert-webhook-url` (or `TALKLEE_ALERT_WEBHOOK_URL`) on a new or worse
problem, every `--realert-minutes` while it persists, and once on recovery.
`systemd/talklee-db-monitor.*` runs it every five minutes.

## Point-in-time recovery for a self-managed server (`pitr.py`)

Managed providers include PITR; for a dedicated server, `archive-settings`
prints the WAL archiving lines, `base-backup` publishes a checksummed
`pg_basebackup` (replication role, verified TLS), `restore` rebuilds a NEW data
directory to `--target-time` (or the latest WAL) on its own loopback port with
archiving off, `archive-health` alerts on a stale archive, and `prune` keeps the
newest `--keep` base backups (7 daily = a 7-day window) and the WAL needed to
replay from the oldest of them, deleting nothing unless the newest verifies.

## Dedicated database server (`db_server/`)

`render_config.py` renders `talklee.conf` (private listen address, TLS 1.2+,
SCRAM, tuning, optional archiving) and a `pg_hba.conf` that rejects every
non-TLS connection and admits only the allow-listed application CIDRs (plus
`--admin-role` into the maintenance database and `--allow-replication` for
base backups). `install_db_server.sh` applies it on Ubuntu 24.04 with a `ufw`
allow-list; it has been syntax-checked, not run.

## Local verification

```bash
python -m unittest discover -s scripts/database/tests -v
python scripts/database/tests/local_postgres_rehearsal.py   --bin-dir /usr/lib/postgresql/16/bin --run   --receipt test-output/database-recovery/local-rehearsal.json
# End to end with the real schema and application code (backend Python):
backend/.venv/bin/python scripts/database/tests/local_separation_rehearsal.py   --bin-dir /usr/lib/postgresql/16/bin --run   --receipt test-output/database-separation/receipt.json
```

The explicit integration commands create disposable clusters under the
repository's ignored `test-output` directory, bound only to loopback, with
synthetic data. They use no existing server credentials and stop only their
owned clusters. `--allow-local-plaintext` admits plaintext only to a loopback
address (a synthetic fixture, or a source database on the same host such as
today's Docker database); it cannot authorize plaintext to a remote host.

The implementation follows PostgreSQL's [connection service rules](https://www.postgresql.org/docs/current/libpq-pgservice.html),
[password file guidance](https://www.postgresql.org/docs/current/libpq-envars.html),
[exported snapshot support in pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html)
and [pg_restore behavior](https://www.postgresql.org/docs/current/app-pgrestore.html).
