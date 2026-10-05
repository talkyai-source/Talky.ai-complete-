# OP05 scheduling holds and active-job ownership

This bounded repair was tested on isolated branch `codex/production-ready-op05-20261005`, based on `09cfad067a3999098766987dfa2378fd415384fd`. Worker source is committed as `c6d2c7ed`; the accompanying migration and evidence commit completes this candidate. No production database, provider account, customer call, or external message was used.

## Verified problems and repair

The actual worker and Redis queue implementation previously treated rejected scheduling rules as new attempts: outside-window/day, daily-cap and concurrency holds called `schedule_retry`, incremented the attempt, then saved a terminal `skipped` database status. Eight of nine new controls failed before the repair; the genuine post-attempt retry control already passed. The shared rejection branch now uses the existing original-payload deferral helper with the existing rule-selected delay. Genuine cooldown holds reaching this branch receive the same behavior. Acknowledged handoff saves `retry_scheduled`, keeping active database ownership, and leaves the attempt unchanged.

Two additional fault controls showed that a bookkeeping database error after a successful queue handoff entered the generic attempted-call retry path, producing two scheduled payloads and attempt N+1. The helper now logs the failed projection without creating a new retry. Cancellation is not swallowed. A lagging database row remains active; it is not falsely reported as updated. Resume controls confirm the original attempt reaches the synthetic provider boundary once.

Actual migrated PostgreSQL exposed a second problem: the one-active-job index existed only in historical standalone SQL, outside the complete-schema/Alembic path. `0060_dialer_active_job_owner` (after 0059) now installs the existing active predicate: pending, queued, retry_scheduled, processing, calling. The matching bootstrap block has the same contract. A transaction-scoped writer lock closes the preflight/build race. Existing duplicate jobs cause an actionable migration failure without modifying any job. Existing named invalid, wrong-key, nonunique or unsupported-predicate indexes fail explicitly; valid stronger guards are retained. Downgrade retains the safety index. The historical SQL is marked retired because it automatically cancels siblings and was not executed.

## Verification

All commands ran from the isolated `backend` directory with the original repository Python virtual environment. `PYTHONDONTWRITEBYTECODE=1`; the existing test-only Lua dependency directory was supplied through `PYTHONPATH` (`../production-ready-ag07-20261005/tmp/op02-testdeps` relative to the worktree parent). No dependency installation or package mutation was performed.

- **122 unit tests passed**, across the ten files listed below, with 190 existing datetime deprecation warnings.
- **14 PostgreSQL tests passed**, zero skips, on the explicitly supplied loopback `cp04_acceptance_test` after canonical 0059→0060 migration. Unique NOBYPASSRLS fixture roles exercised the actual worker's authorized database updates; role/fixture cleanup completed. No existing role privileges changed.
- A new exclusively owned database ran actual `complete_schema.sql` → Alembic stamp0008 → head0060 → retaining downgrade0059 → head0060, then the same **14 PostgreSQL controls passed**. The fresh database was removed afterward. These are repeated controls, not 28 distinct tests.
- Scoped Ruff F gate passed, excluding the repository's existing F401/F841 exclusions. `git diff --check` passed.
- Parent independently reviewed the DDL and post-handoff exception boundary. The LLM audit agent independently reviewed the worker/queue change without finding a new material issue; it did not execute the tests or review the migration.

```text
python -m pytest tests/unit/test_op05_pre_attempt_holds.py tests/unit/test_dialer_worker_block_visibility.py tests/unit/test_dialer_origination_durability.py tests/unit/test_dialer_redis_reliability.py tests/unit/test_op01_metering_unavailable.py tests/unit/test_dialer_lead_cooldown_gate.py tests/unit/test_lead_timezone_precedence.py tests/unit/test_dialer_worker_guard_ordering.py tests/unit/test_campaign_schedule.py tests/unit/test_queue_retry_idempotency.py -q --tb=short
python -m alembic upgrade head
python -m pytest tests/integration/test_op05_pre_attempt_holds.py tests/integration/test_op05_active_job_migration.py -q --tb=short
python ../docs/sessions/artifacts/op05/verify_fresh_bootstrap.py
python -m ruff check app/domain/services/queue_service.py app/domain/services/dialer/job_states.py app/workers/dialer_worker.py Alembic/versions/0060_dialer_active_job_owner.py tests/unit/test_op05_pre_attempt_holds.py tests/integration/test_op05_pre_attempt_holds.py tests/integration/test_op05_active_job_migration.py tests/unit/test_dialer_worker_block_visibility.py tests/unit/test_dialer_origination_durability.py tests/unit/test_dialer_lead_cooldown_gate.py tests/unit/test_lead_timezone_precedence.py ../docs/sessions/artifacts/op05/verify_fresh_bootstrap.py --select F --ignore F401,F841
```

Evidence is in [artifacts/op05](artifacts/op05): `holds-red.txt`, `holds-bookkeeping-red.txt`, `holds-regression.txt`, `holds-postgres.txt`, `migration-postgres.txt`, `migration-public-upgrade.txt`, `fresh-bootstrap.txt`, and `holds-ruff.txt`. Initial failures are retained separately: unavailable Lua/EVAL in `holds-first-green.txt`; fixture campaign-parent lock grants in `holds-postgres-fixture-*.txt`; real absent-index failure in `holds-postgres-missing-index-red.txt`. The fixture grant issue was corrected on newly created roles only, without relaxing application policy.

## Scope and rollout limits

This proves application behavior with actual queue code over FakeRedis and actual PostgreSQL, not a real Redis process restart, real PSTN behavior, production migration compatibility, or complete OP05 qualification. Synthetic provider calls are spies only. Existing scheduling windows, daily-cap reset, cooldown delays, paid allowance policy and post-attempt retry policy were not changed. Redis publication then inflight cleanup remains the existing staged handoff; lost publication acknowledgement retains the original inflight evidence.

The stale-cooldown special workaround (clear stale timestamp, increment and enqueue before the shared rejection branch), batch pacing, and other pre-admission paths are not included in this worker repair; the stale workaround is under a separate read-only correctness check. Native DNC lifecycle persistence remains a separate OP05/OP07 handoff. No full OP05 completion or release approval follows from these results.

Deploy the canonical migration through the normal reviewed process before relying on database uniqueness. A duplicate or incompatible-index failure requires inspection of the existing call/queue evidence; do not execute the historical cancelling script. Code rollback may revert the worker delta while retaining 0060's index; do not drop the invariant as an automatic rollback step.
