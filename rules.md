# Engineering Rules — Talky.ai

Non-negotiable standards for all work in this repo. Stated by Uzair; treat as
binding. "Build like a 20-year senior engineer — production-grade, not patches."

## 1. No patches. Root cause + production-grade only.
- Never apply a fallback, band-aid, heuristic, or defensive try/except that
  masks a symptom. Trace the true root cause, fix THAT, and explain it.
- Every change must be the real, durable design that serves the product in
  production — not a quick fix that "usually works." If it wouldn't survive
  real prod traffic, it's not done.

## 2. Clean module boundaries. No beefy files.
- **New files: ≤ 500 lines.** If a file is approaching that, split it by
  responsibility BEFORE it grows — don't dump multiple concerns into one file.
- One module = one clear responsibility. A reader should locate logic fast and
  debug it without scrolling a god-file.
- Prefer a small folder of focused files over a single large file. Hot paths
  may stay flat; everything else is decomposed by concern.
- (Existing precedent: `backend/app/services/scripts/` ≤600/file; strangler-fig
  decomposition of monoliths — keep that discipline everywhere.)

## 3. Test before integration. Don't push until done AND tested.
- Do NOT push to GitHub or deploy until the whole change is complete and
  verified (unit tests pass, build/typecheck clean, behaviour confirmed).
- Write tests for new logic (the project uses pytest backend / vitest+Playwright
  frontend). Characterize before refactoring hot paths.

## 4. Rate honestly.
- When asked to assess work, call a patch a patch and name the gap to
  "most senior." No inflated self-rating.

## 5. Standing project invariants (do not break)
- Telephony API runs `uvicorn --workers 1` (per-call state + ARI socket are
  process-local).
- Frontend deploys to Vercel from the repo root (`dangerouslyDisableSandbox`).
- `global_ai_config` is process-global — never mutate from tenant code.
- Postgres RLS is dormant — enforce tenant isolation with explicit
  `.eq("tenant_id")` filters, never trust RLS.
- Commits: Uzair is sole author — never add a Claude co-author trailer.
- Never blanket-autofix F401 (breaks intentional re-exports).
