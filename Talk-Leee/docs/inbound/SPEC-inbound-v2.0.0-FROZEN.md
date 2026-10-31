# Inbound section, campaign form and call history — requirements spec

| | |
|---|---|
| **Version** | 2.0.0 |
| **Status** | **FROZEN** — 2026-09-01 |
| **Supersedes** | [SPEC-inbound-v1.0.0-FROZEN.md](./SPEC-inbound-v1.0.0-FROZEN.md) |
| **Scope** | Frontend only: the inbound campaign section, its create/edit form, lifecycle controls, and inbound call history. |
| **Contract of record** | [BACKEND-CONTRACT-EXTRACT.md](./BACKEND-CONTRACT-EXTRACT.md) |
| **Companion documents** | [Forms, states and API payloads](./SPEC-inbound-forms-states-api-v2.0.0-FROZEN.md) · [Open questions](./OPEN-QUESTIONS.md) · [Shared-component collisions](./SHARED-COMPONENT-COLLISIONS.md) |

## 0. Freeze notice

This spec is version-frozen at 2.0.0. Anything raised after the freeze goes
to [OPEN-QUESTIONS.md](./OPEN-QUESTIONS.md), not into this document.
Amending it requires a version bump and a re-freeze.

**What changed from 1.0.0, and why.** 1.0.0 was written while the inbound
backend did not exist. It said so, repeatedly, and built a fixture-backed
frontend behind a swappable data module so that no invented field name could
harden into a contract. That discipline worked: when the real backend
arrived, nothing had to be un-invented — only re-mapped.

The backend now exists. 2.0.0 is therefore the opposite kind of document:
**every field, state and payload below is traceable to a backend source line,
or is explicitly marked frontend-only with a reason.**

Explicit non-goals, restated so they cannot be read back in as omissions:
no backend, schema, queue or worker changes; no edits to outbound code; no
telephony; no billing. The platform-admin inbound surface
(`backend/app/api/v1/endpoints/admin/inbound.py`, twelve routes) is real but
**out of scope** — no frontend consumes it.

---

## 1. The backend is the contract

Every statement in this spec about a field, an enum, a limit or a status
transition is copied from source and cited in
[BACKEND-CONTRACT-EXTRACT.md](./BACKEND-CONTRACT-EXTRACT.md). Where this
spec and the backend disagree, **the backend is right and this spec is a
defect.**

Three rules follow, and all three are load-bearing:

1. **`extra="forbid"`.** Every inbound request model derives from
   `_StrictModel` (`schemas/inbound_campaigns.py:16`). A key the model does
   not declare is a 422 before the handler runs. The frontend must send
   exactly the declared keys — no more, and no "harmless" extras.
2. **`Idempotency-Key` is mandatory on every mutation**, 8–255 characters
   (`inbound_campaigns.py:82-90`). There is no default and no fallback.
3. **`expected_version` is mandatory on every update and lifecycle change**
   (`schemas/inbound_campaigns.py:68`, `:105`). Optimistic concurrency is
   not optional, and conflict is a normal outcome rather than an error
   condition — see §6.

---

## 2. Vocabulary

| Term | Meaning |
|---|---|
| **Inbound campaign** | One `inbound_campaign_configs` row: a verified DID bound to one base AI campaign and one inbound-capable SIP trunk, plus its answering behaviour. Identified by a UUID. |
| **Base campaign** | The existing `campaigns` row named by `campaign_id`. Supplies knowledge, the approved persona and the default prompt. Cannot be changed after creation. |
| **DID assignment** | The `inbound_did_assignments` row binding the number to the campaign. Has its own status and version, distinct from the campaign's. |
| **Readiness** | The server's 28-check activation gate. Recomputed on every campaign response. The only authority on whether activation may be offered. |
| **Config version** | `version` — the optimistic-concurrency token. Every mutation must echo the version it expects. |

---

## 3. Navigation entry point and route paths

**Entry point.** One top-level sidebar item, **Inbound**, in the
`navigation` array of `src/components/layout/sidebar.tsx`. Icon
`PhoneIncoming`, target `/inbound-campaigns`, no `adminOnly` flag, no child
rows.

**Exactly one entry.** Between 2026-08-31 and 2026-09-01 the sidebar carried
*two* rows both labelled "Inbound" — one per implementation — which produced
duplicate React keys (`key={item.name}`) and a duplicate `PhoneIncoming`
import that broke both `tsc` and `next build`. The second row was removed on
2026-09-01. **Adding a second inbound nav entry is a regression.**

| Route | Screen |
|---|---|
| `/inbound-campaigns` | Campaign list, plus the tenant inbound admission control |
| `/inbound-campaigns/new` | Create form |
| `/inbound-campaigns/<id>` | Campaign detail: readiness, safety policy, lifecycle actions |
| `/inbound-campaigns/<id>/edit` | Edit form |
| `/calls?direction=inbound` | Inbound call history |
| `/calls?direction=inbound&inbound_campaign_id=<id>` | One campaign's call history |
| `/calls/<id>` | Call detail, with the inbound route snapshot when the call is inbound |

### 3.1 Retired routes

`/inbound`, `/inbound/new`, `/inbound/calls` and `/inbound/<id>/edit` were
removed on 2026-09-01 and redirect to the table above. They were
fixture-backed: they never issued a request, and their create and update
calls returned success while persisting nothing.

`/inbound/<id>/edit` redirects to the **list**, not to an edit page, because
its ids were fixture strings (`cfg-1001`) and `config_id` must be a UUID
(`inbound_campaign_service.py:91`). A per-id redirect would produce a 400.

### 3.2 No `?state=` harness

1.0.0 honoured `?state=<scenario>` on inbound routes to reach any named
state on demand. It was removed with the fixtures and **not replaced**.
Named states are now reached only by exercising the real API. This is a
deliberate loss of reviewability, recorded as OQ-FE-06.

---

## 4. Screen inventory

### 4.1 Campaign list — `/inbound-campaigns`

**Purpose.** Every inbound campaign for the tenant, with lifecycle status,
readiness at a glance, and the tenant-wide admission switch.

**Data.** `GET /inbound-campaigns/` (`inbound_campaigns.py:101`),
`INBOUND_READ`. Envelope `{items, total}`. **Unpaginated** — the only query
parameter is `include_archived`. Search and status filtering are therefore
client-side over the full set, and this is correct rather than a shortcut:
there is no server-side page to request.

**Also on this screen.** The tenant inbound admission control, shown only
with `INBOUND_CONTROLS`. Reads `GET /inbound-campaigns/controls`, writes
`PATCH` with `expected_version` and a **mandatory reason of at least 3
characters** (`schemas/inbound_campaigns.py:121`). This is the tenant
emergency stop: disabling it fails new calls closed before answer, and does
not interrupt calls already in progress.

**Leaves to.** `/inbound-campaigns/new`, `/inbound-campaigns/<id>`.

### 4.2 Create form — `/inbound-campaigns/new`

**Purpose.** Bind one verified DID to one base campaign and one inbound
trunk, and set the answering behaviour.

**Access.** Requires **both** `canCreate` and `canAssignNumber`, mirroring
the backend, which requires both `INBOUND_MANAGE` and `INBOUND_ASSIGN` on
`POST /` (`inbound_campaigns.py:124`, `:128`). A role holding only one — and
`campaign_manager` holds `INBOUND_ASSIGN` without `INBOUND_MANAGE`
(`rbac.py:387-388`) — must not be offered the form.

**Writes.** `POST /inbound-campaigns/` with `Idempotency-Key`. On success,
navigates to the new campaign's detail page.

**Save is not activation.** A created campaign is `draft`
(`0022_inbound_calling_foundation.py:459`) and routes no calls.

### 4.3 Campaign detail — `/inbound-campaigns/<id>`

**Purpose.** The live routing state of one campaign, the server's readiness
verdict, and the lifecycle controls.

**Data.** `GET /inbound-campaigns/{config_id}` plus
`GET /{config_id}/readiness`, both `INBOUND_READ`.

**Readiness is the server's, not the form's.** Activate is offered only when
`readiness.ready` is explicitly `true`. The parser fails closed: a missing or
non-boolean `ready` reads as `false`. **A campaign that looks complete in the
form is not ready; only the server decides** — it checks tenant and platform
admission, DID verification and quarantine, trunk runtime health, billing
quota, concurrency policy, AI provider configuration and more, none of which
the form can see. All 28 checks are listed in the contract extract §8.

**Lifecycle actions**, each gated on `canChangeLifecycle` and on status:

| Action | Offered when | Endpoint |
|---|---|---|
| Activate | `draft` or `paused`, **and** readiness passes | `POST /{id}/activate` |
| Deactivate | `active` | `POST /{id}/deactivate` |
| Archive | `draft` or `paused` | `POST /{id}/archive` |

The archive gate mirrors the server: archiving an `active` campaign is
refused with `pause_before_archive`
(`inbound_campaign_service.py:1655`). Every action carries
`expected_version` and an `Idempotency-Key`, and every one is confirmed
through a dialog that states the consequence for live call routing.

**Leaves to.** Edit, and `/calls?direction=inbound&inbound_campaign_id=<id>`.

### 4.4 Edit form — `/inbound-campaigns/<id>/edit`

**Purpose.** Change an inactive campaign's configuration without silently
changing live routing.

**Blocked, not hidden, for `active` and `archived`.** The screen explains
that the campaign must be deactivated first. This mirrors `pause_before_edit`
(`inbound_campaign_service.py:1405`) and `campaign_archived` (`:1410`)
rather than discovering them as a 409 after the user has typed.

**The base campaign is locked.** `campaign_id` cannot change
(`campaign_change_forbidden`, `:1452`), so the control is disabled.

**Writes, in order.** `PUT /{config_id}` with `expected_version`. Then, only
if the DID or trunk changed **and** the user holds `canAssignNumber`,
`POST /{config_id}/assign` with the version returned by the update. The two
calls are separate because sending a changed `did_number` or `sip_trunk_id`
on the update route is refused with `assignment_workflow_required`
(`:1445`).

### 4.5 Inbound call history — `/calls?direction=inbound`

**Purpose.** Calls that arrived at the tenant's inbound campaigns.

**Not a separate screen.** 1.0.0 built `/inbound/calls` as its own view
specifically to avoid generalising `/calls` (collision COL-07). That decision
was correct when `GET /calls/` had no `direction` parameter. It now has one
(`calls.py:1084`), so the separate screen was removed and the shared screen
generalised. **This reverses COL-07 deliberately.**

**Data.** `GET /calls/` with `direction=inbound`, optionally
`inbound_campaign_id`. Pages with `page` ≥ 1 and `page_size` 1–100,
default 20.

**Two documented limitations.** `GET /calls/` has no `sort` and no `search`
parameter. Column sorting and the search box therefore act on the current
page only. The UI must not imply otherwise. OQ-CH-05, OQ-CH-10.

### 4.6 Call detail — `/calls/<id>`

Direction-aware. For an inbound call it additionally shows the route
snapshot pinned at admission — inbound campaign, assignment, route version,
config version and checksum — and the admission, consent, processing, media,
recording and transcript states.

**Party projection is the server's.** Caller ANI is exposed only when the
carrier did not mark it private (`calls.py:43-44`), and recording state is
suppressed to `disabled` when the pinned config or the tenant control
forbids it (`calls.py:131-142`). The frontend renders what it is given and
must not reconstruct a caller number from another field.

---

## 5. Named states

Eighteen states carried unchanged from 1.0.0, plus five added in 2.0.0 for
failure modes the real backend produces and the original taxonomy could not
name. The executable copy is `src/lib/inbound/inbound-types.ts`, and a test
asserts the two lists are identical in both directions.

### 5.1 Section states (5)

`loading` · `empty` · `populated` · `error` · `no-permission`

### 5.2 Call-history states (7)

`loading` · `empty` · `empty-after-filter` · `populated` ·
`partial-load-error` · `row-detail-loading` · `row-detail-error`

### 5.3 Form step states (6)

`idle` · `validating` · `blocked-by-validation` · `saving` · `save-failed` ·
`complete`

### 5.4 Added in 2.0.0 (5)

| State | Trigger | Why 1.0.0 could not express it |
|---|---|---|
| `conflict` | HTTP 409/412 | The 1.0.0 taxonomy had four kinds — `forbidden`, `unavailable`, `partial`, `not-found` — and none of them is conflict. Optimistic concurrency is this backend's primary interaction model; 22 distinct 409 codes exist. |
| `rejected-by-server` | HTTP 422 with a field-level code | Server-side field rejection was indistinguishable from a generic failure. |
| `activation-blocked` | `readiness.ready === false` | Readiness did not exist in 1.0.0; activation was out of scope. |
| `authorization-unavailable` | HTTP 503 `authorization_unavailable` | The permission lookup can fail *without* the user lacking permission (`inbound_campaigns.py:68`). Rendering that as `no-permission` tells the user something false. |
| `retry-window-expired` | Idempotency key older than the server's 24h claim window | Idempotency did not exist in 1.0.0. Replaying a stale key risks a duplicate create. |

### 5.5 Canonical state register

The list below is **machine-read** by
`src/lib/inbound/inbound-state-parity.test.ts`, which asserts in both
directions that it matches the unions in
`src/lib/inbound/inbound-types.ts` — every state here is declared in code,
and every state declared in code appears here. Editing one without the other
fails the suite.

Format: one `union-name: state-value` per line. Do not reformat this block.

```state-register
InboundSectionState: loading
InboundSectionState: empty
InboundSectionState: populated
InboundSectionState: error
InboundSectionState: no-permission
CallHistoryState: loading
CallHistoryState: empty
CallHistoryState: empty-after-filter
CallHistoryState: populated
CallHistoryState: partial-load-error
CallHistoryState: row-detail-loading
CallHistoryState: row-detail-error
WizardStepState: idle
WizardStepState: validating
WizardStepState: blocked-by-validation
WizardStepState: saving
WizardStepState: save-failed
WizardStepState: complete
InboundOperationState: conflict
InboundOperationState: rejected-by-server
InboundOperationState: activation-blocked
InboundOperationState: authorization-unavailable
InboundOperationState: retry-window-expired
```

**23 states: 18 carried from 1.0.0, 5 added at 2.0.0.**

---

## 6. Conflict is normal

The single largest behavioural difference from 1.0.0. Because every mutation
carries `expected_version`, any campaign changed in another session or tab
returns 409 rather than silently overwriting.

**Required handling.** A conflict is not an error toast. It must:
1. render as `conflict`, not as a generic failure;
2. say that the campaign changed elsewhere, in those words;
3. offer to reload the current server state;
4. never retry automatically — the user must see what changed before
   re-applying.

**Idempotency is the other half.** A mutation that fails ambiguously — a
timeout, a disconnect, a gateway response, a 5xx — may already have
committed. Such a retry must replay the **original** `Idempotency-Key`. A
retry with a fresh key after a commit creates a duplicate campaign and
consumes a DID that only permits one live assignment
(`0022_inbound_calling_foundation.py:563`).

---

## 7. Permissions

**The server's effective permission set is the only authority.** Capabilities
are derived from `GET /rbac/users/me/permissions`
(`rbac/users.py:32`), never from a display role string.

| Capability | Granted by |
|---|---|
| View | `inbound:read`, or `inbound:manage`, or `platform:admin` |
| Create / Edit / Lifecycle | `inbound:manage` or `platform:admin` |
| Assign number | `inbound:assign`, or `inbound:manage`, or `platform:admin` |
| Tenant controls | `inbound:controls` or `platform:admin` |

**Fails closed.** When the lookup fails or has not resolved, every capability
is `false`. A failed lookup renders `authorization-unavailable`, not
`no-permission`.

**Why a role string is not enough.** `readonly`, `user` and `agent` all hold
`inbound:read` only (`rbac.py:297`, `:310`, `:337`). `campaign_manager` holds
`inbound:read` and `inbound:assign` but **not** `inbound:manage`
(`rbac.py:387-388`) — so it can assign a number but cannot create a campaign,
because create requires both. No role-name heuristic reproduces that.

---

## 8. Transfer is unavailable by default

Inbound transfer is gated behind two independent server switches, both of
which must be explicitly `true`:
`GET /inbound-campaigns/capabilities` returns
`transfer_runtime_available`, `transfer_platform_enabled` and
`transfer_configuration_available` (`schemas/inbound_campaigns.py:198`).

The frontend must treat transfer as **unavailable unless all gates are
explicitly true**, re-check at submit time rather than trusting a cached
value, and surface the server's own refusal codes —
`transfer_runtime_unavailable`, `transfer_platform_disabled`,
`transfer_staging_scope_mismatch` — rather than a generic message.

The staging proof window is environment-gated
(`backend/.env.example:155-160`) and production startup refuses it.

---

## 9. Where each item lives

| Concern | Module |
|---|---|
| HTTP client, parsers, idempotency | `src/lib/inbound-api.ts` |
| Query and mutation layer | `src/lib/queries/inbound-queries.ts` |
| Capabilities | `src/lib/inbound-permissions.ts` |
| Form validation and defaults | `src/lib/inbound-validation.ts` |
| Named states and error taxonomy | `src/lib/inbound/inbound-types.ts` |
| Display formatting | `src/lib/inbound/inbound-format.ts` |
| Draft persistence | `src/lib/inbound/inbound-draft-store.ts` |
| Reusable field components | `src/components/inbound/fields/` |
| Campaign form | `src/components/inbound/inbound-campaign-form.tsx` |
| Page states | `src/components/inbound/inbound-page-state.tsx` |
| Status and readiness display | `src/components/inbound/inbound-status.tsx` |
| Routes | `src/app/inbound-campaigns/` |

> `src/lib/queries/inbound-queries.ts` also exports `useEffectivePermissions`,
> which five **non-inbound** screens import — `/calls`, `/calls/[id]`,
> `/recordings`, `live-calls-panel`, `conversation-review-panel`. It is
> shared infrastructure and must not be removed as if it were inbound-only.
