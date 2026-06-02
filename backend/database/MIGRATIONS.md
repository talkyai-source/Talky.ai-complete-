# Database migrations — the one process

**Alembic is the single source of truth for schema changes.** As of
2026-06-02 the project had drifted into two parallel systems — Alembic
(`Alembic/versions/`) *and* hand-applied raw SQL (`database/migrations/`).
The raw-SQL system is now **frozen and archived** (see
`database/migrations/_archive/`). Do not add or run files there.

## Standing up a fresh database

Alembic's `0001_baseline` only *stamps* an existing schema as applied —
it does not create tables. So a brand-new database is built in two steps:

```bash
# 1. Load the full schema snapshot (dumped from prod 2026-06-02).
psql "$DATABASE_URL" -f database/schema/baseline_2026-06-02.sql

# 2. Tell Alembic this schema is at head, then apply anything newer.
alembic stamp head      # or: alembic upgrade head  (idempotent revisions, safe either way)
```

`database/schema/baseline_2026-06-02.sql` is a `pg_dump --schema-only`
of production and is the authoritative starting point. Regenerate it
periodically (and bump the date in the filename) so fresh installs stay
close to prod; each regeneration should line up with `alembic stamp head`.

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

## Production state (2026-06-02)

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
