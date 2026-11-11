# Database migrations — the one process

**Alembic is the single source of truth for schema changes.** As of
2026-06-02 the project had drifted into two parallel systems — Alembic
(`Alembic/versions/`) *and* hand-applied raw SQL (`database/migrations/`).
The raw-SQL system is **superseded**. Both the archived files and the later
June–July files are retained only for provenance. See the exact disposition in
[`migrations/README.md`](migrations/README.md); do not add or run manual files.

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
psql -X -v ON_ERROR_STOP=1 -f database/complete_schema.sql

# 2. Record the conservative proven floor, then execute every newer migration.
alembic stamp 0008_tenant_voice_tuning
alembic upgrade head
```

The preserved 2026-06-02 production snapshot is also supported for recovery at
the same conservative `0008_tenant_voice_tuning` boundary:

```bash
psql -X -v ON_ERROR_STOP=1 -f database/schema/baseline_2026-06-02.sql
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

`0087_bootstrap_manual_parity` comes after `0086_call_workflows` and
`0085_legal_acceptance`. It fills the documented bootstrap/manual gaps, using
the current post-0065/0067 table contracts. It adopts compatible existing
objects without replacing them, and refuses incompatible columns, defaults,
constraints, indexes or tenant security.

`0092_drop_broken_touch_triggers` comes after 0088-0091. On the production
lineage it drops the touch triggers that set `updated_at` on
`assistant_actions` and `reminders`, which have no such column, so every
UPDATE of either table failed. Fresh installs have no such trigger, so it
changes nothing there.

`0093_abuse_event_columns` adds, on the production lineage, the ten
`abuse_events` columns the abuse code inserts and resolves with, so abuse
events can be recorded at all; fresh installs already have them.

The current sole head is `0094_harden_legacy_db_objects`. It drops the broken
SECURITY DEFINER `log_audit_event(...)` (unpinned search_path, nothing calls
it) and makes the owner-rights views `active_suspensions`, `expiring_secrets`
and `open_security_events` run with the reader's rights (`security_invoker`),
so the row-level security of the tables beneath them applies.

A fresh install and the production lineage still differ elsewhere (QA F14-02,
see the CI schema-parity step).

For the `psql` commands above, privately configure libpq's `PGHOST`, `PGPORT`,
`PGUSER`, `PGDATABASE` and authentication environment/password file. Configure
Alembic's `MIGRATION_DATABASE_URL` separately in the process environment. Never
paste a password-bearing DSN into a command argument or share its value.

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
DDL idempotently avoids recreating compatible objects. `IF NOT EXISTS` alone
does not prove an existing object has the correct shape: use guarded metadata
checks and behavioral PostgreSQL tests for adopted manual objects.

## 0087 preflight, application and rollback

This is additive schema repair, not permission to rewrite unknown production
objects. Before applying, rehearse both bootstrap routes on disposable
PostgreSQL and retain the schema diff described below. An authorized owner
must take the normal database backup and use a maintenance window: 0087 uses
transactional locks (5-second lock timeout; 120-second statement timeout),
including ACCESS EXCLUSIVE metadata validation. It cannot use CONCURRENTLY in
the repository's single migration transaction.

Run the count-only preflight with the authorized maintenance connection. It
prints no row values and makes no writes. `row_security=off` deliberately
refuses a role whose observation would be filtered. Every reported rejection
or duplicate count must be zero; unexpected schema/permission errors require
review, not a bypass or automatic data cleanup.

```bash
psql -X -v ON_ERROR_STOP=1 -f scripts/check_manual_parity_constraints.sql
alembic current
alembic upgrade head
alembic current
alembic heads
```

The refresh-reason and tenant-provider CHECKs are added NOT VALID when absent
or known-old, then validated only after zero-invalid-row checks. An already
current refresh CHECK retains its identity and validation state. Existing
credential history, worker counters, event rows, index identities, policies
and compatible table definitions are retained. The migration never replays the
old job-cancellation UPDATE. Unknown definitions cause the whole transaction
to roll back; do not widen the guard without a separate reviewed repair.

0087 is **forward-only**: it may adopt objects that existed before Alembic, and
the database contains no reliable provenance proving which objects a downgrade
could drop. `alembic downgrade 0086_call_workflows` therefore refuses before
issuing DDL. Keep this additive schema for an approved compatible application
rollback. Any schema compensation requires a separately reviewed migration;
do not drop tables or restore a database backup over live writes merely to
reverse a code release.

## Two-bootstrap schema parity gate

`scripts/verify_schema_parity.py` creates two unique disposable databases,
restores each supported snapshot unchanged, stamps 0008, upgrades each to
head, then compares `pg_dump --schema-only --no-owner` from the same client.
It normalizes only dump client/version comments and random paired restrict
tokens in the **comparison output**; the original restore guards remain intact.
Columns, defaults, functions and their literal whitespace, constraints, indexes,
comments, grants, policies and FORCE RLS differences remain visible. The owner
name is the sole explicit metadata exclusion; there is no structural allowlist.

Privately set `SCHEMA_PARITY_ADMIN_DSN` (or `TEST_DATABASE_URL`) to a loopback,
test-named PostgreSQL provisioning database with CREATE DATABASE permission.
Routing query overrides, remote hosts and production-named databases are
refused before a subprocess starts. The environment must already contain the
snapshot's required `authenticated` role. Use psql 16.14+ for its guarded
snapshot and the same compatible pg_dump executable for both routes.

```bash
python scripts/verify_schema_parity.py --output-dir tmp/schema-parity
pytest tests/integration/test_bootstrap_manual_parity.py -q
```

Exit 0 means the two dumped schemas matched, 1 means the retained `schema.diff`
is nonempty, and 2 means provisioning, migration, dump or cleanup failed. Every
subprocess is bounded at 300 seconds and does not receive credentials in argv.
`invocation.json` lists only generated database names; raw schema dumps,
`schema.diff` and `result.json` are retained on successful execution. Cleanup
drops only names successfully created by that invocation, without FORCE or
terminating other sessions. If cleanup fails, the owner can inspect the named
disposable databases and remove them after verifying their identity.

CI has PostgreSQL and runs this as a blocking gate, using the pinned 16.14
client container already used for the preserved snapshot, and uploads the
artifact directory even on failure. Unit subprocess doubles verify orchestration
only. The four opt-in PostgreSQL tests verify the late repair, idempotence,
constraint preservation and atomic refusal; they do not replace the full dump
comparison. Until the actual PostgreSQL run is recorded, report **not run on
PostgreSQL**, not schema parity or a production no-op proven by unit tests.

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
