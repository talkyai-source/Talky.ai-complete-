# Inbound section, wizard and call history — requirements spec

> # ⚠️ SUPERSEDED — 2026-09-01
>
> **This document is a historical record. Do not build from it.**
>
> It was written while the inbound backend did not exist, and it says so in
> several places. That is no longer true: `origin/main` delivered
> `backend/app/api/v1/endpoints/inbound_campaigns.py`, registered on the v1
> router at `backend/app/api/v1/routes.py:83`.
>
> **Successor:** [SPEC-inbound-v2.0.0-FROZEN.md](./SPEC-inbound-v2.0.0-FROZEN.md)
> **Contract of record:** [BACKEND-CONTRACT-EXTRACT.md](./BACKEND-CONTRACT-EXTRACT.md)
>
> Two statements in this document are **factually false** as of 2026-09-01
> and are corrected inline where they appear:
>
> 1. §1 — that no inbound configuration endpoint exists. Fifteen do.
> 2. Companion §4.2 — that `GET /calls/` has no `direction` parameter and
>    that one must not be assumed. It has one, plus `inbound_campaign_id`.
>
> The routes described here (`/inbound`, `/inbound/new`, `/inbound/calls`)
> were removed on 2026-09-01 and now redirect to `/inbound-campaigns*`.
> What remains valuable and was carried into 2.0.0: the named-state
> taxonomy, the eight-attribute field-table format, the error envelope, and
> the rule that placeholder data may never harden into a contract.

| | |
|---|---|
| **Version** | 1.0.0 |
| **Status** | **SUPERSEDED** — 2026-09-01 (frozen 2026-08-31) |
| **Superseded by** | [SPEC-inbound-v2.0.0-FROZEN.md](./SPEC-inbound-v2.0.0-FROZEN.md) |
| **Scope** | Frontend only: the Inbound section shell and navigation, the inbound wizard, and the call-history view. |
| **Companion documents** | [Forms, states and API payloads](./SPEC-inbound-forms-states-api-v1.0.0-FROZEN.md) · [Open questions](./OPEN-QUESTIONS.md) · [Shared-component collisions](./SHARED-COMPONENT-COLLISIONS.md) |

## 0. Freeze notice

This spec is version-frozen at 1.0.0. The three screens it describes are the
whole of the Inbound frontend scope; nothing outside §3–§9 is in scope.

**Anything raised after the freeze goes to [OPEN-QUESTIONS.md](./OPEN-QUESTIONS.md), not into this
spec.** That includes new screens, new fields, new filters, new states, and
every backend contract question. Amending the spec requires a version bump
and a re-freeze.

Explicit non-goals, restated so they cannot be read back in as omissions:
no backend, schema, queue or worker changes; no edits to outbound code; no
telephony (no provider SDKs, number provisioning, SIP, call routing or
media); no billing (no pricing, plans, quotas, metering or invoices).

---

## 1. Nothing here is a backend contract unless it says CONFIRMED

> **CORRECTION — 2026-09-01.** The premise of this section no longer holds.
> The inbound backend contract **is** settled: fifteen tenant-facing routes
> exist under the `/inbound-campaigns` prefix
> (`backend/app/api/v1/endpoints/inbound_campaigns.py:41`), registered on the
> v1 router (`backend/app/api/v1/routes.py:83`), with request and response
> models in `backend/app/api/v1/schemas/inbound_campaigns.py`. Read
> [BACKEND-CONTRACT-EXTRACT.md](./BACKEND-CONTRACT-EXTRACT.md) instead of
> this section. The specific claim below that "no inbound configuration
> endpoint exists" — restated in
> [OPEN-QUESTIONS.md](./OPEN-QUESTIONS.md) §1 — is **false**.

The inbound backend contract is not settled. This spec therefore describes
what the **frontend** renders and how it behaves. Every field name in it is
a frontend-owned name. Where a real endpoint exists it is marked CONFIRMED
in the companion document, with its method, path and payloads taken from the
FastAPI source. Everything else is marked **OPEN** and carries an entry in
[OPEN-QUESTIONS.md](./OPEN-QUESTIONS.md) naming the endpoint, the field and the decision needed.

The screens still render: a placeholder fixture set sits behind a single
swappable data module (`src/lib/inbound/inbound-data.ts`). The fixtures
carry no authority and no component may read a fixture field name — a
structural test enforces both (§11).

---

## 2. Vocabulary

| Term | Means |
|---|---|
| **Inbound agent** | One saved inbound configuration. What the list view lists and the wizard creates. |
| **Inbound section** | The three screens below plus their two detail panels, reached from one sidebar entry. |
| **Named state** | A state in §5, §7 or §9. The running frontend puts its current one on a `data-inbound-state`, `data-inbound-step-state` or `data-inbound-*-panel-state` attribute. |

---

## 3. Navigation entry point and route paths

**Entry point.** One top-level sidebar item, **Inbound**, added to
`navigation` in `src/components/layout/sidebar.tsx`. Icon `PhoneIncoming`,
target `/inbound`. It is visible to every authenticated role, has no
`adminOnly` flag and no child rows. The addition is purely additive — no
existing entry, route, ordering or behaviour changes (COL-01).

The section's second screen is reached from a tab strip inside the section
shell rather than a second sidebar row, so the sidebar gains exactly one
entry.

| Route | Screen |
|---|---|
| `/inbound` | Inbound list view (section home) |
| `/inbound?config=<id>` | Inbound list view with the inbound detail panel open |
| `/inbound/new` | Inbound wizard |
| `/inbound/calls` | Call-history view |
| `/inbound/calls?call=<id>` | Call-history view with the call detail panel open |

`?state=<scenario>` may be appended to any of these to exercise a named
state (§10). It is honoured only while the data module runs on fixtures.

---

## 4. Screen inventory

Five surfaces across three routes. There is nothing else in the section.

### 4.1 Inbound list view — `/inbound`

- **Purpose.** Show every inbound agent on the tenant, and be the way into
  creating one or opening one.
- **Who can reach it.** Any authenticated role, `readonly` included
  (`canViewInbound`). `readonly` sees the list but not the create action.
- **What leaves the screen, and where it goes.**
  - Row click → the inbound detail panel (§4.2), same route, `?config=<id>`.
  - **New inbound agent** → `/inbound/new`. Rendered only when
    `canCreateInbound` is true; `readonly` sees a "Read-only access" note in
    its place.
  - **Call history** tab → `/inbound/calls`.
  - Empty-state primary action → `/inbound/new` (create-capable roles only).
  - No-permission action → `/dashboard`.

### 4.2 Inbound detail panel — `/inbound?config=<id>`

- **Purpose.** Show one inbound agent's saved settings, read-only.
- **Who can reach it.** Anyone who can reach the list view.
- **Opened by.** Clicking a list row. Clicking the selected row again, or
  the panel's close control, removes `?config` and closes it.
- **Contents.** Business, agent, status, description, greeting, tone,
  knowledge summary, availability summary, out-of-hours message, last
  updated. No edit control — editing an existing agent is out of scope for
  1.0.0 (OQ-CFG-06).
- **What leaves the screen.** Close only. The panel has no outbound links.

### 4.3 Inbound wizard — `/inbound/new`

- **Purpose.** Create one inbound agent across five ordered steps (§7).
- **Who can reach it.** Roles at `user` and above (`canCreateInbound`).
  `readonly` reaching the route directly gets the no-permission state.
- **What leaves the screen, and where it goes.**
  - **Create inbound agent** on the final step → `/inbound?config=<newId>`.
  - **Save and exit** → `/inbound`, draft retained.
  - **Cancel** → confirm, then `/inbound`, draft discarded.
  - No-permission action → `/dashboard`.

### 4.4 Call-history view — `/inbound/calls`

- **Purpose.** List the calls that came in to this tenant's inbound agents,
  filterable and paged, and be the way into one call's detail.
- **Who can reach it.** Any authenticated role, `readonly` included.
- **What leaves the screen, and where it goes.**
  - Row click → the call detail panel (§4.5), same route, `?call=<id>`.
  - **Inbound agents** tab → `/inbound`.
  - **New inbound agent** → `/inbound/new` (create-capable roles only).
  - No-permission action → `/dashboard`.

### 4.5 Call detail panel — `/inbound/calls?call=<id>`

- **Purpose.** Show one inbound call read-only: received time, answering
  agent, status, outcome, duration, summary and transcript.
- **Who can reach it.** Anyone who can reach the call-history view.
- **Opened by.** Clicking a call-history row. Clicking the selected row
  again, or the panel's close control, removes `?call` and closes it.
- **What leaves the screen.** Close, and — from the row-detail error state —
  retry. No outbound links.

---

## 5. Named states — Inbound section

These five are the section's states. The list view (§4.1) realises all five.
The detail panel (§4.2) realises `loading`, `populated` and `error` — `empty`
has no meaning for a single record, and `no-permission` is handled one level
up by the list view, which never renders the panel in that case.
`no-permission` additionally pre-empts every other screen in the section,
call history included.

| State | Entered when | The screen shows |
|---|---|---|
| `loading` | The list read is in flight, or auth is still resolving. | Skeleton card titled "Loading inbound agents". |
| `empty` | The list read resolved with zero inbound agents. | "No inbound agents yet" with a **New inbound agent** action for create-capable roles. |
| `populated` | The list read resolved with one or more agents. | The rows, plus the detail panel when `?config` is set. |
| `error` | The list read rejected with anything other than a permission failure. | "Could not load inbound agents", the failure message, and **Retry**. |
| `no-permission` | The viewer's role fails `canViewInbound`, or a read rejected as forbidden. | "You do not have access to Inbound" and **Back to dashboard**. |

Full trigger / visible output / enabled controls / exit rows for each are in
the companion document §3.

---

## 6. The wizard at a glance

Five ordered steps, forward-only entry (a step is enterable only when every
earlier step validates), free backward movement.

| # | Step | Purpose |
|---|---|---|
| 1 | **Basics** | Name the inbound agent and say which business it answers for. |
| 2 | **Agent and greeting** | Decide who the caller hears and the first thing they are told. |
| 3 | **Knowledge** | Give the agent the facts it answers from. |
| 4 | **Availability** | Say when the agent answers and what a caller hears outside those hours. |
| 5 | **Review and create** | Show everything entered, then create the inbound agent. |

---

## 7. The wizard in detail

### 7.1 Per-step entry, exit and blocking fields

"Blocking fields" are the fields that must validate before **Next** (or
**Create**) advances. Their constraints and exact inline messages are in the
companion document §2.

| # | Step | Entry condition | Exit condition | Fields that must validate to advance |
|---|---|---|---|---|
| 1 | Basics | The wizard opens here, or **Back** from step 2. Always enterable. | `configName` and `businessName` validate and `description` is within length. | `configName`, `businessName`, `description` |
| 2 | Agent and greeting | Step 1 validates. | `agentDisplayName`, `tone` and `greetingText` all validate. | `agentDisplayName`, `tone`, `greetingText` |
| 3 | Knowledge | Steps 1–2 validate. | `knowledgeMode` is a known value **and** the field it reveals validates: `knowledgeText` when "paste", `knowledgeFileName` when "upload". | `knowledgeMode`, plus `knowledgeText` **or** `knowledgeFileName` |
| 4 | Availability | Steps 1–3 validate. | `availabilityMode` is a known value; when it is "hours", `answerFromTime`, `answerToTime` and `outOfHoursMessage` all validate. | `availabilityMode`, plus `answerFromTime`, `answerToTime`, `outOfHoursMessage` when mode is "hours" |
| 5 | Review and create | Steps 1–4 validate. | `confirmAccurate` is true **and** the create call resolves. | `confirmAccurate` |

A hidden field never blocks: `knowledgeText` is not validated in upload
mode, and the three availability fields are not validated in "any hour"
mode.

### 7.2 Navigation behaviour

| Control | Available on | Behaviour |
|---|---|---|
| **Back** | Steps 2–5 | Returns to the previous step. Clears the current step's inline messages and returns it to `idle`. Entered values are kept. Disabled on step 1 and while `saving`. |
| **Next** | Steps 1–4 | Runs that step's validation. On failure the step becomes `blocked-by-validation` and stays put. On success the step becomes `complete` and the wizard advances one step. |
| **Save and exit** | Every step | Writes the draft (current step + all values, minus `confirmAccurate` and minus the picked file handle) to this browser's storage and navigates to `/inbound`. Does not create anything. Disabled while `saving`. |
| **Cancel** | Every step | Asks "Discard this draft?" inline. **Discard** clears the stored draft and navigates to `/inbound`; **Keep editing** returns to the step untouched. Disabled while `saving`. |
| **Create inbound agent** | Step 5 only | Validates step 5, then calls the create path. See §7.4. |
| **Resume** | Wizard entry | If a stored draft exists, the wizard opens on a resume prompt instead of a step: **Continue where I left off** restores the values and the saved step; **Start over** clears the draft and opens step 1 at defaults. |

Resume rules that are part of the freeze:

- A draft is **per browser**. There is no draft endpoint, so it is not
  synced between devices or sessions (OQ-CFG-05). The resume prompt says so.
- `confirmAccurate` is never restored — the final confirmation must be
  given against what is on screen now.
- A picked knowledge file cannot survive a reload. Only its name is stored;
  an upload-mode draft asks for the file again.
- Resume never lands past a step that no longer validates; it falls back to
  step 1 in that case.

### 7.3 Named states per step

Held per step, not per wizard. `saving` and `save-failed` are reachable only
on step 5, the only step that writes.

| State | Entered when | The step shows | Exits to |
|---|---|---|---|
| `idle` | The step is entered, or any field on it is edited. | Fields editable, no inline messages. | `validating` on Next/Create. |
| `validating` | **Next** or **Create** is pressed. | Fields as-is; **Next** disabled for the duration. | `blocked-by-validation` on failure, `complete` (steps 1–4) or `saving` (step 5) on success. |
| `blocked-by-validation` | Validation returned at least one field. | An inline message under each failing field; the step does not advance. | `idle` as soon as a failing field is edited. |
| `saving` | Step 5 validated and the create call is in flight. | Spinner on **Creating…**; every field, **Back**, **Save and exit**, **Cancel** and **Create** disabled. | `complete` on success, `save-failed` on failure, no-permission on a forbidden failure. |
| `save-failed` | The create call rejected with anything other than a permission failure. | The failure message above the button, which now reads **Try again**. Fields re-enabled. | `saving` on retry, `idle` on any edit. |
| `complete` | Steps 1–4: the step validated and the wizard moved past it. Step 5: the create call resolved. | Steps 1–4: a check mark on that step in the stepper. Step 5: the wizard has already navigated away. | Steps 1–4: `idle` on **Back**. Step 5: terminal. |

### 7.4 Final step behaviour

1. **Create inbound agent** validates step 5. A missing confirmation leaves
   the step in `blocked-by-validation` and nothing is created.
2. On success the step enters `saving` and the create path is called through
   the data module with the trimmed values from every step.
3. **What is created:** one inbound agent. The create endpoint is **OPEN**
   (OQ-CFG-02) — the frontend passes its own form-state keys to the data
   module and builds no request body.
4. On success the stored draft is cleared and the user lands on
   **`/inbound?config=<newId>`** — the list view with the new agent's detail
   panel open.
5. On failure the step enters `save-failed`; the draft is **not** cleared and
   nothing is navigated. A forbidden failure renders the no-permission state
   instead.

---

## 8. Call history in detail

### 8.1 Columns

Six columns. Sorting and paging apply to the table as a whole.

| # | Column | Data type | Format | Sort | Truncation |
|---|---|---|---|---|---|
| 1 | **Received** | ISO 8601 timestamp string | Local short date + 24-hour time, `30 Aug 2026, 14:22`; `—` when unparseable | **Sortable.** The only sortable column. Toggles newest-first / oldest-first; default **newest first**. Sorts the rows on the current page (server-side ordering is OPEN — OQ-CH-05). | None. Fixed width, tabular figures. |
| 2 | **Caller** | string | Rendered exactly as supplied; tabular figures | Not sortable | None. Column is sized to hold an international number. |
| 3 | **Answered by** | string | Rendered as supplied | Not sortable | Single line, ellipsis at the column edge; the full value is the cell's `title`. |
| 4 | **Status** | string | Pill, rendered as supplied. No frontend enum — the canonical set is OPEN (OQ-CH-03). | Not sortable | None. Pill sizes to its label. |
| 5 | **Outcome** | string | Rendered as supplied | Not sortable | Single line, ellipsis at the column edge; the full value is the cell's `title`. |
| 6 | **Duration** | integer seconds, nullable | `m:ss`, right-aligned, tabular figures; `—` when null | Not sortable | None. |

### 8.2 Row contents and row click

A row shows exactly the six columns above and nothing else — no inline
play, no inline transcript, no per-row actions.

The whole row is one button. Clicking it sets `?call=<id>` and opens the
call detail panel (§4.5). Clicking the selected row again clears `?call` and
closes the panel. The row carries `aria-pressed` to reflect selection.

### 8.3 Filter and search controls

Four controls plus a reset. Full field table — types, defaults, constraints,
messages, disabled conditions — in the companion document §2.3.

| Control | Input type | Allowed values | Default | Reset behaviour |
|---|---|---|---|---|
| **Search caller number** | Search text | 0–32 characters, digits, spaces, `+`, `-`, `(`, `)` only. Matched on digits, so formatting differences do not hide a row. | `""` | Returns to `""` |
| **Status** | Single select | `all`, plus the options the data module reports at runtime. `all` is a frontend sentinel and is never sent as a status. The canonical backend enum is **OPEN** (OQ-CH-03), so no status value is hard-coded in any component. | `all` | Returns to `all` |
| **From** | Date | `YYYY-MM-DD`, on or before **To** when both are set | `""` | Returns to `""` |
| **To** | Date | `YYYY-MM-DD`, on or after **From** when both are set | `""` | Returns to `""` |
| **Reset filters** | Button | — | — | Sets all four back to the defaults above and returns to page 1. Disabled while all four are already at their defaults. |

Behaviour that is part of the freeze:

- Changing any filter returns the table to page 1.
- An invalid filter set is never sent. The failing control shows its inline
  message and the last valid result stays on screen.
- The status control is additionally disabled when the data module reports
  no status options.

### 8.4 Pagination and total count

- **Model:** page-based paging, not infinite scroll.
- **Page size:** 25, fixed. Not user-adjustable in 1.0.0.
- **Controls:** **Previous** and **Next**. Previous is disabled on page 1;
  Next is disabled on the last page; both are disabled while `loading`.
- **Total count:** rendered under the table as `Showing 1–25 of 132`, and
  `Showing 0 of 0` when the total is zero. Total comes from the list
  envelope's `total`.

---

## 9. Named states — call history

Seven states. The section-level `no-permission` state (§5) pre-empts all of
them: when a read is refused for permission reasons the table is not
rendered at all.

| State | Entered when | The screen shows |
|---|---|---|
| `loading` | A list read is in flight — first load, filter change, page change or retry. | Filter bar (controls disabled) above a skeleton titled "Loading inbound calls". |
| `empty` | The read resolved with zero rows **and** every filter is at its default. | "No inbound calls yet". |
| `empty-after-filter` | The read resolved with zero rows and at least one filter is off its default. | "No calls match these filters" with a **Reset filters** action. |
| `populated` | The read resolved with one or more rows. | Filter bar, table, pagination, and the detail panel when `?call` is set. |
| `partial-load error` | The list read rejected — either incompletely or entirely. | A red banner carrying the failure message and **Retry**, above whatever rows were last loaded. Filters stay usable. |
| `row-detail loading` | A row was clicked and its detail read is in flight. | The table unchanged; the panel shows a skeleton. |
| `row-detail error` | The detail read rejected. | The table unchanged; the panel shows the failure message with **Try again** and **Close**. |

`partial-load error` deliberately covers both a partial and a total list
failure: the table's contract is "show what we have and say that something
is missing", and that is the same screen either way.

---

## 10. Exercising every named state

While the data module runs on fixtures, append `?state=<scenario>` to any
inbound route. This is how a reviewer reaches a state without contriving
backend conditions.

| `?state=` | Reaches |
|---|---|
| *(omitted)* or `default` | `populated` on both list screens |
| `loading` | `loading` on either screen, held indefinitely |
| `empty` | `empty` on either screen; also empties the status filter options |
| `error` | Section `error` on `/inbound`; `partial-load error` on `/inbound/calls` |
| `no-permission` | `no-permission` on every screen, including the wizard's create path |
| `partial-load-error` | `partial-load error` on `/inbound/calls` |
| `row-detail-loading` | `row-detail loading` in either panel, held indefinitely |
| `row-detail-error` | `row-detail error` in either panel |
| `save-failed` | `save-failed` on wizard step 5 |

`empty-after-filter` needs no scenario — set a filter that matches nothing
(for example a caller search of `999999`).

An unrecognised value falls back to `default`, and the parameter is ignored
entirely once the data module is switched to a live source.

---

## 11. Where each item lives in the running frontend

Round-tripping table: every screen and state above resolves to a file, and
every file below is named above.

| Spec item | File |
|---|---|
| Navigation entry (§3) | [sidebar.tsx](../../src/components/layout/sidebar.tsx) — one added `navigation` row |
| Section shell and tabs (§3, §4) | [inbound-section-shell.tsx](../../src/components/inbound/inbound-section-shell.tsx) |
| Inbound list view + its 5 states (§4.1, §5) | [inbound-list-view.tsx](../../src/components/inbound/inbound-list-view.tsx), route [page.tsx](../../src/app/inbound/page.tsx) |
| Inbound detail panel (§4.2) | [inbound-config-detail-panel.tsx](../../src/components/inbound/inbound-config-detail-panel.tsx) |
| Wizard, its 5 steps and 6 per-step states (§6, §7) | [inbound-wizard.tsx](../../src/components/inbound/inbound-wizard.tsx), route [new/page.tsx](../../src/app/inbound/new/page.tsx) |
| Draft save / resume / discard (§7.2) | [inbound-draft-store.ts](../../src/lib/inbound/inbound-draft-store.ts) |
| Call-history view + its 7 states (§8, §9) | [inbound-call-history-view.tsx](../../src/components/inbound/inbound-call-history-view.tsx), route [calls/page.tsx](../../src/app/inbound/calls/page.tsx) |
| Call detail panel (§4.5) | [inbound-call-detail-panel.tsx](../../src/components/inbound/inbound-call-detail-panel.tsx) |
| `no-permission` state (§5) | [inbound-no-permission.tsx](../../src/components/inbound/inbound-no-permission.tsx) |
| Who can reach what (§4) | [inbound-access.ts](../../src/lib/inbound/inbound-access.ts) |
| Field tables, constraints, messages (§7.1, §8.3) | [inbound-form-schema.ts](../../src/lib/inbound/inbound-form-schema.ts) |
| Column formats (§8.1), total-count line (§8.4) | [inbound-format.ts](../../src/lib/inbound/inbound-format.ts) |
| View models and state unions (§5, §7.3, §9) | [inbound-types.ts](../../src/lib/inbound/inbound-types.ts) |
| The single swappable data module, scenarios (§10) | [inbound-data.ts](../../src/lib/inbound/inbound-data.ts) |
| Placeholder fixtures — no authority | [inbound-fixtures.ts](../../src/lib/inbound/inbound-fixtures.ts) |
| Fixture-isolation guard | [structural-inbound-fixture-isolation.test.ts](../../src/lib/structural-inbound-fixture-isolation.test.ts) |
| Field-table and state coverage guards | [inbound-form-schema.test.ts](../../src/lib/inbound/inbound-form-schema.test.ts), [inbound-data.test.ts](../../src/lib/inbound/inbound-data.test.ts) |

The rendered state is readable from the DOM without instrumentation:
`data-inbound-state` on the list and call-history screens,
`data-inbound-step-state` and `data-inbound-step` on the wizard,
`data-inbound-panel-state` and `data-inbound-call-panel-state` on the panels.
