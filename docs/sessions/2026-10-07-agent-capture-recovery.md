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

## Frontend follow-up — 8 October 2026

Source: `396335f0788af3e0db5d968e87c77b5d90fc7beb`. Root's first two-module frontend run preserved **32 passes / 4 failures**: the same new assertion failed for all four transcript states because it expected a headline which the summary card intentionally does not render. The DOM already contained the correct body and source warning. The tests now assert the rendered `what_happened` body and retain each exact source-warning assertion. The stale-result negative also uses that displayed field, so it cannot pass merely because headlines are never shown.

The Calls page's `SummaryPreview` moved unchanged into existing `call-panels.tsx`, following the repository's existing testable-presentational-component convention. Its body was compared to the prior source and matched exactly after adding `export`; both existing page uses import it. Seven real React DOM controls exercise this actual preview: unavailable, missing and stale results suppress the old list headline and current body, while partial/failed/unknown/complete results show current content with the appropriate independent warning. No API, query key, props or product behavior changed.

Owner checks in the isolated worktree, using Node **v25.8.1** and a newly created junction to the existing integration tree's `Talk-Leee/node_modules` (no install or shared dependency changes):

```text
node --test --import tsx --import ./src/test-utils/setup.ts src/components/calls/CallSummaryCard.test.tsx src/app/calls/page.test.tsx
node node_modules/typescript/bin/tsc -p tsconfig.json --noEmit
node node_modules/eslint/bin/eslint.js src/app/calls/page.tsx src/app/calls/page.test.tsx src/components/calls/call-panels.tsx src/components/calls/CallSummaryCard.test.tsx
git diff --check
```

**24 tests passed**, none skipped, 14.90s; TypeScript, scoped ESLint and diff check exited 0 without diagnostics. Logs: [tests](artifacts/agent-capture-recovery/frontend-followup.txt), [types](artifacts/agent-capture-recovery/frontend-followup-typecheck.txt), [lint](artifacts/agent-capture-recovery/frontend-followup-lint.txt). Independent CRM review cleared the move and assertions without execution. These results close the owner frontend checks left pending above; they do not replace root's separate database/integration qualification or establish browser, deployed UI or model acceptance.
