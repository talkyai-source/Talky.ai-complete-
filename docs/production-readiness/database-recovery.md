# Database separation and recovery

The local tools in [scripts/database](../../scripts/database/README.md) support
read-only auditing, consistent logical backups, isolated restore rehearsal and
backup health checks. They use direct PostgreSQL services with verified TLS
and separate identities. They do not automatically provision a provider,
switch production endpoints or certify recoverability of the complete product.

## Establish the live facts

Before a database move, inspect the running application's configured endpoint,
database host inventory, PostgreSQL/client/extension versions, schema heads,
role capabilities, storage locations and last successful backup/restore. The
October 8 release record reported a failed backup size comparison; establish
the current condition rather than treating that historical result as current.

Copy the [service template](../../scripts/database/pg_service.conf.example)
outside the repository. Use a separate password file, with credentials and CA
files readable only by the operator or backup user. Every production service
uses `sslmode=verify-full` and a certificate-valid hostname. Supply all known
application-server IP addresses to the audit:

```bash
export PGSERVICEFILE=/etc/talklee/secrets/pg_service.conf
export PGPASSFILE=/etc/talklee/secrets/pgpass
python3 scripts/database/recovery.py audit --service talklee_runtime \
  --app-address 192.0.2.10 --receipt /secure/evidence/database-audit.json
```

Replace documentation addresses before running. The runtime audit rejects
superuser, RLS bypass, role-creation and database-creation privileges. A
different endpoint still needs inventory evidence that application-host loss
cannot destroy the database. Audit receipts do not assert that backups or
monitoring exist.

## Preserve and prove a backup

Investigate a failed dump before replacing the last known-good archive. The
backup command uses one exported read-only snapshot for both inventory counts
and `pg_dump`, then verifies archive structure, size and checksum before
publishing the complete bundle. Follow the exact [backup commands](../../scripts/database/README.md#backup).

Use a backup identity able to read all included tables; an application role
subject to row filtering is not a complete backup identity. Record the snapshot
time, client/server versions, schema, size, checksum and approved retention.
Transfer complete bundles through the approved encrypted off-host process and
verify the destination checksum. Database-only dumps do not contain keys,
recording objects, cluster roles or the full runtime configuration.

The optional [systemd templates](../../scripts/database/systemd/) schedule a
daily backup and an hourly local checksum/age check. Provision the dedicated
backup user and protected directories first. Route failed jobs and stale,
corrupt or missing backups to the existing approved alert receiver and prove
delivery. Configure capacity monitoring and retention/expiry at the selected
storage/database service. Record PITR support and configuration if available.
These provider-specific steps remain unconfigured until the target is known.

## Rehearse restoration

1. Use an authorized archive on a separate isolated database endpoint. Supply
   the expected source Alembic head and a distinct restricted probe role.
2. Run the [restore command](../../scripts/database/README.md#isolated-restore-rehearsal)
   against a new `talky_restore_*` name. It refuses the source endpoint and
   existing targets; no existing database is overwritten or dropped.
3. Review exact snapshot counts and schema heads, representative read contracts,
   and own/foreign-tenant RLS results. Preserve the scratch database and receipt
   for investigation; explicitly clean it up after the review.
4. Rehearse required migrations using the separate migration identity. Derive
   heads from the selected code and compare actual schema. Use the maintained
   [bootstrap procedure](../../backend/database/MIGRATIONS.md) for genuinely empty
   databases, not a false `stamp head` shortcut on an older snapshot.
5. Reapply reviewed ownership, grants and default privileges for objects created
   by the migration owner. Verify the actual runtime role's required reads and
   writes and tenant boundaries. The restore probe grants SELECT only on three
   scoped tables; it is not a complete application-permission provisioning tool.
6. Connect an isolated application to restored data with outbound workers and
   real provider effects prevented. Verify tenants, campaigns, calls, billing
   and other critical records through the application. The existing
   `backend/scripts/verify_restore_compatibility.py` provides an additional
   pool/import check when configured for the isolated server.
7. Recover recordings, configurations and encryption keys; reconcile previously
   deleted records and pending/unknown actions. Do not blindly replay Redis jobs.

The receipt's elapsed SQL restore time is not end-to-end recovery time. Measure
from the declared incident/rebuild starting point until the service is usable,
and compare the last recoverable committed record with the approved data-loss
target. Agree RTO, RPO and retention with the owner before claiming success.

## Prepare the production move

Produce a reviewable cutover record containing source and destination identity,
verified archive/restore evidence, exact code/schema compatibility, approved
grants, maintenance window, freeze/drain procedure, final synchronization,
endpoint/credential change, acceptance checks and rollback boundaries.

Keep `DATABASE_URL` as the restricted runtime connection. `MIGRATION_DATABASE_URL`
is supported by Alembic for privileged migration execution and belongs only in
the operator migration environment. Both paths preserve PostgreSQL URI TLS
parameters and URL-encoded credentials. A secure destination still needs actual
certificate and application connectivity testing.

At cutover, stop new effects and prove the required counters are drained before
the final synchronized backup/migration and endpoint switch. Verify data and
application behavior before reopening traffic. Preserve the old database and
declare the point after which returning to it would require reconciliation of
new writes. Execute this only through the reviewed production release procedure.

## Remaining operating inputs

The external database and backup storage destination, access, tested Linux VM,
real domain/carrier configuration, recovery objectives, maintenance window,
retention/PITR policy and alert receiver are still required to demonstrate the
deliverable. The [handover record](infrastructure-handover.example.json) tracks
these without storing secrets. Local synthetic restoration does not close OP10
or release acceptance.
