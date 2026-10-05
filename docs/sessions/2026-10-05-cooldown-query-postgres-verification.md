# Opt-in cooldown SQL verification

Candidate: `a42f270046a21121b60e108fce3926b39b31c5a4`. The eight existing tests in `backend/tests/unit/test_dialer_cooldown_query_real_db.py` passed unchanged against actual PostgreSQL: **8 passed, zero skips**, in 7.69 seconds, with 16 existing Pydantic datetime deprecation warnings. No application or test assertions were changed and no runtime defect was reproduced.

The module was skipped in the whole unit run because its explicit opt-in database variable was unset. Its fixture executes unqualified `TRUNCATE calls, dialer_jobs` before each case, so it must not be pointed directly at the retained public schema.

For this execution, the task script created a uniquely named schema and empty `LIKE public.calls INCLUDING ALL` / `LIKE public.dialer_jobs INCLUDING ALL` copies on the existing loopback disposable `cp04_acceptance_test` database. The public migration marker was `0061_dnc_runtime_contract`. A uniquely named LOGIN role had `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS` and privileges only on the private tables. Before pytest, the script verified that role had no public-table INSERT, UPDATE, DELETE or TRUNCATE privileges and that both unqualified relations resolved to the private OIDs. Its connection URL supplied a schema-only `search_path`, with no public fallback. No password, provider credential or customer action was used.

The original tests then exercised:

- Answered or live calls inside the cooldown window block clearing; unanswered terminal calls and answered calls outside the window do not.
- Remaining cooldown duration is anchored to the qualifying call; absent qualifying evidence returns `None`.
- Persisting the attempt number updates the owned row; a mismatched tenant raises and leaves the other tenant's row unchanged.

These are actual SQL and explicit predicate checks. `LIKE INCLUDING ALL` copies column definitions, defaults, check constraints and indexes; it does **not** copy foreign keys, triggers or RLS policies. The private copies therefore do not prove canonical RLS, audit-trigger, retained-parent, queue, origination or provider behavior. Those remain covered by the separate canonical integration matrix, not by these eight tests.

Before and after execution, the script compared public calls/jobs and every public audit/ledger table using row counts and sorted row-hash digests, plus the calls/jobs RLS flags and ACLs and the migration marker. All were unchanged. Cleanup removed only the task-created schema and role after verifying schema ownership. The PG slot was released immediately after completion; no process remains active.

Evidence and the exact task script are in [artifacts/cooldown-query-pg](artifacts/cooldown-query-pg): `preflight.txt`, `run_isolated.py`, `run-console.txt`, `tests.txt`, `results.xml`, and `verification.json`. The manifest records source and test hashes, private identities, environment, pytest command, and before/after preservation proofs without public row contents.

The exact dependency overlay preceded the existing test-only Lua target in `PYTHONPATH`; the shared original virtual environment was unchanged. Run command, from the isolated worktree root:

```powershell
$env:ENVIRONMENT = 'test'
$env:PYTHONPATH = 'C:/Users/AL AZIZ TECH/Desktop/Talky.ai-complete-/tmp/postgres-matrix-20261005/tmp/exact-requirements-20261005/packages;C:/Users/AL AZIZ TECH/Desktop/Talky.ai-complete-/tmp/production-ready-ag07-20261005/tmp/op02-testdeps'
& 'C:/Users/AL AZIZ TECH/Desktop/Talky.ai-complete-/backend/.venv/Scripts/python.exe' docs/sessions/artifacts/cooldown-query-pg/run_isolated.py --dsn postgresql://talky@127.0.0.1:55434/cp04_acceptance_test
```

The script requires the explicitly disposable loopback database and local test-role authentication. It is a retained verification command, not a production migration or a general-purpose test database provisioning tool.
