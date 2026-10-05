# Full-suite branding and static RLS regression repairs

The full backend run at `1b33f6eb` reported four failures in `test_company_name_fallback.py` and one in `test_rls_policy_backfill_migration.py`. All five reproduced locally (**5 failed, 12 passed**) in the isolated `codex/backend-fixtures-20261005` worktree. Source/test correction: `a2b25257`.

The branding tests used the obsolete fixture engine `flux`. The actual configuration contract requires `deepgram_flux`; an omitted language on the MagicMock would also manufacture an arbitrary language. The fixture now explicitly supplies `deepgram_flux` and `en`. All original neutral-brand, configured-tenant-brand and warning-count/no-phone assertions remain. No prompt, company identity, production configuration or runtime validation changed.

The static inventory failure exposed a real audit-tool defect. Its ENABLE-RLS regex accepted arbitrary text between the table identifier and ENABLE. Migration 0055 contains separate Python SQL literals without trailing SQL semicolons: an `ALTER TABLE invoices ADD COLUMN ...` followed eventually by `ALTER TABLE invoice_snapshots ENABLE ROW LEVEL SECURITY`. The regex crossed those statements and attributed the snapshot protection to `invoices`. This could claim protection without its actual migration source.

The regex now requires the existing directly supported ALTER-table ENABLE syntax. A minimal adversarial two-table fixture failed before the change while six supported plain/quoted/public-qualified/ONLY identifier controls passed. After repair, only the genuinely enabled table is discovered. The original expectation that invoices depends on migration 0038 remains unchanged. No runtime RLS policy changed; the inventory remains a static, best-effort audit rather than proof of a deployed database's policy.

Verification used the original backend virtual environment, `PYTHONDONTWRITEBYTECODE=1`, `ENVIRONMENT=test`, and an intentionally unavailable loopback database (`127.0.0.1:1`). No PostgreSQL opt-in, provider calls, credential changes or dependency installations were used.

```text
python -m pytest tests/unit/test_company_name_fallback.py tests/unit/test_rls_policy_backfill_migration.py -q --tb=short
python -m pytest tests/unit/test_rls_policy_backfill_migration.py -k "unrelated_python_alter or supported_enable" -q --tb=short
python -m pytest tests/unit/test_company_name_fallback.py tests/unit/test_rls_policy_backfill_migration.py tests/unit/test_telephony_session_config.py -q --tb=short
python -m pytest tests/unit/test_rls_inventory_helper_arguments.py tests/unit/test_rls_set_local_invariant.py -q --tb=short
python -m ruff check scripts/rls_acquire_inventory.py tests/unit/test_company_name_fallback.py tests/unit/test_rls_policy_backfill_migration.py --select F --ignore F401,F841
```

Final results: **92 passed** in the branding/session/inventory group; **1,202 passed** in the related inventory-helper and static tenant-context controls. These are 1,294 checks across five distinct modules, not the whole backend suite. Scoped Ruff and Git whitespace checks passed. Initial failures and final outputs are retained under [backend-fixtures evidence](artifacts/backend-fixtures). Parent owns the final integrated rerun and the other full-suite failures.
