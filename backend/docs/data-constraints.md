# F2-04 data constraints and foreign-key indexes

Revision `0088_qa_data_constraints` follows `0087_bootstrap_manual_parity`.
It adds seven checks as **NOT VALID** and creates missing supporting indexes
for declared public-table foreign keys. It does not clean, delete or validate
historical data. No production data or lock timings have been measured here.

| Table | Column | Constraint |
| --- | --- | --- |
| plans | minutes | `q31_plans_minutes_nonnegative`: >= 0 |
| tenants | minutes_allocated | `q31_tenants_minutes_allocated_nonnegative`: >= 0 |
| tenants | minutes_used | `q31_tenants_minutes_used_nonnegative`: >= 0 |
| tenant_call_limits | monthly_minutes_allocated | `q31_limits_minutes_allocated_nonnegative`: >= 0 |
| tenant_call_limits | monthly_minutes_used | `q31_limits_minutes_used_nonnegative`: >= 0 |
| campaigns | status | `q31_campaigns_status_valid`: draft, scheduled, running, paused, completed, cancelled, stopped, deleted |
| leads | status | `q31_leads_status_valid`: pending, calling, called, contacted, completed, failed, dnc, deleted, skipped |

Zero-minute plans/allocations remain valid. Current quota code rejects negative
allocations; its older unlimited-sentinel documentation does not justify new
negative values. Signed `billing_ledger.minutes_delta`, negative refund amounts
and computed balances are deliberately excluded. Their negative values describe
real refunds, adjustments and consumption. Existing nullable lead status stays
nullable. Status values include actual stop/delete writers and the legacy
`skipped` value that campaign restart consumes. This is not a blanket constraint
on every numeric or status column. E.164 cleaning/policy remains item1's separate
repair boundary; no phone-number constraint is introduced here.

`NOT VALID` keeps old violations present, but PostgreSQL enforces the check on
new inserts **and updates**, including updates to a legacy bad row. Run the
count before deploying and plan repairs for any affected workflows. A changed
row is not exempt merely because its violation predates the migration.

## Read-only count before and after deployment

From `backend/`, with the operator's existing migration/read authority supplied
through process environment (do not place privileged credentials in runtime.env):

```sh
python scripts/check_data_constraints.py --all-tenants
python scripts/check_data_constraints.py --tenant-id <tenant-uuid>
```

The default variable is `MIGRATION_DATABASE_URL`; `--dsn-env OWNER_DATABASE_URL`
selects a different existing variable. The script never reads dotenv files or
prints the DSN. There is no apply mode. A read-only repeatable-read transaction
counts each tenant using explicit predicates and pages the platform tenant-ID
directory in batches of 500. Global plan counts and schema-wide missing FK-index
metadata are included once even for a single-tenant request. No row samples or
customer values appear in the report. Zero counts do not prove other data rules.

`SET LOCAL row_security=off` does not grant access: it makes PostgreSQL refuse a
query that would otherwise silently filter rows. Use owner-provisioned authority
with visibility over the requested scope. Missing tables/permissions, RLS
filtering and timeouts fail the command without a partial success report. The
snapshot is as current as the selected database; use the primary for deployment
decisions. Connection and lock waits are bounded at 5 seconds; each statement
has a 60-second deadline. An all-tenant scan may span many statements.

An exit0 means counting completed, **not** that every violation count is zero.
Review the `constraints[].violations` and `missing_fk_indexes` arrays. A nonzero
exit means the count is incomplete. Preserve the count output with the deployment
record. Have the data owner approve repairs; then rerun the count. This script
does not issue `VALIDATE CONSTRAINT`. A later reviewed validation can scan the
historical rows after repairs; do not infer validation from migration success.

## Index and migration behavior

Each declared FK can require a referencing-table lookup during parent deletes
or referenced-key changes. The migration checks `pg_constraint` and `pg_index`,
including composite keys. A valid, ready, live B-tree must contain the complete
FK column set in its leading key positions. A permutation of those equality
keys is sufficient; nonleading keys, INCLUDE-only columns, partial indexes and
expression indexes do not establish complete coverage. Compatible existing
indexes are retained without a duplicate. The historical QA count of153 missing
indexes is not assumed to describe the current schema; the count script reports
the actual catalog gaps.

New indexes have deterministic `q31_fk_...` names. A conflicting existing name
or CHECK expression aborts rather than replacing the object. PostgreSQL parses
the expected CHECK against a temporary LIKE table so expression compatibility
is checked using the actual column types. Newly created objects receive an
`0088` ownership comment; adopted objects keep their existing metadata.

Use a maintenance window. Ordinary `CREATE INDEX` and the migration's
SHARE ROW EXCLUSIVE index-DDL lock allow ordinary reads but block writes.
CHECK installation requires ACCESS EXCLUSIVE on the five target tables, which
also blocks reads there. **Locks persist until the entire Alembic transaction
commits**, not just until each statement finishes. For tables around50k rows,
expect seconds per ordinary index under light load, but the total depends on
the number/width of missing indexes, disk and concurrent workload; this is an
estimate, not a measured guarantee. Record actual restored-snapshot and owner
timing. Do not advertise a zero-downtime or subsecond migration.

Lock acquisition is limited to5 seconds and each migration statement to120
seconds. A timeout rolls back the migration transaction; retry in a suitable
window after inspecting the blocker. The CREATE statements are not concurrent
index builds and do not run outside the transaction. Do not increase limits or
remove guards simply to force a deployment through.

## Rollback and verification

From0088, `alembic downgrade 0087_bootstrap_manual_parity` removes only CHECKs and
indexes carrying this revision's ownership marker. It preserves adopted objects,
data and signed financial history. No CASCADE drop is used; a later dependency
can correctly refuse rollback. Do not downgrade past0087, whose separate
forward-only policy remains in force. Upgrade can be repeated after rollback.

The unit tests verify the count/CLI contract with fake database ports; SQL text
guards alone do not prove PostgreSQL behavior. The optional
`tests/integration/test_qa_data_constraints_pg.py` uses only an explicitly
supplied loopback test-named `TEST_DATABASE_URL` and creates/removes its own UUID
database. It exercises legacy violations, new-write rejection, zero and valid
statuses, signed ledger controls, prefix-index variants, name conflicts,
compatible-object adoption and upgrade/downgrade/reupgrade. It never validates a
production snapshot. Root retains actual test output separately; owner cleanup,
historical validation and production lock duration remain unverified.

Primary PostgreSQL16 references:

- [ALTER TABLE](https://www.postgresql.org/docs/16/sql-altertable.html): NOT VALID and constraint lock behavior.
- [pg_index](https://www.postgresql.org/docs/16/catalog-pg-index.html): key versus included attributes and index validity.
- [Multicolumn indexes](https://www.postgresql.org/docs/16/indexes-multicolumn.html): leading equality-key coverage.
- [Row security configuration](https://www.postgresql.org/docs/16/runtime-config-client.html#GUC-ROW-SECURITY): refusing results that would be filtered.
