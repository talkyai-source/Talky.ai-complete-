# Database migrations — the one process

**Alembic is the single source of truth for schema changes.** As of
2026-06-02 the project had drifted into two parallel systems — Alembic
(`Alembic/versions/`) *and* hand-applied raw SQL (`database/migrations/`).
The raw-SQL system is now **frozen and archived** (see
`database/migrations/_archive/`). Do not add or run files there.

## Standing up a fresh database

Alembic's `0001_baseline` does not build the historical application schema.
A brand-new database must first load one of the repository's schema snapshots,
then be stamped at its **oldest fully represented and proven floor**, and only
then upgraded. The maintained hand-consolidated schema contains some later
idempotent objects, but that does not justify skipping intervening migrations.
Never stamp an old snapshot at `head`: doing so skips every newer migration
while falsely reporting a current database.

The maintained and CI-tested bootstrap path is:

```bash
# 1. Load the maintained historical schema floor.
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/complete_schema.sql

# 2. Record the conservative proven floor, then execute every newer migration.
alembic stamp 0008_tenant_voice_tuning
alembic upgrade head
```

The preserved 2026-06-02 production snapshot is also supported for recovery at
the same conservative `0008_tenant_voice_tuning` boundary:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/schema/baseline_2026-06-02.sql
alembic stamp 0008_tenant_voice_tuning
alembic upgrade head
```

The preserved snapshot was emitted by `pg_dump 16.14` and contains its paired
`\restrict`/`\unrestrict` client guards. Restore the exact file with `psql
16.14` or newer; do not strip those guards in release or disaster-recovery
workflows. CI pins a `postgres:16.14-alpine` client for this restore rather
than relying on the runner's unversioned `psql`.

`database/schema/baseline_2026-06-02.sql` is a historical `pg_dump
--schema-only`; `database/complete_schema.sql` is the current bootstrap used by
CI. Both deliberately use the conservative 0008 floor: a 2026-08-28 audit
proved that `complete_schema.sql` did not contain every 0009-0021 object even
though it had previously been stamped at 0021. Migration 0033 repairs databases
already stamped past that gap. New databases must run the full 0009-to-head
chain and must never restore the old 0021 stamp shortcut.

The current sole head is `0035_user_profiles_role_widen`. It widens the
`user_profiles.role` constraint to the complete application `UserRole` set,
discovers either historical constraint name from the PostgreSQL catalog, and
refuses upgrade or downgrade when existing rows would be rejected by the
target constraint. Its predecessor, `0034_inbound_billing_four_eye`, adds the
durable pending/approved record required for two distinct platform
administrators to finalize a manual inbound billing hold and forward-repairs
pre-existing `0033` databases so `billing_ledger` is append-only under tenant,
service-bypass, and owner contexts. Downgrade from `0034` refuses while
approval evidence exists and never removes the append-only ledger boundary.

When a snapshot is regenerated, record and test its proven floor here. Reserve
an **exact revision boundary** for a generated dump whose schema has been
catalog-compared with that revision. `alembic stamp head` is valid only for a
dump verified to have been generated from that exact head revision.

## Making a schema change

```bash
alembic revision -m "short description"   # creates Alembic/versions/NNNN_short_description.py
# edit upgrade()/downgrade() — prefer idempotent DDL:
#   ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / DROP ... IF EXISTS
alembic upgrade head                      # apply locally
```

Then deploy and run `alembic upgrade head` on the target. Writing the
DDL idempotently means re-running a revision (or running one that was
already applied by hand) is a safe no-op — this is how the repo recovers
from the historical drift.

## Historical production state (2026-06-02)

* Prod `alembic_version` = `0008_tenant_voice_tuning`.
* `0009_dialer_jobs_failure_classification` reintroduces, as a tracked
  revision, the `dialer_jobs.failure_category` / `failure_reason` columns
  that were originally applied to prod via a raw SQL file. The columns
  already exist on prod, so the revision is a no-op there — the next
  `alembic upgrade head` simply advances the version marker to 0009.

## Why the raw-SQL files were archived, not deleted

`database/migrations/_archive/` keeps the 28 historical raw files for
forensic reference. Everything they changed is already reflected in
`baseline_2026-06-02.sql`, so they must never be re-run.
