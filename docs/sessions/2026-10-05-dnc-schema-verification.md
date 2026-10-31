# DNC schema contract repair — 0061

The native opt-out persistence acceptance tests reached the real migrated database and failed: the first of two `CREATE TABLE IF NOT EXISTS dnc_entries` definitions won during bootstrap. It required `phone_number`, accepted only six old source values, omitted runtime `added_by`/`updated_at`, and enforced source-blind tenant/number uniqueness. The later definition had the runtime columns but lacked the source-specific uniqueness assumed by `DNCService.add`. Neither shape implemented the complete existing service contract.

The isolated branch `codex/production-ready-dnc-schema-20261005`, based on `d26f0ff0`, changes only schema, migration tests and evidence. Schema/tests are committed as `444cbf73`. The DNC service/native lifecycle repair is owned and verified separately. No production database, provider account, customer call or external message was used.

## Retained contract

`0061_dnc_runtime_contract`, after `0060_dialer_active_job_owner`, holds a bounded table lock while inspecting and aligning the schema. It adds compatible phone/audit columns, retains all existing rows and attribution, and preserves foreign keys and RLS policies. A missing compatibility phone value is filled from its existing normalized value; existing display values are unchanged. An added timestamp records migration time; an existing nullable timestamp retains unknown values. Existing incompatible column types fail with an actionable message.

Source remains free text, as the existing API and service already permit custom values. Only the exact obsolete six-value CHECK is removed. Only the verified historical source-blind unique constraint is replaced. Tenant rows are unique by tenant/number/source; global rows are unique by number/source. Separate sources remain independent evidence. Conflicting rows, unknown source constraints, unrecognized unique restrictions and invalid or incorrectly shaped named indexes stop the migration without merging or deleting evidence. The same source-specific guards remain on downgrade. Canonical bootstrap now has one DNC table definition and the same guarded alignment block.

Existing nullable timestamp/legacy `created_by` data is retained rather than relabeled as verified modern attribution. This migration does not infer the meaning or validity of historical source values or caller consent. It does not reconcile duplicate records automatically.

## Verification

- **18 distinct actual PostgreSQL cases passed** across both historical shapes and the identical bootstrap alignment block: original evidence/expiry/actor preservation; tenant/global source keys; concurrent writes; custom source coexistence; inherited RLS policy and FK retention; duplicate failure with no row/column changes; unknown constraint/index rejection; invalid concurrent-index rejection; column type mismatch; nullable historical timestamp; repeat upgrade and retaining downgrade.
- A separately generated, exclusively owned database ran the complete canonical schema, stamp `0008`, migration through `0061`, retaining downgrade to `0060`, then `0061` again. All 18 cases passed again. This is repeated environment evidence, not 18 additional distinct cases. The generated database was removed afterward.
- After parent review, the exact migration upgraded only loopback `cp04_acceptance_test` from `0060` to `0061`. Both source-specific keys were present and RLS policy/flags unchanged. That database had zero DNC rows at upgrade; populated-row preservation is established by the isolated cases above. No unknown constraint, duplicate record or data value was manually adjusted to make the upgrade pass.
- Scoped Ruff F and Git whitespace checks passed. The initial isolated run was **14 passed, 4 failed**: unconditional `ALTER ... TYPE text` rebuilt indexes on repeated upgrade. The migration now changes type only when necessary; the initial result is retained.

Commands used the original repository's `backend/.venv/Scripts/python.exe`, from this isolated worktree. `TEST_DATABASE_URL` was explicitly restricted to the disposable localhost test database; `PYTHONDONTWRITEBYTECODE=1`. No dependency installation was needed.

```text
python -m pytest tests/integration/test_dnc_schema_migration.py -q --tb=short
python docs/sessions/artifacts/op07-dnc-schema/verify_fresh_bootstrap.py
python docs/sessions/artifacts/op07-dnc-schema/verify_public_upgrade.py
python -m ruff check Alembic/versions/0061_dnc_runtime_contract.py tests/integration/test_dnc_schema_migration.py ../docs/sessions/artifacts/op07-dnc-schema/verify_fresh_bootstrap.py ../docs/sessions/artifacts/op07-dnc-schema/verify_public_upgrade.py --select F --ignore F401,F841
```

The pytest/Ruff commands run in `backend`; the two verification helpers run from the worktree root. See [schema evidence](artifacts/op07-dnc-schema): `isolated-schema-first.txt`, `isolated-schema-final.txt`, `fresh-bootstrap.txt`, `public-upgrade.txt`, and `ruff.txt`. Runtime opt-out tests belong to the separate OP07 service report; this schema evidence alone does not prove provider, speech, process-restart or production-release behavior.

## Deployment and rollback boundary

Deploy the canonical migration through the normal reviewed path before relying on the aligned runtime writes. Unknown constraints or duplicate source records require evidence review; do not delete/merge records or execute a source-blind cleanup to bypass the failure. Lock/statement timeouts are bounded at 5/60 seconds in the migration. Rollback retains the aligned schema and keys; restoring source-blind uniqueness could destroy independent opt-out evidence and is deliberately not automated.
