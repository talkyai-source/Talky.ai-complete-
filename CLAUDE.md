# CLAUDE.md — Talky.ai

@AGENTS.md

## How to think about a task here (the approach that works)

Follow this order on every non-trivial task. Each step produces something you can show.

1. **Find the source of truth, not the summary.** Docs, release-gate files, commit messages and earlier reports are claims. The code, the running config script, the migration file and the test output are facts. When they disagree, the fact wins and the doc gets a correction. (Example: the release-gate doc said "0 failed"; the suite had 29.)
2. **Reproduce before reasoning.** Run the failing thing and read the real error line. Only then form a hypothesis. Rank hypotheses by evidence, not plausibility; a story that explains everything but was never observed is the one to distrust.
3. **Triage real vs stale.** For each failure decide: (a) code is wrong, (b) test encodes an outdated contract, (c) someone else is mid-edit. Say which, with the line that proves it. Fix (a) in code, (b) by asserting the current invariant, (c) by waiting and re-running.
4. **Decompose into goals with a done-condition you can execute.** "Done when `pytest tests/unit/test_x.py` passes and the full suite has no new failures" — not "done when fixed". Write the board down (files, owner, done-when) before starting; disjoint file sets if parallel.
5. **Severity by consequence, not by effort.** Wrong tenant / money / unconsented recording / silent compliance miss > lost rows or fail-open > everything else. Fix in that order; the cheapest fix is not the first fix.
6. **Smallest change that fixes the root cause.** Write the failing test, make it pass, stop. If you notice a second bug, report it as "beyond brief" and fix it only if it's in your fenced files.
7. **Verify with the canonical command, then re-verify after integration.** Your own test file passing is step one; the full suite after everyone's changes is the result. Numbers come from output you read in this turn.
8. **Report outcome first, evidence second, in a fixed shape.** Root cause → change → file:line → test → pasted summary line. Then "beyond brief" findings, then "could not verify" items. Never bury a limitation in the middle.
9. **Say what you did not do.** "Not run: X, because Y" is more valuable than an optimistic omission. A blocked step is reported, not worked around.
10. **When something can't be verified here (no compiler, no server, sandbox block), stop and say so.** Do not ship an unverifiable change as done; describe it as a prepared change awaiting verification, with the exact command that would verify it.

## Working rules (remedies for known model failure modes)

These exist because they were each violated at least once in real sessions. They are not optional.

### 1. No claim without evidence (false-green)
- Never say "passes", "green", "done", "verified" unless you ran the canonical command **in this turn** and can paste its summary line. A targeted test file is not proof the suite passes.
- Canonical checks: `backend/.venv/Scripts/python -m pytest tests/unit tests/security -q` (≈10 min, ~6700 tests) · `cd Talk-Leee && npm run typecheck && npm run lint && npm test` · `cd Admin/frontend && npm run lint && npm test && npm run build`.
- Read the output **before** writing the number. If a background run has not finished, say "still running".
- Your closing summary is where unverified claims creep in. Re-check every number in it against real output.
- When delegating to subagents: the lead re-runs the full suites after workers report. Worker claims are inputs, not results.

### 2. Root cause before fix (hallucinated diagnosis)
- A plausible story is not a diagnosis. Reproduce the failure, read the actual traceback line, then fix.
- If a test fails only in the full run, check first whether another agent is editing the same file right now (this tree is shared with Codex) before theorising about circular imports or pollution.
- Never "fix" a failing test by loosening or deleting its assertion. If the test encodes a rule (layering guard, billing vocabulary, IDOR scoping, one-Alembic-head), the code must satisfy the rule. If the test is genuinely stale, rewrite it to assert the *current* invariant, not to pass.

### 3. Minimal diff, no drive-by work (over-engineering)
- Change only what the task names. No new dependencies, no speculative abstractions, no reformatting, no renaming neighbours.
- Fixing a bug adjacent to your task is allowed only if you report it separately as "beyond brief".
- Prefer a 5-line wrapper that delegates over a 200-line rewrite. Prove behaviour preservation with an equivalence test before consolidating duplicates.

### 4. Run the project's own checks (ignored repo rules)
- Ruff gate is `ruff check app/ --select F --extend-ignore F401,F841` (must be clean). ESLint must be 0 errors; do not downgrade rules in config — use a one-line targeted disable with a reason where a rule genuinely does not apply.
- Every fix ships with a failing-then-passing test. Tests that grep source text are guards, not proof of behaviour.

### 5. Say less, say it exactly (verbosity)
- Report per item: root cause → change → file:line → test name → command output. No hedging, no restating the brief.

### 6. Scope fences (multi-agent safety)
- Another engineer/agent (Codex) may be editing this working tree. Never `git stash`, `git checkout --`, `git reset`, or commit the whole tree — a commit would snapshot their half-done work under your name. Commit only explicitly named files when asked.
- Assign disjoint file sets to parallel workers; never two workers on one file.
- Do not touch `backend/app/domain/services/telephony/inbound_*`, `inbound_campaign_service.py`, `telephony/`, `setup-asterisk.sh`, or Talk-Leee inbound UI unless the task is explicitly inbound work.

## Environment facts (rediscovered every session — don't)

- Backend venv: `backend/.venv` may be **empty**. Populate with `uv pip install --python .venv/Scripts/python.exe -r requirements.txt -r requirements-dev.txt`. `uv.lock` is a stub; `pyproject.toml` declares no deps.
- Run pytest as `backend/.venv/Scripts/python -m pytest -q --no-header -p no:cacheprovider -W ignore <paths>` (Windows). Tests import `fakeredis` (in requirements-dev).
- Frontend Node ≥22.18 (Admin tests import `.ts` from `.mjs` via built-in type stripping). CI `NODE_VERSION` is 22.
- Talk-Leee is Next.js 16 + React Compiler: never read/write `ref.current` during render; `middleware.ts` is now `proxy.ts`.
- Production = Hetzner bare-metal, systemd units as root, PG/Redis in Docker, deploy via `deploy_to_server.sh` (git checkout of `origin/main`). `docker-compose.yml` is local-dev only. The `telephony/` OpenSIPS/canary stack is modelled, **not deployed**; prod Asterisk config comes from `setup-asterisk.sh`.
- Prod DB app role has been superuser+BYPASSRLS — always add an explicit `tenant_id` predicate in SQL; never rely on RLS alone.
- Partial unique indexes: an `ON CONFLICT` target must name the exact index predicate or Postgres cannot infer it.
- Phone numbers: one normaliser, `app/domain/services/phone_number_normalizer.py` (see its contract table). DNC writes and call-guard reads must use the same function.
- Guards wired to a signal that is constant in prod are a recurring trap: check the input actually varies, and log the wiring per call.
- Sandbox blocks SSH and package installs inside compound commands; run them as separate simple commands or ask the user (`! <cmd>`).
- Repo-root reports/worklogs live in `docs/sessions/`; scratch scripts in `scripts/archive/`. Keep the root clean; `tmp/` is gitignored.
- Line endings: `.gitattributes` enforces LF; do not run `git add --renormalize` on a large uncommitted tree.

## Approach validation log

Tested 2026-08-28 by giving two Opus workers deliberately thin briefs ("fix it", "make the gateway send the header") plus only "read CLAUDE.md and follow it". Graded against: reproduced before editing · diff inside fence · real output pasted · fixed report shape with "not done" stated · no "done" claim without the canonical check.

- **T1 (vague bug: Redis concurrency over-count):** PASS. Found two root causes (blind increment on ON CONFLICT re-acquire; non-atomic GET/SETEX decrement), proved failing-then-passing by restoring old code, ran the full 6.7k suite and Ruff, separated a third cause as "beyond brief, not fixed", and correctly attributed one unrelated red test to Codex's concurrent edit. Lead re-ran: 21 passed. Weakness: one small drive-by (helper + named constants) — allowed but should have been listed as beyond brief.
- **T2 (unverifiable C++ change, no compiler on host):** PASS. Led with "not compiled or run", shipped a regression test + README, gave the exact `run_gate.sh`/ctest commands, named the out-of-fence dependency (gateway systemd env var). Diff confined to `services/voice-gateway-cpp/` (3 files).

Re-run this check whenever the rules above change: same two briefs, same rubric. A worker that says "done" on T2 fails.

## Subagent policy
- Workers on a single well-fenced task, short sessions; long-running workers drift or get killed by host restarts — resume them with "re-verify on disk first".
- Brief must contain: exact files allowed, exact commands to run, "paste real output", "no new deps / no refactors / no commits".
