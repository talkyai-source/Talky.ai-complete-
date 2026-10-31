# Inbound — form fields, states and API payloads

| | |
|---|---|
| **Version** | 2.0.0 |
| **Status** | **FROZEN** — 2026-09-01 |
| **Supersedes** | [SPEC-inbound-forms-states-api-v1.0.0-FROZEN.md](./SPEC-inbound-forms-states-api-v1.0.0-FROZEN.md) |
| **Companion to** | [SPEC-inbound-v2.0.0-FROZEN.md](./SPEC-inbound-v2.0.0-FROZEN.md) |
| **Contract of record** | [BACKEND-CONTRACT-EXTRACT.md](./BACKEND-CONTRACT-EXTRACT.md) |
| **Also see** | [Open questions](./OPEN-QUESTIONS.md) · [Shared-component collisions](./SHARED-COMPONENT-COLLISIONS.md) |

## 1. How to read this document

- **§2** — one row per field, eight attributes, no blanks. Every row names
  its **backend destination** or is marked frontend-only with a reason.
- **§3** — one row per named state, with trigger, output, controls and exit.
- **§4** — API contracts, taken from source.
- **§5** — the error-to-state map: every backend code, exactly one state.

**The field-key convention changed at 2.0.0.** In 1.0.0 every key was a
frontend-owned name because no contract existed
(`configName`, `agentDisplayName`, `outOfHoursMessage`). In 2.0.0 the keys
**are the backend's own names**, so the form state and the request body
cannot drift. Where a value nests inside `business_hours`,
`transfer_policy`, `recording_policy` or `qualification_config`, the table
says so explicitly.

### 1.1 Two rules that break a build if ignored

**`extra="forbid"`.** Every inbound request model derives from
`_StrictModel` (`schemas/inbound_campaigns.py:16`). **A key the model does
not declare is a 422 before the handler runs.** Adding a field to the form is
not enough — it must exist on `InboundCampaignCreateRequest`
(`schemas/inbound_campaigns.py:27`) or `InboundCampaignUpdateRequest`
(`:67`), or the whole request fails. This is why §2 marks frontend-only
fields explicitly: they must be *stripped before send*, not passed through.

**`Idempotency-Key`.** Mandatory on every mutation, 8–255 characters
(`inbound_campaigns.py:82-90`). A mutation without it is a 400
`invalid_idempotency_key` raised by the dependency, before the handler. There
is no default.

---

## 2. Field tables

### 2.1 Campaign form — routing

| Field key | Label | Input | Required | Default | Constraint | Message | Disabled/hidden when |
|---|---|---|---|---|---|---|---|
| `name` | Campaign name | Text | **Required** | `""` | 1–255 after trim (`schemas:28`); stripped server-side (`:53`) | "Enter a name for this inbound campaign." | Disabled while pending |
| `campaign_id` | AI campaign | Select | **Required** | `""` | UUID of an eligible base campaign | "Choose the AI campaign that should answer." | **Disabled in edit mode** — `campaign_change_forbidden` (`service:1452`) |
| `did_number` | Verified public phone number | Radio list | **Required** | `""` | Strict E.164 `^\+[1-9]\d{6,14}$` (`schemas:13`) | "Choose a verified phone number." | Disabled without `canAssignNumber` |
| `sip_trunk_id` | Inbound SIP trunk | Select | **Required** | `""` | UUID; trunk must be `inbound`/`both`, active, runtime-ready | "Choose an inbound-capable SIP trunk." | Disabled in edit without `canAssignNumber` |
| `timezone` | Business timezone | Select | **Required** | browser zone, else `"UTC"` | 1–64 (`schemas:32`); readiness `timezone_valid` (`service:712`) | "Choose a timezone." | Disabled while pending |

**Eligibility is filtered, not merely validated.** A base campaign qualifies
if its status is one of `active`/`running`/`draft`/`paused`/`stopped` **and**
it is already `inbound`, or is an `outbound` **draft** the server may convert
atomically. The server holds final authority and refuses a campaign with any
outbound activity — `campaign_direction_conflict` (`service:1222`). A trunk
qualifies only when `is_active`, `runtime_ready`, and directional.

**The DID list is double-checked.** Assignment truth lives in
`inbound_did_assignments`, not in mutable DID metadata. Every verified number
from `GET /tenant-phone-numbers/` is re-checked against
`GET /inbound-campaigns/dids/availability` before being offered.

> **The number is carried under the key `e164`**
> (`domain/models/tenant_phone_number.py:38`). There is no `masked_number`,
> no `display_number`, no `phone_number` and no `number` field on that model.
> A masking helper that does not read `e164` renders nothing — the defect
> fixed at Phase D-1.

### 2.2 Campaign form — AI behaviour

All four nest inside **`qualification_config`**, the only whitelisted blob.
Supported keys are exactly `purpose`, `persona`, `system_prompt`, `voice_id`,
`silence_timeout_seconds` (`inbound_overrides.py:8-14`); anything else is
reported by readiness `qualification_runtime_supported` (`service:918`).

| Field key | Label | Input | Required | Default | Constraint | Message | Disabled/hidden when |
|---|---|---|---|---|---|---|---|
| `purpose` | Inbound call purpose | Text | Optional | `""` | ≤2000 (`overrides:9`) | *(none — server-checked)* | Disabled while pending |
| `agent_persona` → `persona` | Inbound agent style | Text | Optional | `""` | ≤2000 (`overrides:10`) | *(none)* | Disabled while pending |
| `system_prompt` | System prompt override | Textarea | Optional | `""` | ≤20000 (`overrides:11`) | *(none)* | Disabled while pending |
| `voice_id` | Voice ID override | Text | Optional | `""` | ≤255 (`overrides:12`) | *(none)* | Disabled while pending |

**Blank means inherit.** A neutral value — `null`, `""`, whitespace, `[]`,
`{}` — is dropped by `_neutral` (`overrides:17-21`) and never stored. The
base campaign's value applies.

**Overrides layer, they do not replace.** `system_prompt` is **appended** to
the approved base instructions (`overrides:98`); `purpose` becomes a prefixed
`INBOUND CALL PURPOSE` block and sets `goal` (`overrides:89-93`); `persona`
becomes a prefixed `INBOUND AGENT STYLE` block (`overrides:94-97`); only
`voice_id` replaces outright (`overrides:86-87`).

**There is no knowledge field.** Knowledge is inherited from the base
campaign named by `campaign_id`. `qualification_config` accepts no knowledge
key of any kind — OQ-CFG-07, OQ-CFG-14, decision D3.

### 2.3 Campaign form — opening and turn-taking

| Field key | Label | Input | Required | Default | Constraint | Message | Disabled/hidden when |
|---|---|---|---|---|---|---|---|
| `opening_mode` | Opening mode | Select | **Required** | `"caller_first"` | `caller_first` \| `agent_first` (`schemas:37`) | *(none — always valid)* | Disabled while pending |
| `greeting` | Opening greeting | Textarea | **Conditional** — required when `opening_mode = "agent_first"` | `""` | ≤2000 (`schemas:38`) | "Add the greeting the agent should play." | Disabled while pending |
| `silence_timeout_seconds` | Opening silence timeout | Number | **Required** | `8` | Integer 3–60 (`overrides:58`) | "Silence timeout must be a whole number between 3 and 60 seconds." | Disabled while pending |

> **`greeting` is ≤2000, not ≤300.** The 1.0.0 limit of 10–300 was a
> frontend invention; the contract allows 2000. And requiredness is
> *conditional*, not absolute: a `caller_first` campaign needs no greeting.

> **`silence_timeout_seconds` is sent only when it differs from 8.** The
> server treats exactly `8.0` as neutral and discards it
> (`overrides:22-24`), so transmitting the default is wasted payload.

### 2.4 Campaign form — business hours

Nest inside **`business_hours`**, an opaque `dict[str, Any]`
(`schemas:39`). **Nothing validates its shape at write time** — it is stored
as `jsonb` and echoed back verbatim. The frontend owns this shape; it is
checked only at activation.

| Field key | Label | Input | Required | Default | Constraint | Message | Disabled/hidden when |
|---|---|---|---|---|---|---|---|
| `weekly_schedule` | Business hours | 7-day grid | **Required** | Mon–Fri 09:00–17:00 enabled | Each enabled day: `HH:MM` windows, start ≠ end; readiness `business_hours_valid` (`service:722`) | "Each open business-hours window needs distinct valid start and end times." | Disabled while pending |
| `holiday_policy` | Holiday policy | Select | **Required** | `"closed"` | `closed` \| `regular_hours` | *(none)* | Disabled while pending |
| `after_hours_message` | AI intake opening message | Textarea | **Conditional** — required when `after_hours_action = "voicemail"` | `""` | Non-empty when required; readiness `after_hours_message_valid` (`service:739`) | "Add the opening message the AI will use for after-hours intake." | **Hidden** unless `after_hours_action = "voicemail"` |

**Day indexing is frontend-owned and unverified.** `weekly_schedule[].day` is
an integer 0–6. The backend does not interpret it at write time, and no
source states whether `0` is Monday or Sunday. The current default enables
`day < 5` and the UI labels Monday-first. **This is an assumption, recorded
here so it is not mistaken for a contract**, and it must be confirmed against
the runtime scheduler before business hours are relied on.

### 2.5 Campaign form — after-hours and transfer

| Field key | Label | Input | Required | Default | Constraint | Message | Disabled/hidden when |
|---|---|---|---|---|---|---|---|
| `after_hours_action` | After-hours action | Select | **Required** | `"hangup"` | `hangup` \| `voicemail` \| `transfer` (`schemas:33`) | "Choose reject or AI message intake. Inbound transfer remains blocked until the runtime and platform capability gates are enabled." | `transfer` selectable only when all capability gates are true |
| `transfer_number` | After-hours transfer destination | Text | **Conditional** — required when `after_hours_action = "transfer"` (`schemas:60-61`) | `""` | E.164, ≤32 (`schemas:34`) | "Enter the transfer destination in E.164 format." | **Hidden** unless action is `transfer` |
| `transfer_enabled` | Live transfer | Checkbox | Optional | `false` | Requires all capability gates | "Disable live transfer until linked-leg ownership, hard-cap teardown, carrier behavior, settlement, and both server gates are verified." | Disabled unless gates pass |
| `transfer_destinations` | Approved transfer destinations | Textarea | **Conditional** — required when `transfer_enabled` | `[]` | ≥1 entry, each E.164 | "Add at least one approved E.164 transfer destination." | Hidden unless `transfer_enabled` |
| `transfer_failure_action` | Failure action | Select | Optional | `"voicemail"` | `voicemail` \| `return_to_agent` \| `hangup` | *(none)* | Hidden unless `transfer_enabled` |
| `max_transfer_attempts` | Maximum attempts | Number | Optional | `2` | Integer 1–5 | "Transfer attempts must be a whole number between 1 and 5." | Hidden unless `transfer_enabled` |
| `max_transfer_hops` | Maximum hops | Number | Optional | `2` | Integer 1–5 | "Transfer hops must be a whole number between 1 and 5." | Hidden unless `transfer_enabled` |
| `max_call_duration_seconds` | Max call seconds | Number | Optional | `1800` | Integer 60–14400; readiness `max_call_duration_valid` (`service:873`) | "Maximum call duration must be a whole number between 60 and 14,400 seconds." | Hidden unless `transfer_enabled` |

`transfer_number` is a **top-level** contract field (`schemas:34`); the rest
nest inside the opaque `transfer_policy` blob (`schemas:41`).

**The 1–5 and 60–14400 bounds are frontend-only.** They live inside
`transfer_policy`, which nothing validates at write time. Only
`max_call_duration_valid` is re-checked, at readiness. A value outside these
bounds is accepted by the API and surfaces later as an activation blocker.

**Transfer fails closed.** Both server gates must be explicitly `true`, and
the capability is re-fetched at submit time rather than trusted from cache.

### 2.6 Campaign form — recording

| Field key | Label | Input | Required | Default | Constraint | Message | Disabled/hidden when |
|---|---|---|---|---|---|---|---|
| `recording_enabled` | Recording | Checkbox | Optional | `false` | Boolean (`schemas:35`) | *(none)* | Disabled while pending |
| `consent_message` | Recording disclosure | Textarea | **Conditional** — required when `recording_enabled` (`schemas:62-63`) | `""` | ≤2000 (`schemas:36`); readiness `recording_consent` (`service:886`) | "Add the disclosure callers must hear before recording." | **Hidden** unless `recording_enabled` |

Both are sent twice: as top-level fields, and inside the `recording_policy`
blob. The top-level pair is what the schema validates; the blob is what
readiness inspects.

### 2.7 Frontend-only fields

Not on any request model. **Must be stripped before send** — `extra="forbid"`
would reject them.

| Field key | Purpose | Why it is not sent |
|---|---|---|
| `expected_version` | Optimistic concurrency | Sent on update and lifecycle, **not** on create — create has no prior version |
| `allowed_tools` | Reserved | No contract field; deliberately never transmitted |
| `knowledge_base_id` | Vestigial | Initialised to `""`, never rendered, never sent. Retained only to avoid a type change; carries no meaning |

### 2.8 Call-history filter

Against `GET /calls/` (`calls.py:1079`).

| Field key | Label | Input | Required | Default | Constraint | Message | Disabled/hidden when |
|---|---|---|---|---|---|---|---|
| `direction` | Direction | Segmented | Optional | `"all"` | `all` (client sentinel) \| `inbound` \| `outbound` (`calls.py:1084`) | *(none)* | Disabled while loading |
| `inbound_campaign_id` | Campaign | URL param | Optional | — | UUID; implies `direction=inbound` (`calls.py:1087`) | *(none)* | Set by deep link only |
| `status` | Status | Select | Optional | `"all"` | `all` + runtime options. **No enum published** — OQ-CH-03 | *(none)* | Disabled while loading |
| `from` | From | Date | Optional | `""` | `YYYY-MM-DD` (`calls.py:1091`) | "Enter a start date on or before the end date." | Disabled while loading |
| `to` | To | Date | Optional | `""` | `YYYY-MM-DD` (`calls.py:1092`) | "Enter an end date on or after the start date." | Disabled while loading |
| `page` | — | — | Optional | `1` | ≥1 (`calls.py:1081`) | — | — |
| `page_size` | — | — | Optional | `20` | 1–100 (`calls.py:1082`) | — | — |
| `search` | Search | Search text | Optional | `""` | **Client-side only** | *(none)* | Disabled while loading |

> **`search` and column sort are page-local.** `GET /calls/` declares no
> `search`, `sort` or `order` parameter — verified on the handler signature.
> The UI must state this rather than imply a full-set search. OQ-CH-05,
> OQ-CH-10.

**No status value may be hard-coded.** The enum is unpublished (OQ-CH-03);
options stay runtime-supplied.

---

## 3. State tables

### 3.1 Campaign list — `/inbound-campaigns`

| State | Trigger | Visible output | Enabled controls | Exit |
|---|---|---|---|---|
| `loading` | Permissions or list in flight | Skeleton | none | Resolution |
| `no-permission` | Permissions resolved, no `canView` | 403 panel naming the capability | none | Permission change |
| `authorization-unavailable` | Permission lookup failed (503) | Error naming it as unverified, **not** as denied | Retry | Retry |
| `error` | List request failed | Message + retry | Retry | Retry |
| `empty` | `items.length === 0` | Empty panel | Create, when permitted | Create |
| `empty-after-filter` | Rows exist, none match | "No campaigns match" | Clear filters | Clear |
| `populated` | ≥1 matching row | Card grid | Open, Edit, filters, controls | Navigation |

### 3.2 Create and edit form

| State | Trigger | Visible output | Enabled controls | Exit |
|---|---|---|---|---|
| `idle` | Mounted, resolved | Form | All | Submit |
| `validating` | Submit pressed | Capability re-fetch when transfer requested | none | Verdict |
| `blocked-by-validation` | Client errors | Focused error summary, per-field messages | All | Correction |
| `saving` | Request in flight | Pending label | none | Settle |
| `rejected-by-server` | 422 with field code | Error summary carrying the server's message | All | Correction |
| `conflict` | 409/412 | "Changed in another session", **plus a reload control** | Reload | Reload |
| `save-failed` | Other failure | Error summary | All | Retry |
| `complete` | 2xx | Navigate to detail | — | — |

**Edit adds two pre-states**, both rendered instead of the form so the user
never types into a form that cannot save:

| State | Trigger | Output |
|---|---|---|
| Deactivate-first | `status === "active"` | "Deactivate before editing" — mirrors `pause_before_edit` (`service:1405`) |
| Read-only | `status === "archived"` | "Archived campaigns are read-only" — mirrors `campaign_archived` (`service:1410`) |

### 3.3 Campaign detail

| State | Trigger | Visible output | Enabled controls | Exit |
|---|---|---|---|---|
| `loading` | Campaign or readiness in flight | Skeleton | none | Resolution |
| `no-permission` | No `canView` | 403 panel | none | — |
| `error` | 404, or load failure | "Not found" or "unavailable" + retry | Retry | Retry |
| `populated` | Loaded | Overview, readiness, safety, versions | Per §7 below | Action |
| `activation-blocked` | `readiness.ready === false` | Blocker list with per-blocker remediation | **Activate disabled** | Fix + refresh |
| `conflict` | Lifecycle 409 | "Changed elsewhere; refresh before changing live routing" | Refresh | Refresh |

### 3.4 Call history and call detail

Seven states unchanged from 1.0.0: `loading`, `empty`, `empty-after-filter`,
`populated`, `partial-load-error`, `row-detail-loading`, `row-detail-error`.

### 3.5 The tenant admission control

| State | Trigger | Output | Controls |
|---|---|---|---|
| `loading` | Controls in flight | "Loading the authoritative switch…" | none |
| `error` | Load failed | "Could not be verified, so it cannot be changed" | none — **fails closed** |
| `populated` | Loaded | Current state + last reason | Toggle, behind a confirm dialog requiring a reason of ≥3 characters |

---

## 4. API

### 4.1 What the inbound screens call

Everything below is live. **No screen reads placeholder data.** The fixture
data module, its scenario harness and the `/inbound` routes it backed were
removed on 2026-09-01.

### 4.2 Campaign endpoints

Prefix `/inbound-campaigns` (`inbound_campaigns.py:41`), registered at
`routes.py:83`.

| Operation | Method + path | Line | Permission | Idem-Key | Body | Response |
|---|---|---|---|---|---|---|
| List | `GET /` | 101 | `INBOUND_READ` | — | — | `{items, total}` |
| Create | `POST /` | 120 | `INBOUND_MANAGE` **+** `INBOUND_ASSIGN` | **yes** | `InboundCampaignCreateRequest` | `InboundCampaignResponse`, 201 |
| Read | `GET /{config_id}` | 209 | `INBOUND_READ` | — | — | `InboundCampaignResponse` |
| Update | `PUT /{config_id}` | 270 | `INBOUND_MANAGE` | **yes** | `InboundCampaignUpdateRequest` | `InboundCampaignResponse` |
| Assign | `POST /{config_id}/assign` | 283 | `INBOUND_ASSIGN` | **yes** | `InboundDidAssignmentRequest` | `InboundCampaignResponse` |
| Readiness | `GET /{config_id}/readiness` | 223 | `INBOUND_READ` | — | — | `InboundReadiness` |
| Activate | `POST /{config_id}/activate` | 331 | `INBOUND_MANAGE` | **yes** | `InboundVersionRequest` | `InboundCampaignResponse` |
| Deactivate | `POST /{config_id}/deactivate` | 350 | `INBOUND_MANAGE` | **yes** | `InboundVersionRequest` | `InboundCampaignResponse` |
| Archive | `POST /{config_id}/archive` | 368 | `INBOUND_MANAGE` | **yes** | `InboundVersionRequest` | `InboundCampaignResponse` |
| DID availability | `GET /dids/availability` | 145 | `INBOUND_READ` | — | `?did_number=` | `InboundDidAvailabilityResponse` |
| Controls read | `GET /controls` | 159 | `INBOUND_CONTROLS` | — | — | `TenantInboundControlsResponse` |
| Controls write | `PATCH /controls` | 170 | `INBOUND_CONTROLS` | **yes** | `TenantInboundControlsPatch` | `TenantInboundControlsResponse` |
| Capabilities | `GET /capabilities` | 191 | `INBOUND_READ` | — | `?config_id=` | `InboundRuntimeCapabilitiesResponse` |

Four constraints, each of which is a real failure mode:

1. **`{config_id}` must be a UUID** (`service:91`), else 400
   `invalid_identifier`.
2. **`/dids/availability`, `/controls`, `/capabilities` are literal paths
   registered before `/{config_id}`** (`inbound_campaigns.py:144`). Never
   treat them as ids.
3. **`PUT` must omit `did_number` and `sip_trunk_id` when they change.**
   Routing changes go through `POST /assign`, else 409
   `assignment_workflow_required` (`service:1445`).
4. **`PATCH /controls` requires a reason of ≥3 characters**
   (`schemas:121`) — the only `reason` in this API that is mandatory.

**Create body — exactly these 15 keys, no others** (`schemas:27-42`):
`name`, `did_number`, `campaign_id`, `sip_trunk_id`, `timezone`,
`after_hours_action`, `transfer_number`, `recording_enabled`,
`consent_message`, `opening_mode`, `greeting`, `business_hours`,
`recording_policy`, `transfer_policy`, `qualification_config`.

**Update body** — any of the above as optional, plus mandatory
`expected_version` (≥1) and optional `reason` (≤1000).
`model_dump(exclude_unset=True)` (`inbound_campaigns.py:251`) means an
omitted key is *not sent*, not *set to null*.

**Response** — 31 fields (`schemas:143-175`). Two traps:
`did_number` is the **raw** E.164, never masked — `mask_did` exists at
`service:201` but `_serialize_bundle` does not call it, so masking is the
frontend's job. And there is **no `phone_number` object**: the DID arrives
flat as `did_number` + `assignment_id` + `assignment_status` +
`assignment_version`.

**Readiness** — `{ready, checks[], blockers[]}` (`schemas:137-140`).
**No `checked_at` field exists.** `ready` must be read fail-closed: a missing
or non-boolean value is `false`.

### 4.3 Verified DID inventory

`GET /tenant-phone-numbers/` (`tenant_phone_numbers.py:111`) — a **bare JSON
array**, not an envelope. The number is under **`e164`**
(`domain/models/tenant_phone_number.py:38`); `status` must be `"verified"`
(`:16`).

### 4.4 Call endpoints

| Operation | Method + path | Line | Response |
|---|---|---|---|
| Inbound call list | `GET /calls/?direction=inbound[&inbound_campaign_id=]` | 1079 | `CallListResponse` |
| Call detail | `GET /calls/{call_id}` | 1260 | `CallDetail`, 38 fields |
| Rejected inbound | `GET /calls/rejected` | 780 | `RejectedInboundCallsResponse` |
| Transcript | `GET /calls/{call_id}/transcript` | 1438 | **no `response_model` — OQ-CH-06** |
| Summary | `GET /calls/{call_id}/summary` | 1526 | **no `response_model` — OQ-CH-07** |

`CallListItem` carries 31 fields (`calls.py:177`), including `from_number`
(183), `direction` (200), `caller_ani` (201), `called_did` (202) and
`inbound_campaign_id` (203). `status` (185) and `outcome` (187) remain bare
strings — OQ-CH-03, OQ-CH-04.

### 4.5 Effective permissions

`GET /rbac/users/me/permissions` (`rbac/users.py:32`) →
`{user_id, tenant_id, permissions[], role, grant_type}`. **The only
authority on capability.** Fails closed.

### 4.6 Error envelope

`{"code": ..., "message": ...}` inside FastAPI's `detail`
(`inbound_campaigns.py:95`), with `readiness` added for
`InboundReadinessError` (`:96-97`). The shared HTTP client also accepts the
legacy `{"detail": string}` form and normalises both to an `ApiClientError`
carrying `status`, `code`, `message`, `details`.

---

## 5. Error-to-state map

Every code the tenant-facing surface can emit, mapped to exactly one state.
**No code maps to two states, and no code is unmapped.**

### 5.1 By status

| Status | Code(s) | State |
|---|---|---|
| **403** | `permission_denied` | `no-permission` |
| **503** | `authorization_unavailable` | `authorization-unavailable` |
| **404** | `not_found` | `error` / `row-detail-error` |
| **409** | `not_ready` | `activation-blocked` |
| **409** | `version_conflict`, `did_assignment_conflict`, `pause_before_edit`, `pause_before_archive`, `campaign_archived`, `config_already_exists`, `campaign_direction_conflict`, `assignment_quarantined`, `assignment_archived`, `assignment_state_conflict`, `assignment_workflow_required`, `campaign_change_forbidden`, `campaign_not_inbound`, `campaign_terminal`, `idempotency_race`, `idempotency_mismatch`, `idempotency_in_progress`, `inbound_runtime_restart_required` | `conflict` |
| **422** | `invalid_did`, `invalid_name`, `incomplete_after_hours`, `missing_recording_consent`, `expected_version_required` | `rejected-by-server` |
| **400** | `invalid_identifier`, `invalid_timezone`, `invalid_status`, `invalid_idempotency_key`, `controls_missing`, `did_not_verified`, `trunk_not_ready` | `rejected-by-server` |
| **400** | `transfer_runtime_unavailable`, `transfer_platform_disabled`, `transfer_staging_scope_mismatch` | `rejected-by-server`, rendered with the server's own message |
| **≥500**, network failure | — | `error` / `save-failed` |
| **client-side** | Retry window elapsed | `retry-window-expired` |

### 5.2 Rules

**`401` is absent by design.** The shared HTTP client owns session expiry
globally — single-flight refresh, then the session-expired latch. The inbound
section must not handle it locally.

**403 and 503 must not be conflated.** 403 means the user lacks the
capability. 503 `authorization_unavailable` means the *lookup* failed
(`inbound_campaigns.py:60-70`). Rendering the second as `no-permission` tells
the user something false about their own access.

**`not_ready` is not a generic conflict.** It carries a `readiness` payload
(`inbound_campaigns.py:96-97`) whose blockers must be rendered individually
with their remediation text.

**Conflict never auto-retries.** The user must see what changed before
re-applying.

**Ambiguous failures replay the original key.** A timeout, disconnect,
gateway response or 5xx may have committed server-side. A retry must reuse
the original `Idempotency-Key`; a fresh key after a commit creates a
duplicate campaign and consumes a DID that permits only one live assignment
(`0022_inbound_calling_foundation.py:563`).

---

## 6. No placeholder data

**There is none, anywhere, and none may be added.**

The 1.0.0 fixtures, the `INBOUND_DATA_SOURCE` switch, the artificial 350 ms
latency, the never-resolving `loading` promise, the `?state=` scenario
harness and the create/update calls that returned success while persisting
nothing were all removed on 2026-09-01, together with the `/inbound` routes
they backed.

The 1.0.0 containment rules — the `fx_` prefix, the single-importer rule and
the structural test enforcing both — went with them. They existed to stop
fixtures hardening into a contract. With no fixtures, there is nothing to
contain.

**The replacement rule is simpler and stricter: every value a screen renders
comes from a backend response, or from a documented frontend-only field
listed in §2.7.** A hard-coded response, a fake delay, a placeholder row or a
mock transport reintroduces exactly the failure mode this version removed —
a user completing a flow that silently persists nothing.
