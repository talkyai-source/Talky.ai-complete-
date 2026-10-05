# OP05 pacing holds — 5 October 2026

Source `9b69d34433edd1cf4a2e456fc864126e8d7495ae`, isolated branch `codex/production-ready-op05-pacing-20261005`, base `1b33f6eb87abc73a539e461d8b1efe9fbed77da4`.

Five remaining pre-origination branches used `schedule_retry`, which increments the attempt: batch capacity, campaign call gap, tenant call gap, CallGuard throttling and CallGuard queueing. Actual worker/queue reproduction showed attempt 1 becoming attempt 2 without creating a call intent or entering the provider. A database failure after publication entered the generic terminal failure path. All ten new controls failed before the repair; [initial.txt](artifacts/op05-pacing/initial.txt) retains that evidence.

These branches now reuse the existing durable same-attempt hold helper. It persists the guarded active owner and exact due time before publishing the original Redis payload. Reasons and delays stay unchanged: five seconds for batch capacity, the existing calculated campaign/tenant gaps, 60 seconds for throttling and 30 seconds for queueing. CallGuard refusal still releases its already-claimed pacing slot. Genuine post-intent/provider retry branches remain unchanged; their semantics are not reclassified by this repair.

Validation:

- **150 unit checks passed** across 12 modules, with 210 existing datetime warnings, using the actual queue implementation over FakeRedis. Existing positive call admission, guard ordering, genuine retry and original-payload recovery controls remain included.
- **10 actual PostgreSQL checks passed**, zero skips, with 20 existing warnings, on disposable public migration head 0061. For each of the five branches, they prove the exact database/Redis due time and original attempt, plus real UPDATE-denial retention followed by successful permission-restored retry. Unique NOBYPASSRLS fixture roles and only their synthetic rows were cleaned. No provider effect occurred.
- Final scoped CI Ruff F rules and whitespace checks passed. The first lint invocation found a fixture-import shadowing warning; its log is retained in `ruff.txt`. The fixture now uses its module export, preserving the actual fixture behavior. Independent read review found no material defect; it did not run the tests.

The first regression invocation named a nonexistent test module and ran no tests; `regression.txt` retains the invocation error. The next run found three old guard-ordering assertions expecting `schedule_retry`; `regression-final.txt` retains that result. The corrected fixtures assert the same no-guard-before-pacing rule and the new same-attempt helper, while also asserting no attempted-call retry. Final output is [regression-corrected.txt](artifacts/op05-pacing/regression-corrected.txt), database output is [postgres.txt](artifacts/op05-pacing/postgres.txt).

Commands ran from the isolated `backend` directory, with `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1` and the existing test-only Lua dependency directory in `PYTHONPATH`:

```text
python -m pytest tests/unit/test_op05_pacing_holds.py tests/unit/test_op05_pre_attempt_holds.py tests/unit/test_dialer_worker_block_visibility.py tests/unit/test_dialer_origination_durability.py tests/unit/test_dialer_redis_reliability.py tests/unit/test_op01_metering_unavailable.py tests/unit/test_dialer_lead_cooldown_gate.py tests/unit/test_lead_timezone_precedence.py tests/unit/test_dialer_worker_guard_ordering.py tests/unit/test_campaign_schedule.py tests/unit/test_queue_retry_idempotency.py tests/unit/test_dialer_lifecycle_and_reaper.py -q --tb=short
python -m pytest tests/integration/test_op05_pacing_holds.py -q --tb=short
python -m ruff check app/workers/dialer_worker.py tests/unit/test_op05_pacing_holds.py tests/integration/test_op05_pacing_holds.py tests/unit/test_dialer_worker_guard_ordering.py --select F --ignore F401,F841
```

This is local code/database evidence, not a real Redis process restart, PBX call, audible conversation, complete reconciliation mechanism or OP05 closure. No production migration, provider call, push or deployment occurred. The earlier retained-owner reconciliation limitation and all operating acceptance requirements remain open. Counts overlap earlier suites and must not be added into a distinct coverage total.
