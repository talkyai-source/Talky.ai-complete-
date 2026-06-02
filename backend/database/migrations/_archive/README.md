# Archived raw SQL migrations — FROZEN, do not run

These 28 hand-applied SQL files predate the consolidation onto Alembic
(2026-06-02). They were applied to production ad-hoc via `psql` and were
tracked by nothing.

Everything they changed is already captured in
`../../schema/baseline_2026-06-02.sql` (a `pg_dump` of the live prod
schema). Re-running any of them would at best be a no-op and at worst
conflict with the current schema.

**The single migration process is now Alembic.** See
`../../MIGRATIONS.md`. Add new schema changes as Alembic revisions in
`backend/Alembic/versions/` — never here.
