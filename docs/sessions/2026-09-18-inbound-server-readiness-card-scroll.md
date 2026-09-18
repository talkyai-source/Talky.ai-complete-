# Session report — Inbound "Server Readiness" card: six-item scroll cap

**Date:** 2026-09-18
**Scope:** Frontend only (Talk-Leee). Inbound campaign detail window, "Server Readiness" card.
**Files changed:** 3 — `src/components/inbound/inbound-status.tsx`, `src/app/inbound-campaigns/[id]/page.tsx`, `src/app/globals.css`.
**Status:** Implemented, verified in a real browser (46/47 automated cases + 6 screenshots), canonical checks green.

---

## 1. The ask

> On the Inbound window's "Server Readiness" card: add a vertical scroll bar inside the card so it shows a maximum of six items at a time, on all devices including phones. Additional items stay in the card's scroll area and appear on scroll. No backend/data/status-logic changes; no changes to other cards unless strictly required.

The task was run in two phases: (1) a **verification-and-planning pass** (no edits — find the real component, understand its data, recommend answers to open design questions), then (2) an **approved implementation pass** once the user signed off on the plan and decisions.

---

## 2. Phase 1 — Verification (before any code changed)

### 2.1 What is the card, exactly

- Inbound detail page: `Talk-Leee/src/app/inbound-campaigns/[id]/page.tsx`
- Card markup: same file, `<section className="content-card" aria-labelledby="readiness-heading">` (line 141)
- Card body/component: `Talk-Leee/src/components/inbound/inbound-status.tsx` → `InboundReadinessChecklist`
- Data type: `Talk-Leee/src/lib/inbound-api.ts` → `InboundReadiness` / `normalizeReadiness`
- Backend source of the checks: `backend/app/domain/services/inbound_campaign_service.py` → `_readiness()`

The card renders three regions: (1) an always-visible status banner ("Safe to request activation" / "Activation is blocked"), (2) the **checks list** — one `<li>` per readiness check (label + one-line detail), and (3) a conditional **"What needs attention" blockers list** shown only when checks fail.

**Item count:** the backend evaluates **28 distinct checks** (direction, tenant/platform enablement, prompt, AI providers, voice, pipeline mode, DID verification, trunk runtime, quota, concurrency, quarantine, DID conflicts, timezone, business hours, after-hours policy, transfer runtime, etc.) — a large, fixed-ish set, not a short list. No existing height cap, overflow, grouping, or pagination existed before this change.

**Row height uniformity:** *not* uniform. Each check's `detail` string wraps to as many lines as it needs (no `line-clamp` originally), so short details ("DID is verified.") render as 1 line while long ones (e.g. the transfer-runtime message) wrap to 2+ lines. This mattered directly for how the six-item cap could be computed and later caused a real bug (§4.2).

**Reuse:** `InboundReadinessChecklist` is imported in exactly one place — this detail page. The list page and the create/edit form only use the separate `ReadinessBadge` pill, not this checklist. So no other page or shared component was affected by touching it.

**Existing in-card scroll pattern to match:** the most recent commit on this branch (`8f753a0b`) added an identical pattern to the Campaign Performance page's **Event Stream** and **Alert Timeline** cards (`src/components/campaigns/event-stream.tsx`, `alert-timeline.tsx`):
- `ROWS_VISIBLE_DESKTOP = 6`, `ROWS_VISIBLE_MOBILE = 4`, `MOBILE_BREAKPOINT_PX = 768`.
- A `ref` on the first rendered row, measured via `getBoundingClientRect()` in a `useEffect` + `requestAnimationFrame`, re-measured on `window resize`.
- `maxHeight = firstRowHeight × rows + gapPx × (rows − 1)`, applied as an inline style on an `overflow-y-auto overscroll-contain pr-1` wrapper.
- A scoped hover-override class (`.event-stream-card:hover`, `.alert-timeline-card:hover` in `globals.css`) that neutralizes the shared `.content-card:hover` transform/shadow, because that hover effect (`translateY(-2px) scale(1.01)` + a 30px-blur shadow) was found to visually overlap neighboring stacked cards.

This became the template for the Server Readiness card.

### 2.2 Open questions answered

Three design questions were flagged and answered with the user before implementing:

| Question | Recommendation | Decision |
|---|---|---|
| Handle uneven row heights while showing exactly six | Measure the first rendered row's real height (as above); optionally `line-clamp-2` the detail text to reduce variance | **Approved**, plus `line-clamp-2` explicitly required |
| Six-item cap on mobile too, or fewer (matching the 4-on-mobile precedent) | Match the sibling cards' 4-on-mobile convention | **Overridden by user: six on all devices, no exception** |
| What stays fixed while the list scrolls | Header, status banner, and the blockers section stay outside the scroll area, always visible | **Approved** |

---

## 3. Phase 2 — Implementation

### 3.1 Files changed (exactly 3, as scoped)

**`src/components/inbound/inbound-status.tsx`** — `InboundReadinessChecklist`
- Added `"use client"` (the component now uses hooks; it previously had none).
- Added `ROWS_VISIBLE = 6`, a `firstItemRef` on the first `<li>`, and a `useEffect` that measures that row's real height and derives `listMaxHeightPx = height × 6 + 8px × 5`, re-measuring on `resize`; falls back to no cap if the row can't be measured (never a hardcoded pixel guess).
- Wrapped **only** the checks `<ul>` in a new `overflow-y-auto overscroll-contain` div carrying that `maxHeight`. The status banner, empty-state, and the "What needs attention" blockers section were left exactly where they were, outside the new scroll wrapper — so they stay visible regardless of scroll position, per the approved decision.
- Added `line-clamp-2` to each check's detail paragraph.
- No changes to `check.passed`, `check.detail` content, `readiness.ready`, or any data/status logic.

**`src/app/inbound-campaigns/[id]/page.tsx`**
- One-line change: the readiness `<section>`'s class became `"content-card server-readiness-card"` (was `"content-card"`), matching how the Event Stream/Alert Timeline cards add their own scoped class alongside the shared one. Nothing else in this file changed.

**`src/app/globals.css`**
- Added a new, separate rule block (did **not** edit the existing `.event-stream-card`/`.alert-timeline-card` block or `.content-card` itself):
  ```css
  .server-readiness-card:hover {
    transform: none;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.10);
  }
  .dark .server-readiness-card:hover {
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.20);
  }
  ```
- Confirmed via grep that `server-readiness-card` wasn't already used anywhere in the codebase before adding it.

### 3.2 A real bug found during implementation — and its fix

`line-clamp-2` only caps the **maximum** number of lines a paragraph renders; it does not pad short text up to that height. Since check details alternate between short one-line text and long two-line (clamped) text, rows were still **not uniform** — exactly the risk flagged in the verification report. This meant the "first row's height × 6" formula didn't produce a value that matched six *actual* rows for a mixed-height list, and a browser test later caught a row being cut in half.

**Fix:** added `min-h-10 leading-5` to the detail paragraph, alongside `line-clamp-2`. `leading-5` pins the line-height to a known, explicit `1.25rem` (rather than trusting an inherited default), and `min-h-10` (`2.5rem` = exactly two such lines) forces every row — short or long — to reserve the same two-line block. This was **beyond the literal instruction** ("apply `line-clamp-2` … so rows stay even in height") but necessary to actually deliver what that instruction asked for; `line-clamp-2` alone did not achieve it.

---

## 4. Browser verification — what happened, in order

The task required real Playwright verification with a temporary, disposable spec (mocked via `page.route`, reusing the existing pattern in `tests/inbound-campaign-workflow.spec.ts`), across 9 widths × 4 data states, plus hover, zoom, and screenshots. This section documents the debugging path taken to get there, because most of the session's effort went into diagnosing why the app wouldn't load in this sandbox at all — not into the feature code itself.

### 4.1 Issue #1 — the page never left "Loading…" (environment, not the feature)

**Symptom:** every one of the first 47 temporary-spec test cases failed identically: `getByRole("heading", { name: "Server readiness" })` never appeared; the page's accessibility snapshot showed only `status: Loading…`.

**Investigation, in order:**
1. Suspected a missing mock. `useAuth()`'s bootstrap effect calls `api.getMe()` (`GET /auth/me`) whenever a Bearer token is present in `localStorage`. The temp spec set that token but never mocked the endpoint → added a `/auth/me` route mock. **Still failed.**
2. Added `console.log` canaries at three levels of the render tree (`AuthProvider`, `DashboardLayout`, the page component itself) to bisect where rendering stalled. **None of the canaries printed**, even though the DOM unmistakably contained `DashboardLayout`'s own spinner markup (`role="status"` wrapping a `sr-only "Loading…"` span) — meaning the component function *had* to have executed, yet its `console.log` never surfaced.
3. Confirmed the console-listener plumbing itself worked (a `page.evaluate(() => console.log(...))` canary printed fine), ruling out a broken test harness.
4. Noticed a **repeating full-page navigation** in the network log — not a soft client-side transition, but a complete fresh reload of every JS chunk roughly every ~45 seconds — alongside a persistent dev-server warning:
   > `Blocked cross-origin request to Next.js dev resource /_next/hmr from "127.0.0.1". … add it to "allowedDevOrigins" in next.config.js`
5. Checked `next.config.ts` — `allowedDevOrigins` **is** configured, but as full origin strings (`"http://127.0.0.1:3100"`, `"http://localhost:3100"`), not the bare hostnames (`"127.0.0.1"`) the framework's own warning suggests. This is a **pre-existing misconfiguration**, unrelated to this task.
6. To rule out "this is specific to my temp spec," ran the **unmodified, pre-existing** `tests/inbound-campaign-workflow.spec.ts` unchanged. **It failed identically** — proof this was an environment-level issue affecting the whole app in this sandbox, not something introduced by this change or this spec.
7. **Fix (test-only, disposable):** changed the temporary spec's `baseUrl` from `http://127.0.0.1:3100` to `http://localhost:3100` — the exact origin string `next.config.ts` already allows. The page then loaded normally within ~20–25s (cold) instead of hanging indefinitely.

No application code was touched to work around this — the fix lived entirely inside the throwaway test file. `next.config.ts`'s `allowedDevOrigins` format bug is reported here as a "beyond brief" finding for a separate fix, not something this task's file-scope permitted touching.

### 4.2 Issue #2 — a row genuinely got cut in half (real bug, in scope)

Once the app actually loaded, the very first substantive assertion against the real feature failed: with exactly 6 checks, one row was only **partially** visible inside the scroll area (confirmed via a `document.elementFromPoint`-style row/container bounding-box comparison in the test). This was the uneven-row-height issue described in §3.2 — check details alternate short/long text, and `line-clamp-2` alone doesn't equalize them.

**Fix:** `min-h-10 leading-5` added to the detail paragraph (§3.2). Re-ran the same case: passed.

### 4.3 Issue #3 — synthetic wheel scroll leaking to the page (test bug, not a product bug)

With the auth gate and the row-height bug both fixed, the "scroll ≥6 items and confirm the page doesn't move" cases still failed: `page.mouse.wheel()` was scrolling the **outer page** by the full requested delta instead of the card's inner container.

**Diagnosis:**
- Built an isolated, minimal HTML control page with the exact same `overflow-y: auto; overscroll-behavior: contain` pattern and ran the identical wheel gesture against it: it scrolled the inner box correctly and left the page untouched (`pageScrollY: 0`). This proved Playwright's wheel mechanism itself works fine in this sandbox for a correctly-scrollable element.
- Checked `document.elementFromPoint()` at the real app's wheel-target coordinates: it returned **`null`**. The card's measured bounding box (`y: 890, height: 568`) placed its center well below the 800px-tall test viewport — `page.mouse.move`/`wheel` use raw viewport coordinates and, unlike `.click()`/`.hover()`, do **not** auto-scroll a target into view first. The wheel was landing on nothing, and fell through to the document by default.

**Fix (test-only):** added `await scrollArea(page).scrollIntoViewIfNeeded()` before computing the bounding box and dispatching the wheel gesture. After this, the wheel correctly moved only the card's `scrollTop`, and the page's own `scrollY` stayed exactly where it was before the gesture.

### 4.4 Issue #4 — small, bidirectional page-height deltas (test timing, not a product bug)

A handful of cases still failed a "the page must not grow" check, by small amounts (~10–50px), sometimes growing, sometimes *shrinking* — the wrong sign for a genuine layout-growth regression. Root cause: the Knowledge / Live Calls / Rejected Calls / Call Issues panels lower on the same page are intentionally unmocked in this spec (out of scope for this card) and settle from "Loading…" to an error/empty state a moment after the readiness card is ready, independently shifting page height.

**Fix (test-only):** added an 800ms settle wait after page load, before taking the "before" page-height baseline, so only the deliberate scroll interaction is under test. Re-ran: all four previously-failing cases passed.

### 4.5 Issue #5 — CSS-zoom stand-in doesn't reproduce real browser zoom's reflow (disclosed limitation, not fixed)

Playwright has no scriptable lever for genuine OS/browser page zoom on desktop Chromium, so the zoom spot-check used the CSS `zoom` property as an explicitly-disclosed stand-in. At 1.25× and 1.5× this passed; at 0.8× a 1px row-cut appeared. Root cause: real browser zoom changes `window.innerWidth`/`innerHeight` and fires a genuine `resize` event — which the component's own re-measurement effect listens for and reacts to with a fresh DOM measurement. CSS `zoom` fires neither on its own; a synthetic `resize` dispatch was added to approximate it, but a small sub-pixel rounding artifact specific to *downscaling* through the CSS `zoom` mechanism remained. This was left **unfixed and disclosed** rather than chased further, since it is a known limitation of the test proxy, not evidence of a bug reachable by an actual user doing Ctrl/Cmd+– in a real browser (which would trigger the component's real resize-driven remeasurement path).

### 4.6 Final verification matrix

After all four test-side fixes (§4.1, 4.3, 4.4) and the one real product fix (§4.2), the full 47-case temporary suite was re-run in full:

**46 of 47 passed.** The sole failure is the disclosed CSS-zoom artifact at 0.8× (§4.5).

| Width | 0 checks | 3 checks | 6 checks | 10 checks |
|---|---|---|---|---|
| 360×640 | ✅ | ✅ | ✅ | ✅ |
| 390×844 | ✅ | ✅ | ✅ | ✅ |
| 412×915 | ✅ | ✅ | ✅ | ✅ |
| 667×375 | ✅ | ✅ | ✅ | ✅ |
| 768×1024 | ✅ | ✅ | ✅ | ✅ |
| 820×1180 | ✅ | ✅ | ✅ | ✅ |
| 1023×768 | ✅ | ✅ | ✅ | ✅ |
| 1024×768 | ✅ | ✅ | ✅ | ✅ |
| 1280×800 | ✅ | ✅ | ✅ | ✅ |

Each ✅ covers: exactly 6 rows fully visible when ≥6 checks exist, zero partially-visible ("cut") rows, no scrollbar when ≤6 items, a scrollbar when >6, scrolling confined to the card (outer `scrollY` and document height unchanged), no horizontal page scroll, and the header/banner/blockers remaining visible through and after scrolling. The hover-overlap check (card vs. its neighbors above/below) passed at all 9 widths.

**Screenshots** (10-check state, saved under the gitignored `test-results/` directory):
- `Talk-Leee/test-results/readiness-scroll-390x844-normal.png`
- `Talk-Leee/test-results/readiness-scroll-390x844-scrolled.png`
- `Talk-Leee/test-results/readiness-scroll-390x844-hovered.png`
- `Talk-Leee/test-results/readiness-scroll-1280x800-normal.png`
- `Talk-Leee/test-results/readiness-scroll-1280x800-scrolled.png`
- `Talk-Leee/test-results/readiness-scroll-1280x800-hovered.png`

Visually inspected: the scrolled/hovered shots at both sizes show exactly six check rows inside the card's own bordered scroll region (rows 5–10 of the 10-check fixture, after scrolling), no half-cut row, and no visual overlap with the "Safety policy"/"Server state" or "How to test inbound calling" cards on hover.

---

## 5. Canonical checks (final code, after all fixes)

Run against the final state of the three changed files, in this order:

| Command | Result |
|---|---|
| `npm run typecheck` | Clean — no output, exit 0 |
| `npm run lint` | Clean — 0 errors, exit 0 |
| `npm test` | `tests 475 · pass 473 · fail 0 · skipped 2` (pre-existing DB-gated skips, unrelated to this change) |
| `npm run build` | Exit 0; `/inbound-campaigns/[id]` compiled and listed in the route manifest |

---

## 6. Cleanup and scope confirmation

- Both temporary Playwright spec files (`tests/tmp-server-readiness-scroll.spec.ts`, `tests/tmp-wheel-control.spec.ts`) were deleted after the verification pass, per the task's requirement. Confirmed absent via a post-delete directory listing.
- All diagnostic-only `console.log` instrumentation added to `auth-context.tsx`, `dashboard-layout.tsx`, and the page component during root-causing (§4.1) was **reverted** — none of it shipped.
- `git status` after cleanup shows **only** the three approved files modified:
  ```
  modified:   src/app/globals.css
  modified:   src/app/inbound-campaigns/[id]/page.tsx
  modified:   src/components/inbound/inbound-status.tsx
  ```
  No backend, API, config, package, or dependency file was touched. No other card, page, or shared component changed.

---

## 7. Beyond brief (found, not fixed here)

- **`next.config.ts:117`** — `allowedDevOrigins` is set to full origin URLs (`"http://127.0.0.1:3100"`) rather than the bare hostnames Next.js expects, which silently breaks HMR (and, in this sandbox, ordinary page hydration) for anyone hitting the dev server via `127.0.0.1` instead of `localhost`. Confirmed via a control run of the pre-existing, otherwise-unmodified `tests/inbound-campaign-workflow.spec.ts`, which fails identically on `127.0.0.1` in this environment. Out of this task's file scope; flagged for a separate, deliberate fix.

## 8. Not done / could not fully verify

- Real OS/browser-level page zoom at 80% — only the CSS `zoom` stand-in was available in this sandbox, and it showed a sub-pixel artifact at 0.8× not expected to occur under genuine browser zoom (§4.5). Recommend a manual spot check if certainty is required.
- Multi-browser verification (Firefox/WebKit/Edge) was not run — Chromium only, matching this project's existing convention for this test file family.

---

## 9. Summary

The Server Readiness card on the inbound campaign detail page now shows a maximum of six readiness-check rows at a time, on every device width tested, with the remainder reachable by scrolling inside the card only — the outer page never grows or scrolls as a result. The header, status banner, and "What needs attention" blockers section stay fixed and visible throughout. The implementation reuses an established, already-shipped pattern (measured-row-height scroll cap + scoped hover-override) from the Event Stream and Alert Timeline cards rather than inventing a new one. One real bug (uneven row heights defeating the six-row cap) was found and fixed during verification; the rest of the debugging effort in this session went into working around a pre-existing, unrelated dev-server misconfiguration that was blocking the browser test harness itself, not the feature under test.
