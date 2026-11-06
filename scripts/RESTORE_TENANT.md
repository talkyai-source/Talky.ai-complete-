# Tenant restore preparation — F8-07 remains partial

`restore_tenant.py` is a **prepare-only** tool. It restores the complete verified
logical dump into a new isolated database, then writes a private tenant-focused
catalog/count/FK/media manifest. It never copies rows into an existing live
database. There is no `--apply`, no live target connection, no grants/role changes,
no migration and no worker/provider startup. Exit0 means preparation succeeded;
the receipt always has `apply_allowed=false` and
`full_tenant_restore_complete=false`.

## Owner-operated dry-run/isolated preparation

1. Decrypt the approved DB+recordings payload using
   [offhost.py restore-payload](database/OFFHOST.md), into a new private workspace.
   Keep backup/application/connector/TOTP encryption escrow separate. Do not
   point this tool at live media or modify the encrypted backup.
2. Provision a disposable PostgreSQL server and an existing restore-operator
   named service with `dbname=postgres`, protected passfile and verify-full TLS.
   The operator needs CREATE DATABASE, restore and complete read access on that
   isolated server; no role is created by this tool. Non-bypassed RLS filtering
   fails visibly because count queries set `row_security=off`.
3. Obtain approved source cluster ID, endpoint fingerprint and Alembic heads
   from the protected source/backup inventory. They are required operator inputs,
   not inferred from a hostname. Destination cluster/actual TCP endpoint are
   compared against them before database creation. The current logical payload
   manifest has file hashes, not a signed source-cluster identity; the owner must
   establish that provenance independently. A false operator assertion is not
   an isolation proof.
4. Use a new `talky_restore_*` database name and new receipt path. Example values
   below are placeholders, not approved production metadata:

```bash
umask 077
python3 scripts/restore_tenant.py \
  --service-file /secure/disposable-pg-service.conf --service isolated_restore \
  --payload /srv/disposable-recovery/payload \
  --database talky_restore_owner_drill \
  --tenant-id 00000000-0000-4000-8000-000000000001 \
  --source-cluster-id OWNER_RECORDED_NUMERIC_CLUSTER_ID \
  --source-endpoint-fingerprint OWNER_RECORDED_64_HEX_FINGERPRINT \
  --expected-head OWNER_APPROVED_ALEMBIC_HEAD \
  --source-recordings-root /opt/talky/backend/recordings \
  --receipt /srv/disposable-recovery/tenant-plan.json
```

`--source-recordings-root` maps historical absolute local recording paths into
the captured relative media tree. It must match the source's actual configured
directory. Traversal/outside-root paths are refused; local missing files and
external S3/Drive references are reported without printing paths or downloading
from providers. The default matches the standard installed layout only.

All payload hashes are checked before contacting PostgreSQL. The tool refuses
an existing database and calls pg_restore with one transaction, no ownership or
privilege replay. After restore it requires exact approved Alembic heads. It
retains the isolated database and a sanitized failure receipt for diagnosis;
it never drops databases or deletes the recovery workspace automatically.

## What the manifest proves

Catalog discovery enumerates user table/partition metadata through pg_catalog:
schemas, columns/types, PK/FK pairs, trigger names and RLS flags. Each discovered
table receives total and candidate tenant/parent-scope counts. Unresolved tables,
cross-schema ambiguity, cycles, partitions and triggers remain explicit. Direct
tenant predicates and parent-bound nullable children do not rely on RLS alone.
The table/FK ordering is a dependency report, not permission to replay SQL.

Inventory limits are2000 tables and100,000 recording rows. Count queries are
read-only with30-second statement/5-second lock limits; inventory checks a
five-minute overall budget between queries. A final in-flight query can use its
remaining per-query limit. The isolated database must remain quiescent; separate
query receipts are not a shared transaction snapshot. Restoring and hashing
large artifacts have their own I/O/client duration, included in measured RTO.

`catalog_complete=true` means every discovered table is represented in the
manifest. It does **not** establish complete tenant ownership, role isolation,
media recovery or a safe merge. The full restored database retains all original
rows for review; no financial/identity/audit table is silently dropped to create
a smaller apparent success.

## Why live copy is not implemented

- `user_profiles` is a global identity with a unique email, credential and role
  state. `tenant_users` supports membership in multiple tenants. Replacing an
  old tenant's profile could rewind another tenant's identity. Owner review must
  specify identity reuse/remapping and current access checks.
- AuditLogger hashes a platform-wide interleaved event chain. Historical rows
  for only one tenant cannot be spliced into its current predecessor sequence.
  A reviewed evidence-import/linking policy is required; historical hashes must
  not be silently regenerated or chain state overwritten.
- Financial/inbound/invoice/refund/legal receipts are append-only/immutable.
  Current payments, entitlements, legal evidence and cross-tenant transfer
  references need reconciliation, not a bulk overwrite or disabled trigger.
- Missing cloud/Drive media, undeclared ownership links, FK cycles, destination
  conflicts, app-role/RLS proof, quiescence and erasure replay after restoring old
  data require explicit review before a live copy implementation.

These are concrete owner/database-review inputs. The requested full single-
tenant restore remains unfinished; this tool supplies safe preparation evidence
within the timebox. Record an actual isolated restore and timed drill before
accepting even the preparation operationally. See
[proposed owner-approved RPO/RTO](database/PITR.md).
