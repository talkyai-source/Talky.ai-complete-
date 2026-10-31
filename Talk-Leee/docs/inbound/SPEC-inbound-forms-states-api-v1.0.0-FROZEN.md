# Inbound — form fields, states and API payloads

> # ⚠️ SUPERSEDED — 2026-09-01
>
> **This document is a historical record. Do not build from it.**
>
> **Successor:** [SPEC-inbound-forms-states-api-v2.0.0-FROZEN.md](./SPEC-inbound-forms-states-api-v2.0.0-FROZEN.md)
> **Contract of record:** [BACKEND-CONTRACT-EXTRACT.md](./BACKEND-CONTRACT-EXTRACT.md)
>
> **§4.2 is the dangerous part.** It is headed *CONFIRMED* and it is wrong:
>
> - It instructs the reader that `GET /calls/` has **no `direction`
>   parameter** and that one must not be assumed. The endpoint has
>   `direction` (`calls.py:1084`) **and** `inbound_campaign_id`
>   (`calls.py:1087`). The instruction is corrected inline at §4.2.
> - It cites `calls.py:501` and `calls.py:608`. The handlers are now at
>   `calls.py:1079` and `calls.py:1260`.
> - It documents `CallListItem` as 12 fields and `CallDetail` as 13. They
>   carry **31** and **38**.
>
> The wizard field table in §2.1 describes a 13-field form that **cannot
> create a campaign**: it lacks `did_number`, `campaign_id` and
> `sip_trunk_id`, three of the four fields `InboundCampaignCreateRequest`
> requires. Do not implement from it.
>
> What was carried into 2.0.0: the eight-attribute table format, the state
> tables, the error envelope (§4.4), and the fixture-containment rule (§5).

| | |
|---|---|
| **Version** | 1.0.0 |
| **Status** | **SUPERSEDED** — 2026-09-01 (frozen 2026-08-31) |
| **Superseded by** | [SPEC-inbound-forms-states-api-v2.0.0-FROZEN.md](./SPEC-inbound-forms-states-api-v2.0.0-FROZEN.md) |
| **Companion to** | [SPEC-inbound-v1.0.0-FROZEN.md](./SPEC-inbound-v1.0.0-FROZEN.md) |
| **Also see** | [Open questions](./OPEN-QUESTIONS.md) · [Shared-component collisions](./SHARED-COMPONENT-COLLISIONS.md) |

## 1. How to read this document

- **§2** — one row per field, for every form in the Inbound section, carrying
  all eight required attributes with no blanks.
- **§3** — one row per named state, for every screen and form, with trigger,
  visible output, enabled controls and exit.
- **§4** — API contracts. An endpoint appears with full payloads **only**
  where the backend contract is confirmed in source. Everything else is
  marked **OPEN** with nothing defined beyond what the contract states.

Every field key in §2 is a **frontend form-state key**. None is a backend
field name. No request body is built from them anywhere in this section; the
wizard hands them to the data module and the mapping to a real payload is
[OQ-CFG-02](./OPEN-QUESTIONS.md).

The executable copy of every table in §2 is
[`inbound-form-schema.ts`](../../src/lib/inbound/inbound-form-schema.ts), and
[`inbound-form-schema.test.ts`](../../src/lib/inbound/inbound-form-schema.test.ts)
asserts that no attribute is blank and that every default and inline message
here is the one the form actually uses.

---

## 2. Field tables

### 2.1 Wizard

`saving` below always means "the step's named state is `saving`", which is
reachable only on step 5. Disabling on the earlier steps is therefore
unreachable in practice but is specified so the rule is uniform.

#### Step 1 — Basics

| Field key | Display label | Input type | Required | Default | Constraint | Inline validation message | Disabled or hidden when |
|---|---|---|---|---|---|---|---|
| `configName` | Inbound agent name | Text | Required | `""` | 2–60 characters after trimming | "Enter a name between 2 and 60 characters." | Disabled while the step state is `saving`. Never hidden. |
| `businessName` | Business name | Text | Required | `""` | 2–80 characters after trimming | "Enter the business name callers will hear (2 to 80 characters)." | Disabled while the step state is `saving`. Never hidden. |
| `description` | Description | Textarea | Optional | `""` | 0–280 characters | "Keep the description under 280 characters." | Disabled while the step state is `saving`. Never hidden. |

#### Step 2 — Agent and greeting

| Field key | Display label | Input type | Required | Default | Constraint | Inline validation message | Disabled or hidden when |
|---|---|---|---|---|---|---|---|
| `agentDisplayName` | Agent name | Text | Required | `""` | 2–40 characters after trimming, pattern `^[\p{L}][\p{L} '-]*$` (letters, spaces, hyphen, apostrophe) | "Enter an agent name of 2 to 40 letters." | Disabled while the step state is `saving`. Never hidden. |
| `tone` | Tone | Single select | Required | `"professional"` | One of `professional`, `friendly`, `concise` | "Choose a tone." | Disabled while the step state is `saving`. Never hidden. |
| `greetingText` | Greeting | Textarea | Required | `""` | 10–300 characters after trimming | "Write a greeting between 10 and 300 characters." | Disabled while the step state is `saving`. Never hidden. |

#### Step 3 — Knowledge

| Field key | Display label | Input type | Required | Default | Constraint | Inline validation message | Disabled or hidden when |
|---|---|---|---|---|---|---|---|
| `knowledgeMode` | How should the agent learn? | Radio group | Required | `"paste"` | One of `paste`, `upload` | "Choose how to provide knowledge." | Disabled while the step state is `saving`. Never hidden. |
| `knowledgeText` | Knowledge | Textarea | Required when `knowledgeMode = "paste"` | `""` | 20–20000 characters after trimming | "Add at least 20 characters of knowledge." | **Hidden** when `knowledgeMode ≠ "paste"`. Disabled while the step state is `saving`. |
| `knowledgeFileName` | Knowledge file | File | Required when `knowledgeMode = "upload"` | `""` | Extension `.md` or `.txt`, size ≤ 10 MB (10485760 bytes). Rejected at pick time, before it reaches form state. | "Choose a .md or .txt file up to 10 MB." | **Hidden** when `knowledgeMode ≠ "upload"`. Disabled while the step state is `saving`. |

#### Step 4 — Availability

| Field key | Display label | Input type | Required | Default | Constraint | Inline validation message | Disabled or hidden when |
|---|---|---|---|---|---|---|---|
| `availabilityMode` | When does the agent answer? | Radio group | Required | `"always"` | One of `always`, `hours` | "Choose when the agent answers." | Disabled while the step state is `saving`. Never hidden. |
| `answerFromTime` | Answer from | Time | Required when `availabilityMode = "hours"` | `"09:00"` | 24-hour `HH:MM`, pattern `^([01]\d\|2[0-3]):[0-5]\d$` | "Enter a start time as HH:MM." | **Hidden** when `availabilityMode ≠ "hours"`. Disabled while the step state is `saving`. |
| `answerToTime` | Answer until | Time | Required when `availabilityMode = "hours"` | `"17:00"` | 24-hour `HH:MM`, same pattern, and strictly later than `answerFromTime` | "Enter an end time later than the start time." | **Hidden** when `availabilityMode ≠ "hours"`. Disabled while the step state is `saving`. |
| `outOfHoursMessage` | Out-of-hours message | Textarea | Required when `availabilityMode = "hours"` | `""` | 10–300 characters after trimming | "Write an out-of-hours message between 10 and 300 characters." | **Hidden** when `availabilityMode ≠ "hours"`. Disabled while the step state is `saving`. |

#### Step 5 — Review and create

| Field key | Display label | Input type | Required | Default | Constraint | Inline validation message | Disabled or hidden when |
|---|---|---|---|---|---|---|---|
| `confirmAccurate` | I have checked these details | Checkbox | Required | `false` | Must be `true` | "Confirm the details before creating the agent." | Disabled while the step state is `saving`. Never hidden. Never restored from a resumed draft. |

### 2.2 Inbound list view and both detail panels

No forms. The list view has no inputs, and both detail panels are read-only —
their only controls are close and, on the call panel's error state, retry.
Editing a saved inbound agent is out of scope for 1.0.0 ([OQ-CFG-06](./OPEN-QUESTIONS.md)).

### 2.3 Call-history filter form

| Field key | Display label | Input type | Required | Default | Constraint | Inline validation message | Disabled or hidden when |
|---|---|---|---|---|---|---|---|
| `search` | Search caller number | Search text | Optional | `""` | 0–32 characters, pattern `^[\d\s+()-]*$`. Compared on digits only. | "Use digits, spaces, +, -, or parentheses only." | Disabled while the call-history state is `loading`. Never hidden. |
| `status` | Status | Single select | Optional | `"all"` | `"all"` plus the options the data module reports at runtime. `"all"` is a frontend sentinel and is never sent as a status value. The canonical backend enum is **OPEN** ([OQ-CH-03](./OPEN-QUESTIONS.md)) and no status value is hard-coded in a component. | "Choose a status." | Disabled while the call-history state is `loading`, and while the data module reports no status options. Never hidden. |
| `fromDate` | From | Date | Optional | `""` | `YYYY-MM-DD`, pattern `^\d{4}-\d{2}-\d{2}$`, on or before `toDate` when both are set | "Enter a start date on or before the end date." | Disabled while the call-history state is `loading`. Never hidden. |
| `toDate` | To | Date | Optional | `""` | `YYYY-MM-DD`, same pattern, on or after `fromDate` when both are set | "Enter an end date on or after the start date." | Disabled while the call-history state is `loading`. Never hidden. |

**Reset filters** is a button, not a field: it restores all four defaults
above and returns to page 1, and is disabled while all four are already at
their defaults.

---

## 3. State tables

### 3.1 Inbound list view — `/inbound`

| State | Trigger | What the user sees | Enabled controls | Exits when |
|---|---|---|---|---|
| `loading` | Route entered, auth still resolving, or the list read is in flight (including a retry). | Skeleton card, "Loading inbound agents". | Section tabs; **New inbound agent** (create-capable roles). | The read resolves → `populated`/`empty`; rejects → `error`/`no-permission`. |
| `empty` | The read resolved with zero rows. | "No inbound agents yet" card with an explanation. | Section tabs; **New inbound agent** in the header and in the card (create-capable roles only). | Navigating to the wizard, or a retry that returns rows. |
| `populated` | The read resolved with one or more rows. | The agent rows: name, business, agent, status pill, last-updated. Detail panel to the right when `?config` is set. | Section tabs; **New inbound agent**; every row; the detail panel's close control. | A row click toggles the panel; a tab or action navigates away. |
| `error` | The read rejected with a non-permission failure (`unavailable`). | "Could not load inbound agents", the failure message. | Section tabs; **New inbound agent**; **Retry**. | **Retry** → `loading`. |
| `no-permission` | The viewer's role fails `canViewInbound`, or a read rejected as `forbidden`. | "You do not have access to Inbound" with an explanation. | **Back to dashboard** only. No tabs, no create action, no rows. | **Back to dashboard** leaves the section. |

### 3.2 Inbound detail panel — `/inbound?config=<id>`

Rendered inside the list view's `populated` state. `no-permission` is handled
one level up, by the list view.

| State | Trigger | What the user sees | Enabled controls | Exits when |
|---|---|---|---|---|
| `loading` | A row was clicked, or the route was opened with `?config`, and the detail read is in flight. | Panel header plus a five-line skeleton. | Panel close; the list behind it. | The read resolves → `populated`; rejects → `error`. |
| `populated` | The detail read resolved. | Business, agent, status, description, greeting, tone, knowledge summary, availability summary, out-of-hours message, last updated. Empty values render as `—`. | Panel close; the list behind it. | Close, or clicking the selected row again, clears `?config`. |
| `error` | The detail read rejected (`not-found`, including an unknown id). | "Could not open this inbound agent" and the failure message. | **Close**; the list behind it. | **Close** clears `?config`. |

### 3.3 Wizard — `/inbound/new`

Held per step. `saving` and `save-failed` are reachable only on step 5.

| State | Trigger | What the user sees | Enabled controls | Exits when |
|---|---|---|---|---|
| `idle` | The step is entered, **Back** returns to it, or any field on it is edited. | The step's fields at their current values, no inline messages. | All visible fields; **Back** (steps 2–5); **Save and exit**; **Cancel**; **Next** or **Create**. | **Next**/**Create** → `validating`. |
| `validating` | **Next** (steps 1–4) or **Create inbound agent** (step 5) pressed. | Fields unchanged. | All fields; **Back**; **Save and exit**; **Cancel**. **Next** is disabled for the duration. | Validation failed → `blocked-by-validation`; passed → `complete` (1–4) or `saving` (5). |
| `blocked-by-validation` | Validation returned one or more failing fields. | An inline message under each failing field, matching §2 exactly. The wizard does not advance. | Everything `idle` enables. | Editing any failing field → `idle`. |
| `saving` | Step 5 validated and the create call is in flight. | **Creating…** with a spinner. | Nothing. Every field, **Back**, **Save and exit**, **Cancel** and **Create** are disabled. | Resolved → `complete`; rejected `unavailable` → `save-failed`; rejected `forbidden` → the wizard's no-permission state. |
| `save-failed` | The create call rejected with a non-permission failure. | The failure message above the action, which now reads **Try again**. The stored draft is untouched. | All fields; **Back**; **Save and exit**; **Cancel**; **Try again**. | **Try again** → `validating`; editing any field → `idle`. |
| `complete` | Steps 1–4: the step validated and the wizard advanced past it. Step 5: the create call resolved. | Steps 1–4: a check mark against that step in the stepper. Step 5: the wizard has navigated to `/inbound?config=<newId>`. | Steps 1–4: **Back** re-opens the step. Step 5: none — the screen is gone. | Steps 1–4: **Back** → `idle`. Step 5: terminal. |

Two wizard-level surfaces that are not step states:

| Surface | Trigger | What the user sees | Enabled controls | Exits when |
|---|---|---|---|---|
| Resume prompt | The wizard mounts and a stored draft exists. | "You have an unfinished inbound agent", the save date, and a note that drafts are not synced between devices. | **Continue where I left off**; **Start over**. | Either choice dismisses the prompt and shows a step. |
| No-permission | The viewer fails `canCreateInbound`, or the create call rejected as `forbidden`. | "Creating an inbound agent needs more than read-only access." | **Back to dashboard** only. | Leaving the route. |

### 3.4 Call-history view — `/inbound/calls`

The filter form has no states of its own: it is driven by these, and its
enabled/disabled behaviour is the "Enabled controls" column below plus §2.3.

| State | Trigger | What the user sees | Enabled controls | Exits when |
|---|---|---|---|---|
| `loading` | A list read is in flight — first load, filter change, page change, or retry. | Filter bar above a skeleton, "Loading inbound calls". | Section tabs; **New inbound agent** (create-capable roles). All four filters, **Reset filters**, **Previous** and **Next** are disabled. | Resolves with rows → `populated`; zero rows → `empty`/`empty-after-filter`; rejects → `partial-load error`/`no-permission`. |
| `empty` | The read resolved with zero rows and every filter is at its default. | Filter bar above "No inbound calls yet". | Section tabs; all four filters. **Reset filters** disabled (already default). | A filter change or retry starts a new read. |
| `empty-after-filter` | The read resolved with zero rows and at least one filter is off its default. | Filter bar above "No calls match these filters". | Section tabs; all four filters; **Reset filters** in the card and in the bar. | **Reset filters** or any filter change → `loading`. |
| `populated` | The read resolved with one or more rows. | Filter bar; the six-column table; the total-count line and pager. Detail panel to the right when `?call` is set. | Section tabs; all four filters; **Reset filters** (when off-default); every row; **Previous**/**Next** within range; the panel's controls. | A row click toggles the panel; a filter, page or tab change starts a new read. |
| `partial-load error` | The list read rejected — `unavailable` or `partial`. | A red banner with the failure message and **Retry**, above whatever rows were last loaded (none on a first load). | Section tabs; all four filters; **Reset filters**; **Retry**; any rows still shown and their pager. | **Retry** or any filter change → `loading`. |
| `row-detail loading` | A row was clicked, or the route was opened with `?call`, and the detail read is in flight. | Table unchanged; the panel shows a five-line skeleton. | Everything `populated` enables, plus the panel's close. | The read resolves → panel `populated`; rejects → `row-detail error`. |
| `row-detail error` | The detail read rejected (`not-found`, including an unknown id). | Table unchanged; the panel shows "Could not open this call" and the failure message. | Everything `populated` enables, plus **Try again** and **Close** in the panel. | **Try again** → `row-detail loading`; **Close** clears `?call`. |
| `no-permission` *(section-level, §3.1)* | The viewer fails `canViewInbound`, or a read rejected as `forbidden`. | "You do not have access to Inbound" with call-history wording. | **Back to dashboard** only. No tabs, no filters, no table. | Leaving the section. |

### 3.5 Call detail panel — `/inbound/calls?call=<id>`

Rendered inside the call-history `populated` state. `no-permission` is handled
one level up, by the call-history view.

| State | Trigger | What the user sees | Enabled controls | Exits when |
|---|---|---|---|---|
| `row-detail loading` | A row was clicked, or the route was opened with `?call`, and the detail read is in flight. | Panel header plus a five-line skeleton. | Panel close; the table behind it. | The read resolves → `populated`; rejects → `row-detail error`. |
| `populated` | The detail read resolved. | Received, answered by, status, outcome, duration, summary, then the transcript as speaker-labelled turns — or "No transcript for this call." when there are none. Empty values render as `—`. | Panel close; the table behind it. | **Close**, or clicking the selected row again, clears `?call`. |
| `row-detail error` | The detail read rejected (`not-found`, including an unknown id). | "Could not open this call" and the failure message. | **Try again**; **Close**; the table behind it. | **Try again** → `row-detail loading`; **Close** clears `?call`. |

---

## 4. API

### 4.1 What the Inbound screens call today

**Nothing.** All five surfaces read through
[`inboundData`](../../src/lib/inbound/inbound-data.ts), whose source is set to
`"fixture"`. There is no confirmed way to scope any call endpoint to inbound
(see §4.3, [OQ-CH-01](./OPEN-QUESTIONS.md)), and there is no inbound
configuration endpoint at all. Rather than guess one, the section renders from
placeholder fixtures and every unsettled decision is logged.

Switching `INBOUND_DATA_SOURCE` to `"live"` before those questions are
answered throws rather than silently rendering fabricated data.

When the contract lands, the live branch goes **inside** that module and must
route through the shared `api` HttpClient — a structural test
(`structural-no-bare-fetch.test.ts`) rejects a bare `fetch()` to the backend.

### 4.2 CONFIRMED contracts

These four exist in `backend/app/api/v1/endpoints/calls.py` and are reproduced
here exactly as the source declares them. They are **adjacent** contracts: the
inbound screens do not call them today because none of them can be scoped to
inbound direction, but they are the contracts an inbound call list and detail
would most likely be built on, so the shapes are frozen here for whoever
settles [OQ-CH-01](./OPEN-QUESTIONS.md).

#### CONFIRMED — `GET /calls/` (list)

*Source: `calls.py:501`, `response_model=CallListResponse`.*

**Request — query parameters**

| Name | Type | Required | Default | Constraint |
|---|---|---|---|---|
| `page` | integer | Optional | `1` | `≥ 1` |
| `page_size` | integer | Optional | `20` | `1 ≤ n ≤ 100` |
| `status` | string | Optional | — | No enum is declared. |
| `from` | string | Optional | — | `YYYY-MM-DD` |
| `to` | string | Optional | — | `YYYY-MM-DD` |

> **CORRECTION — 2026-09-01.** The paragraph immediately below is **false**
> and must not be acted on. `GET /calls/` now declares:
>
> | Name | Source | Type |
> |---|---|---|
> | `direction` | `calls.py:1084` | `inbound` \| `outbound` |
> | `inbound_campaign_id` | `calls.py:1087` | UUID; implies `direction=inbound` |
>
> `inbound_campaign_id` resolves against the pinned route snapshot, not
> `campaign_id` (`calls.py:1128-1130`). **OQ-CH-01 is answered.**
>
> Still absent, verified on the handler signature: no `sort`, no `order`, no
> `search`. **OQ-CH-05 and OQ-CH-10 remain open.**
>
> The line references below are also stale: the list handler is at
> `calls.py:1079` (not 501) and the detail handler at `calls.py:1260`
> (not 608). `CallListItem` carries **31** fields, not the 12 tabulated
> below — including `from_number` (`calls.py:183`), `caller_ani`
> (`calls.py:201`) and `inbound_campaign_id` (`calls.py:203`), which answer
> **OQ-CH-02** and **OQ-CH-08**. `CallDetail` carries **38**, not 13.

There is no `direction` parameter, no `agent` parameter and no `sort`
parameter. **Do not assume one exists.**

**Pagination model and response envelope** — page-based:
`{ items, page, page_size, total }`.

| Field | Type | Nullable |
|---|---|---|
| `items` | `CallListItem[]` | No |
| `page` | integer | No |
| `page_size` | integer | No |
| `total` | integer | No |

**`CallListItem`**

| Field | Type | Nullable | Enum |
|---|---|---|---|
| `id` | string | No | — |
| `talklee_call_id` | string | Yes | — |
| `timestamp` | string | No | — |
| `to_number` | string | No | — |
| `status` | string | No | **None declared** |
| `duration_seconds` | integer | Yes | — |
| `outcome` | string | Yes | **None declared** |
| `campaign_name` | string | Yes | — |
| `summary` | string | Yes | — |
| `recording_id` | string | Yes | — |
| `lead_outcome` | string | Yes | **None declared** |
| `has_feedback` | boolean | No (defaults `false`) | — |

#### CONFIRMED — `GET /calls/{call_id}` (detail)

*Source: `calls.py:608`, `response_model=CallDetail`.*

**Request:** `call_id` path parameter, string. No body, no query parameters.

**Success response — `CallDetail`**

| Field | Type | Nullable | Enum |
|---|---|---|---|
| `id` | string | No | — |
| `talklee_call_id` | string | Yes | — |
| `timestamp` | string | No | — |
| `to_number` | string | No | — |
| `status` | string | No | **None declared** |
| `duration_seconds` | integer | Yes | — |
| `outcome` | string | Yes | **None declared** |
| `transcript` | string | Yes | — |
| `recording_id` | string | Yes | — |
| `campaign_id` | string | Yes | — |
| `lead_id` | string | Yes | — |
| `summary` | string | Yes | — |
| `summary_json` | object | Yes | Contents undeclared |

#### CONFIRMED path, OPEN response — `GET /calls/{call_id}/transcript`

*Source: `calls.py:685`.* Query parameter `format`, string, default `"json"`;
the source documents `"json"` or `"text"` in prose but declares no enum. The
route has **no `response_model`**, so the success payload is **OPEN** —
nothing beyond method, path and that one parameter is defined here
([OQ-CH-06](./OPEN-QUESTIONS.md)).

#### CONFIRMED path, OPEN response — `GET /calls/{call_id}/summary`

*Source: `calls.py:772`.* No query parameters. The route has **no
`response_model`**, so the success payload is **OPEN**
([OQ-CH-07](./OPEN-QUESTIONS.md)).

### 4.3 OPEN endpoints

Nothing is defined for these beyond the fact that they do not yet exist. No
field names, types or enum values are proposed. Each carries an entry in
[OPEN-QUESTIONS.md](./OPEN-QUESTIONS.md).

| Purpose | Status | Open question |
|---|---|---|
| List inbound agents | **OPEN** — no endpoint exists | OQ-CFG-01 |
| Create an inbound agent | **OPEN** — no endpoint exists | OQ-CFG-02 |
| Read one inbound agent | **OPEN** — no endpoint exists | OQ-CFG-03 |
| Status filter options for inbound agents | **OPEN** | OQ-CFG-04 |
| Save / resume a wizard draft server-side | **OPEN** — no endpoint exists; drafts are per-browser today | OQ-CFG-05 |
| Update an existing inbound agent | **OPEN** — out of 1.0.0 scope | OQ-CFG-06 |
| Scope a call list to inbound direction | **OPEN** — `GET /calls/` declares no `direction` parameter | OQ-CH-01 |
| Caller (from) number on a call row | **OPEN** — `CallListItem` declares `to_number` only | OQ-CH-02 |
| Canonical call `status` enum | **OPEN** — declared as bare `string` | OQ-CH-03 |
| Canonical call `outcome` enum | **OPEN** — declared as bare `string` | OQ-CH-04 |
| Server-side sort for the call list | **OPEN** — no `sort` parameter | OQ-CH-05 |
| Transcript response shape | **OPEN** — no `response_model` | OQ-CH-06 |
| Summary response shape | **OPEN** — no `response_model` | OQ-CH-07 |
| Which inbound agent answered a call | **OPEN** — no such field on `CallListItem` | OQ-CH-08 |

### 4.4 Error response shape

Confirmed for every backend call, from the shared HTTP client
(`src/lib/http-client.ts`). Two accepted bodies:

| Body | Fields |
|---|---|
| Envelope (preferred) | `{ "error": { "code": string, "message": string, "details": unknown } }` |
| Legacy FastAPI | `{ "detail": string }` |

The client normalises both to an `ApiClientError` carrying `status`
(HTTP status), `code`, `message` and `details`. When neither body is present,
`code` defaults from the status — `401 → unauthorized`, `403 → forbidden`,
`429 → rate_limited`, `≥500 → server_error`, otherwise `http_error`.

### 4.5 Error-to-state mapping

The Inbound section never switches on an HTTP status directly. The data
module classifies every failure into one of four frontend-owned kinds
(`inbound-types.ts`), and each kind maps to exactly one named state.

| Failure | Error kind | List view | Call history | Wizard step 5 | Either detail panel |
|---|---|---|---|---|---|
| Viewer's role fails the access check | *(no call made)* | `no-permission` | `no-permission` | `no-permission` | not reached |
| `403` / `code: forbidden` | `forbidden` | `no-permission` | `no-permission` | `no-permission` | `no-permission` (one level up) |
| `≥500`, network failure, `code: server_error` | `unavailable` | `error` | `partial-load error` | `save-failed` | n/a |
| List returned incompletely | `partial` | `error` | `partial-load error` | n/a | n/a |
| `404`, or an id that does not resolve | `not-found` | n/a | `row-detail error` | n/a | `error` / `row-detail error` |

`401` is not in this table by design: the shared HTTP client owns session
expiry globally (single-flight refresh, then the session-expired latch) and
the Inbound section must not handle it locally.

---

## 5. Fixtures

Every fixture field is listed as an open question in
[OPEN-QUESTIONS.md §3](./OPEN-QUESTIONS.md), with the endpoint it stands in
for and the decision needed. Two rules keep them from hardening into a
contract, both enforced by
[`structural-inbound-fixture-isolation.test.ts`](../../src/lib/structural-inbound-fixture-isolation.test.ts):

1. Only `inbound-data.ts` may import `inbound-fixtures.ts`.
2. Every fixture field carries an `fx_` prefix, and that token appears
   nowhere else under `src/` — so no component, hook, form or validator can
   parse, validate or branch on a fixture field name.
