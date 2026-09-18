# Campaign Window — Event Stream & Error/Alert Timeline: Hide, Scroll, Hover Fix

**Date:** Friday, 18 September 2026
**Project:** Talk-Leee
**Scope:** Frontend only — Campaign window (`/campaigns`), "Event Stream" card and "Error & Alert Timeline" card
**Status:** Code complete, automated checks pass. Browser (Playwright) verification blocked by a pre-existing environment issue — see [Issue 2](#issue-2--playwright-browser-verification-never-hydrates-pre-existing-environment-bug) below.

---

## 1. What was asked for

Two cards on the Campaign Performance page needed four behavior changes, without touching anything else:

1. **A Hide/Show button** on each card — positioned differently on PC/laptop vs. mobile/tablet, but functionally the same everywhere.
2. **Internal vertical scrolling** — both cards should scroll their own content instead of growing the whole page.
3. **A hover-effect fix** — hovering either card was visually bleeding onto the content next to it (the campaign table row above Event Stream, and Event Stream's own rows above Error & Alert Timeline).
4. Everything scoped to exactly these two cards — no backend/API changes, no changes to any other card or shared component unless proven necessary.

The work was split into two phases at the user's request: first a **verification-and-planning-only pass** (no file changes), then a second **implementation pass** once the plan was approved.

---

## 2. Phase 1 — Verification & Planning (no code changes)

Before writing any code, the codebase was read to establish facts rather than assume them, per this repo's stated working rules (source of truth over docs, reproduce before reasoning).

### 2.1 What we found

| Question | Finding |
|---|---|
| Where do these cards live? | `Talk-Leee/src/app/campaigns/page.tsx` renders `CampaignPerformanceTable` → `EventStream` → `AlertTimeline`, stacked in a `space-y-6` column. |
| Card components | `Talk-Leee/src/components/campaigns/event-stream.tsx` and `.../alert-timeline.tsx`. |
| Existing hide/collapse? | None on either card. The closest precedent in the codebase was `call-issues-panel.tsx`, which already collapses to a summary bar with a chevron toggle. |
| Existing scroll behavior? | Neither card capped its own height. Event Stream only capped its "Today" sub-group to 3 rows via a `ResizeObserver`-style measurement hack; every other group, and the whole Alert Timeline list, grew unbounded and pushed the page down. |
| Cause of the hover overlap | Both cards use a shared global class, `.content-card`, whose `:hover` rule applies `transform: translateY(-2px) scale(1.01)` (grows the box ~1% on every side, including upward) and a `box-shadow` with a 30px blur radius — neither of which is clipped by any parent, so it visually paints over the sibling card. |
| Blast radius of that shared class | `.content-card` is used in **32 files** across the app (tables, forms, other dashboards, white-label pages). Editing its shared `:hover` rule directly would have changed hover behavior everywhere, not just these two cards — explicitly out of scope. |
| Breakpoint in use | No custom Tailwind breakpoints are configured; both cards already switch from stacked to row header layout at the default `md` (768px) breakpoint. |
| A mismatch in the brief | The task described "the call log entries above Error & Alert Timeline," but on this page the element directly above Error & Alert Timeline is the **Event Stream card**, not a separate call-log component. This was flagged explicitly in the report; the user's follow-up confirmed Event Stream's rows were indeed what was meant. |

### 2.2 Decisions proposed (and later approved)

- **Hide behavior:** collapse the card body, keep the header (and the button, now reading "Show") always visible — never remove the whole card. Reasoning: matches the existing collapse pattern already used elsewhere in the codebase, and avoids a "how do I get it back" dead end.
- **Persistence:** remember the hidden state per card across a refresh, via `localStorage`, using the exact same `toLocalStorage`/`fromLocalStorage` helper pattern the files already use for filter preferences.
- **Alert Timeline button placement:** to the right of the "N alerts" count, since that's the only existing element on the right side of that header to anchor against.
- **Hover fix:** add scoped CSS classes on just these two cards' root elements, rather than touching the shared `.content-card` rule.

The user approved this plan and gave explicit go-ahead decisions matching the above, plus one clarification: the content above Error & Alert Timeline really is Event Stream's own rows.

---

## 3. Phase 2 — Implementation

### 3.1 `Talk-Leee/src/components/campaigns/event-stream.tsx`

- Added `hidden` state, restored on mount from `localStorage["campaigns.performance.eventStreamHidden"]` and written back on every change — identical mechanism to the file's existing `sound`/`desktop`/`quick` prefs.
- Added a `Button` reading "Hide"/"Show" as the **last item in the existing filter-button row**, right after "User Actions." Because that row is already `flex flex-wrap`, this one change automatically satisfies both the desktop placement (far right, aligned with the other filter buttons) and the mobile placement (directly after "User Actions") — no separate mobile-only markup was needed.
- Wrapped the card body (the sound/desktop notification toggles and the event list) in `{!hidden ? <div id="event-stream-body">...</div> : null}`. The header, title, filters, and Hide button are declared outside that block, so they always render.
- Replaced the old "Today"-group-only scroll hack with **one scroll container around the whole grouped list** (all of Today/Yesterday/Last 7 Days/Older together), using `overflow-y-auto overscroll-contain`.
- The scroll cap height is **measured from the real first rendered row**, not a guessed pixel value: a ref is attached to the first item in the first non-empty group, its rendered height is read via `getBoundingClientRect()`, and the cap is computed as `rowHeight × rows + 8px × (rows − 1)` — 8px matching the existing `space-y-2` gap between rows. `rows` is 6 at ≥768px and 4 below that, recomputed on window resize. This guarantees the cutoff always lands exactly on a row boundary instead of slicing a row in half, regardless of font size or content length.
- Added a new CSS class, `event-stream-card`, alongside the existing `content-card` class on the root `<div>`, purely as a hook for the scoped hover-fix CSS.

### 3.2 `Talk-Leee/src/components/campaigns/alert-timeline.tsx`

Same shape of change, adapted to this file's simpler (non-grouped) list:

- `hidden` state persisted to `localStorage["campaigns.performance.alertTimelineHidden"]`.
- Hide/Show `Button` placed in the header, immediately to the right of the `{filtered.length} alerts` count, inside a small `flex items-center gap-2` wrapper so the two always sit on the same row at every viewport width (the header's own `md:flex-row` split only affects how this row relates to the title block above it, not the count/button relationship).
- Body (the Severity/Type/Status filter pills and the alert list) wrapped the same way, collapsing to nothing while the header stays.
- Same measured-row-height scroll cap (6 rows ≥768px, 4 rows below), applied to the flat alert list via a ref on the first rendered alert button.
- Added `alert-timeline-card` class on the root for the hover-fix hook.

### 3.3 `Talk-Leee/src/app/globals.css`

- Left the shared `.content-card` and `.content-card:hover` rules **completely untouched**, per the approved plan and the 32-file blast-radius finding.
- Added new, scoped rules instead:

  ```css
  .event-stream-card:hover,
  .alert-timeline-card:hover {
      transform: none;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.10);
  }

  .dark .event-stream-card:hover,
  .dark .alert-timeline-card:hover {
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.20);
  }
  ```

  The `transform: none` removes the scale/translate growth entirely. The dark-mode rule was necessary on top of the light-mode one because the original `.dark .content-card:hover` rule has higher CSS specificity (`.dark .content-card:hover` = 0,3,0) than a same-level scoped override (0,2,0) — without a matching `.dark` rule for our own classes, dark mode would have kept the old 30px-blur shadow even after the light-mode fix worked. This was worked out by hand, not guessed, before writing the CSS.

### 3.4 Why these particular choices

- **One placement, not two:** both cards already had a responsive header (`flex-col` on mobile, `flex-row` on desktop via the existing `md:` breakpoint). Adding the Hide button to the existing responsive row meant one code change correctly serves both the "PC/laptop" and "mobile/tablet" requirements in the brief, instead of duplicating markup per breakpoint.
- **Measured, not guessed, scroll height:** the brief explicitly said "measure the real row height and adjust so rows are not cut in half." A hardcoded pixel value would drift the moment fonts, padding, or content length changed. Measuring the actual rendered row at runtime (the same technique the file already used for its old "Today" cap) keeps the cap correct automatically.
- **Scoped hover classes over editing the shared rule:** `.content-card` is used in 32 other files. Editing its `:hover` rule directly, even slightly, risked changing behavior on pages nobody asked to touch — directly against the brief's "no changes to any other card" constraint.

---

## 4. Issues encountered, and how each was resolved

### Issue 1 — CSS specificity: dark-mode shadow would have silently kept bleeding

**What happened:** after writing the first version of the scoped hover override, a specificity check (not a live-browser observation, but working through the CSS cascade rules by hand) showed that `.dark .content-card:hover` (specificity 0,3,0) would still beat a same-level `.event-stream-card:hover` override (0,2,0) in dark mode, because the dark rule is more specific even though it's declared earlier in the file.

**Fix:** added a second, dark-mode-scoped rule (`.dark .event-stream-card:hover, .dark .alert-timeline-card:hover`) with matching specificity, so the contained shadow wins in both themes, not just light mode.

### Issue 2 — Playwright browser verification never hydrates (pre-existing environment bug)

This was the significant problem of the session, and is the reason full browser verification could not be completed.

**What happened:** A temporary Playwright spec was written to check every item in the brief's test plan (Hide button placement/alignment, Hide/Show behavior, scroll containment, hover overlap, filters, zoom spot-checks, screenshots) across all nine required widths. Every single test failed at the very first step: the page never left the app's own "Loading…" placeholder, even after waiting well past any reasonable timeout.

**Investigation (in order, each step ruling out one cause before moving to the next):**

1. Confirmed the auth cookie/localStorage token the app expects was actually present in the browser context at the right time — it was.
2. Instrumented `window.fetch` before any app code ran, to see whether the app was even attempting to call `/auth/me`. **Zero fetch calls were made at all** — not just the auth call, nothing.
3. Checked for thrown errors (`pageerror`) and console warnings (e.g., a React hydration mismatch) — none appeared.
4. Checked every network response's status code — every JavaScript chunk for the route, including the campaign page's own bundle, returned `200 OK`. Nothing 404'd or failed to load.
5. Directly tested whether React had hydrated the page at all, by checking for React's internal fiber reference on DOM nodes after several seconds of waiting. **No element on the page had a React fiber attached** — meaning React's client-side JavaScript never took over the server-rendered HTML at all, on this route.
6. To rule out this being specific to the authenticated Campaign page, the same check was repeated against the **public home page (`/`)**, which needs no authentication at all. **Same result** — no hydration, no fetches, stuck on the same kind of placeholder state.
7. To rule out this being caused by anything in this session's changes, the exact same check was run against an **existing, completely untouched** Playwright spec already in the repo (`tests/dashboard-first-row.visual.spec.ts`). It failed in the identical way.

**Conclusion:** this is a pre-existing, environment-wide failure of client-side React hydration in this specific sandboxed dev-server setup — not something introduced by, or fixable within, the scope of this task. The dev server's own console output pointed at a likely underlying cause (`Blocked cross-origin request to Next.js dev resource /_next/hmr from "127.0.0.1"`, i.e. a Hot-Module-Reload WebSocket handshake failure), but diagnosing or fixing that would mean changing dev-server/HMR configuration (e.g. `next.config.ts`'s `allowedDevOrigins`), which is outside this task's approved file list and outside "frontend Campaign window" scope entirely.

**Resolution taken:** per this repo's explicit rule ("if a page stays on 'Loading…', stop and report it — do not change app auth code to make tests pass"), the investigation stopped there. No auth code, no dev-server config, and no app code beyond the three approved files were touched in an attempt to work around it. The three temporary Playwright spec files created during this investigation were deleted afterward, and their absence was confirmed with a directory listing.

**What this means for confidence in the shipped change:** the code changes themselves were still verified as far as static analysis allows — TypeScript, ESLint, and the full unit-test suite all ran cleanly against the final code. What could **not** be verified in this session was the actual rendered, interactive behavior in a real browser (button alignment at each width, click-to-collapse, scroll containment, hover appearance, zoom behavior, screenshots). That gap is called out explicitly rather than glossed over.

---

## 5. Files changed

- `Talk-Leee/src/components/campaigns/event-stream.tsx`
- `Talk-Leee/src/components/campaigns/alert-timeline.tsx`
- `Talk-Leee/src/app/globals.css`

No other files were modified. No backend, API, configuration, or dependency changes were made. No git commands that alter repository state (commit, push, checkout, stash, reset, branch) were run at any point — only read-only `git status`/`git diff`.

## 6. Automated checks (final state, this session)

- `npm run typecheck` → clean, no errors.
- `npm run lint` → clean, no errors.
- `npm test` → **475 tests total, 473 pass, 0 fail, 2 skipped** (the 2 skips are pre-existing, database-only tests unrelated to this work).

## 7. What is still outstanding

- **Real-browser verification** of the full test plan in the original brief (all 9 widths, 3 data states per card, hover-overlap screenshots, zoom spot-checks at 80/125/150%, filter regressions) — blocked by the environment issue in [Issue 2](#issue-2--playwright-browser-verification-never-hydrates-pre-existing-environment-bug). Recommended next step: re-run a Playwright verification pass (the deleted spec's approach — mocking `/auth/me`, `/campaigns`, `/events`, `/alerts` via `page.route`) once the dev-server hydration issue is resolved, or in a different environment where `npm run dev` hydration is confirmed working first.
- No other open items from the approved plan remain — all four required behaviors (Hide placement on both size classes, persistence, internal scrolling with a measured row cap, and the scoped hover fix) are implemented in code and covered by the static checks above.
