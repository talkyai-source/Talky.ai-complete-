# Durable capture recovery and summary evidence — 7 October 2026

Source: `84ba08df4a30f72247b63cb9aa10bbd8d119b1f1`, based on `6c9eed991a7c69afd70536379427933f099052d9`. Scope is existing AG05 saved evidence and its review surfaces. No queue, journal, migration, second capture store, call-resume path or confirmation reconstruction was added.

## Findings and repair

Incremental transcripts already persist to `calls` with independent `partial`, `failed`, `unknown` or `complete` save state. The existing summary request can resume business-note writes from a current, committed summary after local transcript state is gone; it does not need another model call. This behavior and preservation of a higher-trust manual note were verified as positive controls. Analysis processing status remains independent of transcript completeness. Existing Leads warnings already distinguish those states, so that contract was preserved.

The summary endpoint did not expose source completeness, and could return an old analysis after a transcript correction, summary replacement or call disappearance during processing. It now performs a tenant/call-scoped final observation. A result is available only when it equals the persisted summary and that summary's hash matches the current transcript/action snapshot. Otherwise the response withholds it. `source_evidence` reports the observed revision, transcript save state, currentness and need for review.

The existing summary card and call-list summary preview show incomplete-source warnings and suppress stale headline fallback when the current response is unavailable. An unavailable summary no longer implies that the call had no conversation. The existing explicit reload action can re-run processing of saved evidence.

The read is a point-in-time observation, not a lock on future revisions. It does not claim completeness of omitted or never-committed words, model correctness, or a new guarantee about unrelated contact mutations. Failed live capture still cannot claim an acknowledged save; this patch does not reinterpret its lost in-memory command as a confirmed contact.

## Verification

- New unit baseline: **7 failed / 2 passed**, 1.69s. Four cases lacked completeness metadata; three exposed stale results. The two existing positive behaviors were recovery of a missing business note and preservation of a manual note.
- Focused final: **9 passed**, 1.13s.
- Affected final: **93 passed**, 97 warnings, 4.86s across seven modules, including the focused nine; no exclusions. Existing summary revision, business-detail, contact recording, transcript flush and finalization controls remain green.
- Scoped Ruff `F` and `git diff --check`: passed.
- Independent CRM source/test review: clear, read-only, no rerun by reviewer.

These runs used Python 3.12.12 from the existing repository virtual environment. The archived runner reuses the prior socket/async/Windows-Proactor guard; only internal event-loop socketpairs were allowed. **Zero prohibited transport attempts**, with recorded source hashes unchanged during each run. The initial attempted baseline command stopped before pytest because its slash spelling did not satisfy the guard's exact `PYTHONPATH` check; the recorded baseline uses the resolved Windows path. No provider or database was contacted.

Commands are the recorded `pytest_argv` in [baseline.json](artifacts/agent-capture-recovery/baseline.json), [focused.json](artifacts/agent-capture-recovery/focused.json) and [affected.json](artifacts/agent-capture-recovery/affected.json), invoked through [runner.py](artifacts/agent-capture-recovery/runner.py). Corresponding `.txt` logs are adjacent. Startup environment was `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`, `ENVIRONMENT=test`, `PYTHONPATH=<resolved worktree>/backend`, and the deliberately unavailable `DATABASE_URL=postgresql://test:test@127.0.0.1:1/unavailable_test`.

Three additional actual-PostgreSQL controls are supplied in `backend/tests/integration/test_capture_restart_recovery.py`, reusing the existing synthetic UUID/RLS fixture. They cover failed note-write recovery from committed summary evidence, complete versus partial transcript status, foreign-tenant denial and later durable source revision. Owner did **not** execute these; root owns the reviewed disposable-database qualification. Six new summary-card controls, existing preview wiring, frontend type/lint checks and combined regression likewise await root integration checks. Their eventual results must not be counted as owner runs.

## Limits

Discarding process-local state models a restart boundary; the unit run is not an operating-system crash experiment. Durable-source recovery is demonstrated, not recovery of audio/text that never committed. A live contact interpretation that never reached storage cannot be reconstructed as a confirmed value from its transcript. Source warnings and existing review/reprocessing remain the honest route. Real database transaction/RLS results, deployed UI, provider/model comprehension, audio, customer acceptance and release remain separately qualified work. No push or deployment occurred.
