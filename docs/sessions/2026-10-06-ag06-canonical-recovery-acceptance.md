# AG06 saved acknowledgement: canonical database acceptance

The missing canonical-schema check for the existing Gmail acknowledgement recovery passed. No production code or migration was changed. This closes the earlier limitation that migration 0062 had only been exercised against hand-built relevant-shape tables; it does not complete broader AG06 or qualify provider delivery.

## Result and source binding

- Final run: **10 passed, 1 warning, 23.73 seconds**, pytest exit 0.
- The tested checkout was `db99f652fa2f921d5c0f24f8d25c002f228f12b2` plus the new integration module. Source commit is recorded in the package manifest after review; committing is not a rerun.
- All **83 recorded source/dependency LF hashes** were equal before and after the final run and matched the reviewed working files. The package includes the actual bootstrap, every migration file, Alembic environment, the test, runner, reused read-only snapshot helper and relevant application dependencies.
- Ruff `F` checks passed for the new test and runner; `git diff --check` passed. No unrelated regression suite was repeated.

## What the fresh database established

The reviewed runner created one UUID-named disposable database and one restricted login role on the designated local PostgreSQL instance. It executed the unmodified `backend/database/complete_schema.sql`, stamped the consolidated `0008_tenant_voice_tuning` baseline, then applied the actual **0009 through 0062** migration chain. This is the supported consolidated bootstrap path, not a claim that migrations 0001–0008 were individually executed. The final fresh schema had **109 public tables** and head `0062_saved_acknowledgement`.

The application role had neither superuser nor BYPASSRLS nor membership in the resolution table owner. Its explicit fixture grants were public-table SELECT, schema USAGE, sequence USAGE/SELECT, and INSERT/UPDATE/DELETE on `assistant_actions` and `assistant_action_resolutions`. These grants qualify this test fixture, not production deployment permissions.

The ten parametrized cases exercised:

1. Actual EmailService and DurableActionExecutor receipt-save failure, for dashboard and confirmed voice actions, followed by the real authenticated Admin GET → advertised digest → three concurrent identical recovery POSTs → coherent GET → exact lost-response replay → normal executor replay without another provider effect (2 cases).
2. Current tenant/partner administrator denial despite an older platform-role JWT, hidden recovery details and foreign-tenant action denial (2 cases).
3. Audit-insert or action-status-update failure rolling back both changes (2 cases).
4. Session revocation, role change or source mutation during the action-row lock wait denying recovery (3 cases).
5. Canonical immutable audit/RLS behavior, no worker audit visibility, and the explicit resolved-action/tenant deletion restriction (1 case).

JWT decoding, current session/profile/membership reads, tenant middleware, role dependencies, principal rechecks, RLS, recovery transactions, and PostgresClient metadata discovery were real. The Admin-advertised digest matched the native UUID/datetime locked-row digest. No authentication dependency or database adapter was replaced. The one injected application infrastructure port supplied the fixture connection pool.

## Isolation and retained first failure

Both runs preserved the existing `cp04_acceptance_test` database: **108 public table counts/data hashes, six schema metadata groups and head 0061** were identical before/after. Every generated database and role was removed, with **zero remaining connections**. Cleanup used exact generated identifiers without FORCE, session termination or unrelated deletion.

Each run recorded **46 designated database transports** in the parent plus **2** in the migration child, zero prohibited attempts, and internal socketpair counts. Socket, asyncio transport and DNS guards admitted only `127.0.0.1:55434`, apart from the scoped standard-library internal socketpair allowance. These are measured guarded Python paths, not a packet capture.

The first run had **9 passed, 1 failed, 1 warning in 60.18 seconds**. The last case stopped before its retention assertions because `deepcopy(asyncpg.Record)` is unsupported. The only correction was to convert the record to a dictionary before copying/comparing it. Original test/runner snapshots, source hashes, logs, migration evidence and cleanup result are retained. This was a test-fixture correction, not an application defect or a claimed pre-fix product failure. The two runs are not additive coverage.

## Limits and evidence

Provider sending and credential/current-authorization acquisition were synthetic ports; no live Gmail call occurred. The result proves recovery of original recorded acceptance, never delivery or payload correctness. HTTP used ASGI transport, not a deployed server or browser. The existing recovery retention restriction was tested, not approved as a privacy/retention policy. No customer database, deployment, Git push or new feature was involved.

The runner pins the selected Python executable, this worktree's backend-only PYTHONPATH, `PYTHONDONTWRITEBYTECODE=1`, fresh owner/application DSNs and a process-only synthetic JWT secret. No token or secret is recorded. Nine installed dependency locations and versions are retained in each result; this is venv provenance, **not exact-requirements parity**.

Evidence package: [manifest](artifacts/ag06-canonical-recovery/manifest.json), [commands](artifacts/ag06-canonical-recovery/commands.json), [final result](artifacts/ag06-canonical-recovery/final-result.json), [final log](artifacts/ag06-canonical-recovery/final.txt), [first result](artifacts/ag06-canonical-recovery/first-result.json), and [test source](../../backend/tests/integration/test_saved_acknowledgement_canonical.py). Root and an independent agent reviewed the harness before execution; the independent final review is recorded in the manifest. The reviewer did not rerun tests or access the database.
