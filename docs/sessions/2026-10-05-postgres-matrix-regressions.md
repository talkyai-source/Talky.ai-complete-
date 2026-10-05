# PostgreSQL matrix regression repairs — 2026-10-05

Four failures from the full integration matrix were reproduced and corrected in tests. No application code, migration, schema, billing policy, or retention guard changed.

Source baseline: `e7b8a84c`. Test repair: `938783f72d08405b4c350a70ce689e18a4275d3d`. Isolated branch: `codex/postgres-matrix-20261005`.

## Findings and repairs

| Failure | Reproduced cause | Preserved or strengthened verification |
| --- | --- | --- |
| Bootstrap billing ledger append-only test | The service bypass correctly sees retained rows from other fixtures. The assertion assumed a globally empty ledger: this reproduction saw 525 rows rather than its own three. | Select the exact three generated IDs and assert tenant and amount for each. Keep tenant/service UPDATE and DELETE denial, owner-trigger rejection, and original value checks. Compare count and whole-row digest of all unrelated retained rows before and after. The fixture transaction rolls back; no old rows are deleted. |
| CRM CLI downgrade test | The newer 0058 guard refuses before 0048, so the old exact exception text is never reached by the CLI. | Invoke the original 0048 guard directly, then run the real current-head CLI rollback. Require a RuntimeError from a migration downgrade, unchanged Alembic marker, and unchanged original account/provider receipt identities. |
| Billing webhook CLI downgrade test | Its growing exception-message allowlist omitted the newer 0058 retention guard. | Invoke the original 0054 guard directly, then verify actual CLI refusal without a revision/message allowlist. Compare the full owned event, notification, and review rows before and after, plus the unchanged marker. |
| AG06 concurrent receipt test | A hardcoded 0058/0059 head list rejects 0061 before testing the actual executor. | Require the original 0058 prerequisite in the applied Alembic ancestry. Keep actual concurrent claim, lost-response lookup, replay, single synthetic effect, and sensitive-output assertions. |

## Verification

- Disposable PostgreSQL: loopback port 55434, `cp04_acceptance_test`, public head `0061_dnc_runtime_contract`. Exclusive test slot; no production database.
- Before changes: the exact four failures reproduced, **4 failed** in 61.00 seconds.
- After changes: all three affected integration modules, **27 passed, zero skips** in 80.27 seconds.
- Scoped Ruff `F` and `git diff --check`: passed.
- Existing fixture ownership and cleanup remain intact. Ledger fixture rows are transaction-local. Receipt fixtures remove only their generated mutable identities. No prior immutable audit rows or migration markers were removed or rewritten.

Logs and commands are in [artifacts/postgres-matrix](artifacts/postgres-matrix). Tests use synthetic local effects; they do not verify live provider behavior or production deployment. The retained-ledger digest comparison assumes the explicitly reserved, serial database test slot.
