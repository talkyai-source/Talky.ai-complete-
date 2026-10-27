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
transaction open, exports its snapshot, counts tenants/campaigns/calls/leads and
records Alembic heads, then runs `pg_dump` against that same snapshot. Commits
after the snapshot cannot create false count mismatches. A custom archive must
pass `pg_restore --list`, size checks and SHA-256 validation before its complete
directory is published. Receipts contain counts and metadata, not row contents.

A successful bundle contains `database.dump`, `database.dump.sha256` and
`receipt.json`. It is a database logical dump, not a cluster backup: role
definitions, original object ownership/ACLs, configuration, encryption keys and
recording objects require their own approved recovery procedure. Ownership and
privilege grants are deliberately reapplied on the isolated destination.

Copy complete bundles through the approved encrypted off-host storage process,
then verify the copied checksum. This CLI does not choose a storage provider,
upload customer data, prune archives or assert that PITR is enabled. Record
retention, expiry, capacity, PITR capability and recoverable key ownership in
the operating handover before enabling a production schedule.

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

The files in `systemd/` are opt-in templates. They use a dedicated
`talklee-backup` OS user, protected files under `/etc/talklee/secrets`, and
`/var/backups/talklee`. Create the user/directories and make the credential/CA
files readable only by that user before installing the units. The scheduled
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

## Local verification

```bash
python -m unittest discover -s scripts/database/tests -v
python scripts/database/tests/local_postgres_rehearsal.py \
  --bin-dir /usr/lib/postgresql/16/bin --run \
  --receipt test-output/database-recovery/local-rehearsal.json
```

The explicit integration command creates two disposable clusters under the
repository's ignored `test-output` directory, bound only to loopback, with
synthetic data. It uses no existing server credentials and stops/removes only
its owned clusters. `--allow-local-plaintext` exists solely for that synthetic
loopback fixture; it cannot authorize plaintext to a remote host.

The implementation follows PostgreSQL's [connection service rules](https://www.postgresql.org/docs/current/libpq-pgservice.html),
[password file guidance](https://www.postgresql.org/docs/current/libpq-envars.html),
[exported snapshot support in pg_dump](https://www.postgresql.org/docs/current/app-pgdump.html)
and [pg_restore behavior](https://www.postgresql.org/docs/current/app-pgrestore.html).
