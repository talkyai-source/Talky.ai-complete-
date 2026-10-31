# Inbound — shared-component collision log

> ## ⚠️ PARTIALLY SUPERSEDED — 2026-09-01
>
> This log records collisions from the 1.0.0 work, whose `/inbound` surface
> was removed on 2026-09-01. Three entries below no longer describe the
> code. Corrections, so the log is not read as current:
>
> | Entry | Recorded | Now |
> |---|---|---|
> | **COL-01** | One sidebar row added, `href: "/inbound"` | **Removed.** The surviving row is `{ name: "Inbound", href: "/inbound-campaigns" }`. Both rows briefly coexisted, both named `"Inbound"` — a duplicate React key under `key={item.name}`, plus a duplicate `PhoneIncoming` import that broke `tsc` and `next build`. |
> | **COL-02 / COL-08** | `no-permission` and `Select` forked into `components/inbound/` | **Fork retired.** `InboundNoPermission` was removed; the canonical surface uses `InboundPermissionState`. The field components survive and no longer import `@/lib/inbound/` at all. |
> | **COL-07** | `/calls` deliberately **not** generalised; `/inbound/calls` kept separate | **Reversed, deliberately.** That decision was correct when `GET /calls/` had no way to scope to inbound. It now has `direction` (`calls.py:1084`) and `inbound_campaign_id` (`:1087`), so `/inbound/calls` was removed and `/calls` carries the filter. Keeping two call-history screens once the backend could serve one would have been the defect. |
>
> **A new collision, recorded here for the first time — COL-11.** Two
> implementations edited the same shared file, `sidebar.tsx`, without either
> knowing. The 1.0.0 log calls its own sidebar edit "purely additive", and
> in isolation it was. Additive edits to a shared array are not additive
> when two of them land. That is the failure mode this log exists to catch,
> and it went uncaught because a collision log records only what its own
> author touched.
>
> Still accurate: COL-03, COL-04, COL-05, COL-06, COL-09, COL-10.

The Inbound work was built under a hard rule: **do not touch outbound**. No
edits, renames, refactors, or shared-component changes that alter outbound
screens, routes or state. Where an inbound need met a shared component, the
component was forked or extended without changing outbound behaviour, and the
collision was logged here.

Recorded 2026-08-31 against the 1.0.0 freeze.

## Summary of what was touched outside `inbound/`

Exactly one file: `src/components/layout/sidebar.tsx`, one added array entry
and one added icon import. Everything else the section needs is new code
under `src/app/inbound/`, `src/components/inbound/` and `src/lib/inbound/`.

---

## COL-01 — Sidebar navigation array *(modified, additively)*

- **Shared component:** [`src/components/layout/sidebar.tsx`](../../src/components/layout/sidebar.tsx)
- **Collision:** the section needs a navigation entry point, and the nav is a
  single module-level `navigation` array shared by every dashboard route.
  There is no registration seam.
- **Resolution:** appended one row —
  `{ name: "Inbound", href: "/inbound", icon: PhoneIncoming }` — immediately
  after the existing Call History row, plus the matching `lucide-react` import.
- **Why this is not an outbound change:** it is purely additive. No existing
  entry's name, `href`, icon, `adminOnly` flag or position changes; nothing is
  removed; no child rows are introduced; the filtering, dropdown-expansion and
  measurement logic below the array is untouched. The rendering path is
  data-driven, so an added row exercises code that already runs for the other
  eleven rows.
- **Not forked because:** a forked sidebar would mean two navigation lists to
  keep in step, and Inbound would be invisible from every existing screen —
  the section would have no entry point at all, which the frozen spec requires
  it to have (§3).
- **Verified:** `sidebar.test.ts` covers the sidebar *store* (collapse,
  persistence, storage events) and asserts nothing about the array contents —
  it passes unchanged. `routes.test.ts` asserts specific routes use
  `DashboardLayout` and is unaffected. Full suite result in the section below.

## COL-02 — `no-permission` page state *(forked)*

- **Shared component:** [`src/components/states/page-states.tsx`](../../src/components/states/page-states.tsx)
- **Collision:** the module exports `LoadingSkeleton`, `LoadingState`,
  `ErrorState` and `EmptyState`. The frozen spec names a fifth state,
  `no-permission`, which has no equivalent there.
- **Resolution:** forked as
  [`components/inbound/inbound-no-permission.tsx`](../../src/components/inbound/inbound-no-permission.tsx),
  built from the same primitives (`Card`, `Button`) so it sits in the same
  visual language.
- **Why not extend the shared module:** adding an export changes a file every
  outbound screen imports. The change would be additive, but the rule is not
  "additive changes are fine" — it is "do not alter shared components that
  outbound depends on". A twenty-line fork costs less than that risk.
- **Note for later:** if the shared module ever grows a no-permission state
  for its own reasons, this fork should be retired in favour of it
  (OQ-FE-05).

## COL-03 — `LoadingState`, `ErrorState`, `EmptyState`, `LoadingSkeleton` *(reused, unmodified)*

- **Shared component:** `src/components/states/page-states.tsx`
- **Collision:** none. The four exports fit the inbound `loading`, `error`,
  `empty` and `empty-after-filter` states as they are — including
  `EmptyState`'s `kind="inbox"` and `kind="search"` variants and its
  primary-action props.
- **Resolution:** imported and used as published. **No edits.** Logged only so
  the reuse is on the record.

## COL-04 — `DashboardLayout` *(reused, unmodified)*

- **Shared component:** `src/components/layout/dashboard-layout.tsx`
- **Collision:** none. All three inbound routes wrap in `DashboardLayout` with
  a `title` and `description`, matching the convention `routes.test.ts`
  enforces for new routes.
- **Resolution:** used as published. **No edits.**

## COL-05 — `Button`, `Input`, `Label`, `Select`, `Card` *(reused, unmodified)*

- **Shared components:** `src/components/ui/*`
- **Collision:** none. The wizard and filter form need text, textarea, radio,
  checkbox, time, date, file, select and button inputs. `Select` takes plain
  `<option>` children and a controlled `value`/`onChange`, which is what the
  status and tone controls need.
- **Resolution:** used as published. **No edits.** Textarea, radio, checkbox
  and file inputs have no shared component, so the inbound forms render native
  elements with the same Tailwind token classes as `Input` — a local style
  constant, not a new shared component.

## COL-06 — Role and permission helpers *(reused, read-only)*

- **Shared module:** `src/lib/auth-roles.ts`, `src/hooks/useAuth.ts`
- **Collision:** none. The `no-permission` state needs a role check.
- **Resolution:** [`lib/inbound/inbound-access.ts`](../../src/lib/inbound/inbound-access.ts)
  wraps the existing `hasRole` in two named predicates (`canViewInbound`,
  `canCreateInbound`). Nothing in `auth-roles.ts` is modified, and no new role
  or permission value is introduced.

## COL-07 — Outbound call history at `/calls` *(untouched)*

- **Shared surface:** `src/app/calls/page.tsx`, `src/components/calls/*`,
  `src/lib/dashboard-api.ts`
- **Collision:** the outbound call-history screen already lists calls, groups
  them by campaign, and has its own search and filter toolbar. The temptation
  was to generalise it and pass a direction flag.
- **Resolution:** **not done.** `/inbound/calls` is a separate screen, with its
  own columns, its own filter set and its own states, and shares no component
  or module with `/calls`. `dashboard-api.ts`, `api-hooks.ts` and the `Call`
  type are untouched; the inbound section reads only through
  `lib/inbound/inbound-data.ts`.
- **Why:** generalising `/calls` would change an outbound screen's state
  handling, its query keys and its data mapping — exactly what the rule
  forbids. Two screens is the correct cost.

## COL-08 — `Select` in the reusable field layer *(forked)*

*Recorded 2026-08-31, second pass — the reusable field components.*

- **Shared component:** [`src/components/ui/select.tsx`](../../src/components/ui/select.tsx)
- **Collision:** `InboundSelectField` needs a dropdown. The shared `Select` is
  the obvious candidate and is already reused by the wizard's tone control
  (COL-05), but it calls `useTheme()`, which **throws** outside a
  `ThemeProvider`, and it renders its option panel through a `createPortal`
  into `document.body`. Both make it impossible to satisfy the requirement
  that every field component render in isolation, with no provider.
- **Resolution:** `InboundSelectField` renders a native `<select>` carrying the
  same Tailwind token classes and the same `ChevronDown` affordance. This is
  the treatment the textarea, radio, checkbox and file inputs already get —
  native elements in the shared visual language — so the field layer is
  internally consistent rather than half provider-bound.
- **Why not wrap the shared component:** a wrapper would inherit the provider
  requirement, so the components could only be tested inside an app shell.
  That would have put every one of the four named field states back out of
  reach of an automated test, which is the gap this work exists to close.
- **What was NOT done:** `components/ui/select.tsx` is untouched. The wizard's
  existing tone control still uses the shared `Select` and still behaves
  exactly as before — this fork adds a component, it does not replace one.
- **Verified:** the shared `Select` has no test of its own; `input.test.tsx`
  and `page-states.test.ts` cover the other reused primitives and pass
  unchanged. Full suite result below.

---

## 2026-09-01 addendum — the six-field campaign wizard

**Nothing outside the inbound directories was touched by this work.** The
brief's "do not touch outbound" rule was met without a new collision: no
shared component was edited, forked or wrapped, and `sidebar.tsx` was not
touched again — the Inbound entry COL-01 added on 2026-08-31 is the entry the
brief asks for and it already routes to the campaign list.

Files changed, all of them inbound-owned:

| File | Change |
|---|---|
| `src/app/inbound/new/page.tsx` | Mounts the campaign wizard in create mode. |
| `src/app/inbound/[id]/edit/page.tsx` | New route — the wizard in edit mode. |
| `src/components/inbound/inbound-campaign-wizard.tsx` | New — the six-field wizard. |
| `src/components/inbound/inbound-config-detail-panel.tsx` | Added an optional **Edit campaign** action, default off (OQ-FE-10). |
| `src/components/inbound/inbound-list-view.tsx` | Passes `canEdit` to the panel. One added prop; every state it renders is unchanged. |
| `src/lib/inbound/inbound-campaign-fields.ts` | New — the six-field table and its validation. |
| `src/lib/inbound/inbound-data.ts` | Five added functions. Every existing export is untouched. |
| `src/lib/inbound/inbound-draft-store.ts` | Campaign draft under its own storage key. Every existing export and the existing key's shape are untouched. |
| `src/lib/inbound/inbound-fixtures.ts` | Four added fixture fields and two option lists, all `fx_`-prefixed or label-only. |

Two inbound-internal collisions, recorded because they are departures from the
frozen 1.0.0 spec rather than from a shared component:

- **COL-09 — the frozen wizard is superseded on its own route.** `/inbound/new`
  now mounts `inbound-campaign-wizard.tsx` instead of `inbound-wizard.tsx`.
  The 13-field component and its schema, draft store and tests are left in the
  tree, unmounted and passing, so the swap back is one import. Logged as
  OQ-FE-09; the underlying question is OQ-CFG-15.
- **COL-10 — the read-only detail panel gained an action.** The frozen §4.2
  panel has "no edit control". It now has one, behind a `canEdit` prop that is
  off by default and is passed only for create-capable roles. Logged as
  OQ-FE-10; the underlying question is OQ-CFG-06.

---

## Verification

Run from `Talk-Leee/`.

| Check | Command | Result |
|---|---|---|
| Types | `npm run typecheck` | Clean. |
| Lint | `npm run lint` | 0 errors, 30 warnings — all pre-existing, none in an inbound file. |
| Build | `npm run build` | Succeeds. `/inbound`, `/inbound/calls` and `/inbound/new` prerender. |
| Inbound tests | `node --test --import tsx --import ./src/test-utils/setup.ts src/lib/inbound/*.test.ts src/lib/structural-inbound-fixture-isolation.test.ts` | 28 pass, 0 fail. |
| Full suite | `npm test` | 225 tests, 220 pass, 3 fail — **all three pre-existing and unrelated**: `structural-auth-isolation` flags `components/campaigns/test-agent-button.tsx` (outbound, untouched), and `theme-implementation`'s `globals.css` assertion fails along with its parent suite (`globals.css` untouched). Confirmed by re-running both files with the sidebar edit stashed: same three failures. |
