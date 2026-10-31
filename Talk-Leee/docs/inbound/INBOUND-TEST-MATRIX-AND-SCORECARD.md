# Inbound — test matrix and acceptance scorecard

| | |
|---|---|
| **Version** | 2.0.2 |
| **Created** | 2026-08-31 · **re-based 2026-09-01** · **revised 2026-09-02, 2026-09-03** |
| **Covers** | [SPEC-inbound-v2.0.0-FROZEN.md](./SPEC-inbound-v2.0.0-FROZEN.md) · [SPEC-inbound-forms-states-api-v2.0.0-FROZEN.md](./SPEC-inbound-forms-states-api-v2.0.0-FROZEN.md) |
| **Contract of record** | [BACKEND-CONTRACT-EXTRACT.md](./BACKEND-CONTRACT-EXTRACT.md) |
| **Scope** | Frontend only. The `/inbound-campaigns` surface and inbound call history. |
| **Status** | **UNSCORED.** Every verdict below is blank by design — scored on the live staging pass, not from a desk. |

## Why this document was re-based

The 1.0.0 matrix graded 54 rows against a fixture-backed `/inbound` surface
that issued no request. Its create and update calls reported success while
persisting nothing, so a row reading VERIFIED meant only that a placeholder
resolved. **Every one of those 54 rows is retired in Part C — none is
deleted**, because the reasoning behind each is still a fair record of what
was checked and what was not.

The 2.0.0 matrix grades against the real backend. Its central rule:

> **A row is not passed by observing the frontend. A row is passed by
> observing the frontend AND confirming the server agrees** — by re-reading
> through the endpoint the row names, or by an audit/DB check where the row
> says so. A UI that looks right over a request that was never made is
> exactly the failure this re-base exists to remove.

---

## Evidence classes

| Class | Meaning |
|---|---|
| **LIVE** | Executed against the staging tenant, against the real endpoint. The only class that can pass a lifecycle row. |
| **UT** | A named automated test asserts it. `npm test` in `Talk-Leee/`. |
| **TE** | Type-enforced. `npm run typecheck` exits 0. |
| **BV** | Build-verified. The route or redirect appears in `.next/routes-manifest.json` after `npm run build`. |
| **CP** | Code present and read; nothing automated asserts it. |

## Verdicts

| Verdict | Meaning |
|---|---|
| **PASS** | The pass condition was met, on staging, with the server-side confirmation the row requires. |
| **FAIL** | The pass condition was not met. Record the observed value. |
| **BLOCKED** | Could not be run — a precondition was unmet. Record which. |
| *(blank)* | Not yet run. **Every row ships blank.** |

---

## Staging preconditions

No row in Part A can run until all of these hold. Confirm and record them
first; a row run without them is not evidence.

| # | Precondition | How to confirm | Why it gates |
|---|---|---|---|
| **P1** | Staging API reachable, `NEXT_PUBLIC_API_BASE_URL` points at it | `GET /rbac/users/me/permissions` returns 200 | Every row calls the API |
| **P2** | Test user holds `inbound:read`, `inbound:manage`, `inbound:assign` | Same call; inspect `permissions[]`. Source: `rbac.py:238-241` | Create needs **both** manage and assign — `inbound_campaigns.py:124`, `:128` |
| **P3** | A separate user holds `inbound:read` **only** | `rbac.py:297` — the `readonly` default | Rows LIVE-24, LIVE-25 need a genuinely read-only identity |
| **P4** | At least two tenant DIDs with `status = "verified"`, one unassigned | `GET /tenant-phone-numbers/` — `tenant_phone_numbers.py:111` | LIVE-07 needs a list; LIVE-01 consumes one |
| **P5** | At least one SIP trunk, `is_active` **and** `runtime_ready`, direction `inbound` or `both` | `GET /telephony/sip/trunks` | Readiness `trunk_ready` — `inbound_campaign_service.py:656` |
| **P6** | At least one base campaign that is `inbound`, or an `outbound` **draft** with no calls and no queued dialer jobs | `GET /campaigns` | `campaign_direction_conflict` — `inbound_campaign_service.py:1222` |
| **P7** | Tenant inbound admission **enabled** | `GET /inbound-campaigns/controls` → `inbound_enabled: true` | Readiness `tenant_inbound_enabled` — `service:546` |
| **P8** | Record whether the platform transfer gate is open | `GET /inbound-campaigns/capabilities` — `inbound_campaigns.py:191` | Decides whether LIVE-14 asserts blocked or permitted |
| **P9** | `npm run build` completed on the build under test | `.next/routes-manifest.json` exists | LIVE-27…LIVE-29 read it |
| **P10** | Record whether each provider failover flag is **on** in the deployed unit: `STT_FAILOVER_ENABLED`, `LLM_FAILOVER_ENABLED`, `TTS_FAILOVER_ENABLED` | Read the environment of the running systemd unit on the host. They are set in **no file in this repo** — the only env file present is `backend/.env.example` | Gates the wrappers at `voice_orchestrator.py:1218`, `:1349`, `:1521`. With a flag off there is **no secondary**, so A.13 asserts *the call ends honestly*, not *the call survives*. Recording the flag is what makes either verdict meaningful |
| **P11** | Host log access for the service under test, and the ability to set/unset env vars and restart it | Able to read the unit's logs live and grep by `call_id` | Every row in A.13–A.17 is closed by a log line plus a persisted row. Without logs these are unfalsifiable |
| **P12** | A caller-side line that can hang up at a scripted moment, and a second line for concurrent calls | Two external lines able to dial the DID | LIVE-67, LIVE-68 need hangup timing; LIVE-70 needs two simultaneous calls |

> **P8 is expected to be closed.** `INBOUND_TRANSFER_STAGING_PROOF_ENABLED`
> defaults to `false` (`backend/.env.example:158`) and is honoured only when
> `ENVIRONMENT=staging`. If all three gates are open, note it — LIVE-14
> inverts.

---

# Part A — Live test matrix

## A.1 Campaign lifecycle

| ID | Verifies | Endpoint / schema (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-01** | **Create an inbound campaign** | `POST /inbound-campaigns/` — `inbound_campaigns.py:120`; body `InboundCampaignCreateRequest` — `schemas/inbound_campaigns.py:27` | `/inbound-campaigns/new` → set name, pick a verified DID, base campaign, inbound trunk, timezone → Save | **201**; browser lands on `/inbound-campaigns/<uuid>`; `GET /inbound-campaigns/{id}` returns the same `name`, `did_number`, `campaign_id`, `sip_trunk_id`; `status = "draft"`; `version = 1` | Any non-201; or the detail read does not echo what was submitted | LIVE | |
| **LIVE-02** | Create is **draft**, not live | `status` default `'draft'` — `0022_inbound_calling_foundation.py:459` | Immediately after LIVE-01, call the DID from an external line | Call is **not** answered by the agent | The number answers before activation | LIVE | |
| **LIVE-03** | Create sends **only** contract keys | `extra="forbid"` — `schemas/inbound_campaigns.py:16` | DevTools → Network → the `POST` request body | Every top-level key appears in `InboundCampaignCreateRequest` (`schemas:28-42`). No `allowed_tools`, no `knowledge_base_id`, no `expected_version` | Any extra key present, or a 422 naming an unexpected field | LIVE | |
| **LIVE-04** | `Idempotency-Key` is sent | `_key` dependency — `inbound_campaigns.py:82-90` | Same request, inspect headers | Header present, 8–255 chars | Absent, or a 400 `invalid_idempotency_key` | LIVE | |
| **LIVE-05** | **Edit round-trip: `system_prompt`, `voice_id`, `greeting`** | `GET /inbound-campaigns/{config_id}` — `inbound_campaigns.py:209`; `qualification_config` echoed verbatim — `inbound_campaign_service.py:1015`; whitelist — `inbound_overrides.py:8-14` | On the LIVE-01 campaign open `/edit`. Set `system_prompt` to a 3-line string with punctuation and a trailing space, `voice_id` to a real id from LIVE-08, `greeting` to a 400-char string. Save. **Reload the page**, then re-open `/edit` | All three fields render **byte-for-byte** what was submitted — no trim, no truncation, no default substituted. Confirm against the raw `GET /inbound-campaigns/{id}`: `qualification_config.system_prompt`, `qualification_config.voice_id`, top-level `greeting` | Any character differs; any field returns empty; `greeting` truncated at 300 | LIVE | |
| **LIVE-06** | Blank override means **inherit**, not overwrite | `_neutral` — `inbound_overrides.py:17-27` | Clear `system_prompt`, save, re-read | `qualification_config` has **no** `system_prompt` key at all | The key persists as `""` or `null` | LIVE | |
| **LIVE-11** | Update refuses a changed DID | `assignment_workflow_required` — `service:1445` | In `/edit`, change the DID and save | Two requests: `PUT /{id}` then `POST /{id}/assign`. Both 200 | A single `PUT` carrying `did_number`; or a 409 `assignment_workflow_required` surfaced to the user | LIVE | |
| **LIVE-12** | Base campaign is locked in edit | `campaign_change_forbidden` — `service:1452` | Open `/edit` | The AI-campaign control is **disabled** | It is editable | LIVE | |
| **LIVE-13** | Active campaign cannot be edited | `pause_before_edit` — `service:1405` | Activate, then navigate to `/edit` | "Deactivate before editing" is rendered **instead of** the form | The form renders and only fails on save | LIVE | |
| **LIVE-15** | Activate a **ready** campaign | `POST /{id}/activate` — `inbound_campaigns.py:331` | Resolve every blocker, then Activate | 200; `status = "active"`; `active_at` set; the DID answers on a real call | Any non-200, or the DID does not answer | LIVE | |
| **LIVE-16** | Deactivate | `POST /{id}/deactivate` — `inbound_campaigns.py:350` | Deactivate an active campaign | 200; `status = "paused"`; a new call is **not** answered by the agent | Status unchanged, or the DID still answers | LIVE | |
| **LIVE-17** | Archive is offered only from `draft`/`paused` | `pause_before_archive` — `service:1655` | Inspect the action bar while `active` | Archive is **not offered** | Archive is offered while active | LIVE | |
| **LIVE-18** | Archived is read-only | `campaign_archived` — `service:1410` | Archive a paused campaign, then try to edit | Edit is refused with the read-only panel | The form opens | LIVE | |

## A.2 Readiness and activation gating

| ID | Verifies | Endpoint / schema (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-19** | **Activating a not-ready campaign shows the blocked state** | `GET /{config_id}/readiness` — `inbound_campaigns.py:223`; `InboundReadinessError` (409 `not_ready`) — `service:55-58`; the 28 checks — `service:493-985` | Create a campaign, then break one prerequisite — simplest is P7: `PATCH /inbound-campaigns/controls` with `inbound_enabled: false`. Open the detail page | `readiness.ready = false`; **Activate is disabled, not merely erroring**; the blocker for `tenant_inbound_enabled` (`service:546`) is listed with its remediation text | Activate is clickable; or it is clickable and returns 409 `not_ready` — the gate must be pre-emptive, not discovered on submit | LIVE | |
| **LIVE-20** | Readiness fails **closed** | `ready` read fail-closed — `inbound-api.ts` `normalizeReadiness` | Force the readiness call to fail (offline, or block it in DevTools) | Activate stays disabled; the readiness panel reports it could not be refreshed | Activate becomes enabled when readiness is unknown | LIVE | |
| **LIVE-21** | Every blocker is rendered individually | `blockers[]` — `schemas/inbound_campaigns.py:140`; built at `service:973-981` | With ≥2 blockers outstanding, read the checklist | Each blocker appears with its own `message` and `remediation`, not a single summary line | Blockers collapsed into one message, or remediation dropped | LIVE | |
| **LIVE-22** | Readiness re-checks on demand | `GET /{config_id}/readiness` | Fix the P7 blocker, press Refresh on the readiness panel | The blocker clears **without a full page reload**; Activate becomes enabled | Stale readiness persists | LIVE | |

## A.3 Routing inputs

| ID | Verifies | Endpoint / schema (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-07** | **DID picker shows masked numbers** | `GET /tenant-phone-numbers/` — `tenant_phone_numbers.py:111`; the number is carried under **`e164`** — `domain/models/tenant_phone_number.py:38` | Open `/inbound-campaigns/new` with P4 satisfied | Every option shows a **masked** number — a partial number with `•`, ending in the true last two digits. **No option reads "Number not assigned."** Selecting one populates `did_number` with the full E.164 in the request body | Any option renders "Number not assigned", or renders the number **unmasked in full** | LIVE, UT — *verified inventory is masked from e164, the only key that carries the number* (`inbound-api.test.ts`) | |
| **LIVE-08** | Availability is re-checked per number | `GET /inbound-campaigns/dids/availability` — `inbound_campaigns.py:145` | Assign a DID to a campaign, then open `/new` again | The assigned DID is **absent** from the picker | An already-assigned DID is still offered | LIVE | |
| **LIVE-09** | **Voice list loads from `GET /ai-options/voices`** | `GET /ai-options/voices` — `ai_options/providers.py:115`; item shape `VoiceInfo` — `domain/models/ai_config.py:187`; envelope `{voices: [...], elevenlabs_error?}` — `providers.py:149` | Open the AI-behaviour section of the form | The voice control is a **list populated from the endpoint**, each option showing `name` and `provider`; choosing one sets `qualification_config.voice_id` to that voice's `id`. When the response carries `elevenlabs_error`, it is surfaced rather than swallowed | The control is a free-text box; or the list is hard-coded; or `elevenlabs_error` is discarded silently | LIVE | |
| **LIVE-10** | **Timezone list is generated, not hard-coded** | `timezone` field, 1–64 chars — `schemas/inbound_campaigns.py:32`; readiness `timezone_valid` — `service:712` | Open the form; inspect the timezone control | The list is **generated from the platform's IANA zone set**, not a fixed literal; it contains zones from every continent; the browser's own zone is present and preselected; the value submitted is a valid IANA name the server accepts | The list is a fixed literal of a dozen entries; or a tenant in an unlisted zone cannot select their own | LIVE | |
| **LIVE-23** | Only eligible trunks are selectable | Readiness `trunk_ready` — `service:656`; `isEligibleInboundTrunk` | Inspect the trunk control with a mix of trunks present | Outbound-only or not-runtime-ready trunks are **disabled**, with the reason shown | An ineligible trunk is selectable and fails at readiness | LIVE | |
| **LIVE-38** | **Duplicate DID assignment is refused** | `POST /inbound-campaigns/{config_id}/assign` — `inbound_campaigns.py:283`; guard `_assert_did_free` raises `did_assignment_conflict` — `inbound_campaign_service.py:1141-1144`; race-safe authority is the partial unique index `uq_inbound_live_canonical_did ON inbound_did_assignments (canonical_did) WHERE status <> 'archived'` — `Alembic/versions/0022_inbound_calling_foundation.py:571-577` (and `uq_inbound_active_canonical_did`, `:562-566`) | With campaign A holding an active assignment for DID X, open campaign B and attempt to assign DID X | The backend **refuses** with `409 did_assignment_conflict`. The UI renders the **conflict** state — "Campaign changed elsewhere"/reload-before-retry copy from `inboundStateForError` — naming the clash and offering reload. No assignment is written and campaign A keeps DID X | The assignment succeeds; or two live assignments exist for one `canonical_did`; or the refusal surfaces as a **generic** "could not save" with no conflict affordance | LIVE | |
| **LIVE-39** | **Cross-tenant DID assignment is refused** | Tenant-scoped lookup `_verified_phone` — `SELECT ... FROM tenant_phone_numbers WHERE tenant_id = $1 AND e164 = $2`, `inbound_campaign_service.py:1058-1072`, raising `did_not_verified` at `:1069-1072`; picker source `GET /tenant-phone-numbers/` scoped by `_require_tenant` — `tenant_phone_numbers.py:111-119` | As tenant T1, call assign with a DID registered to tenant T2 (bypass the UI; the picker will not offer it) | The backend **refuses** — the tenant-scoped `SELECT` returns no row, so `did_not_verified` is raised regardless of the DID's status under T2. The number **never appears** in T1's picker, because the inventory read is tenant-scoped server-side | The assignment succeeds; or a T2 number is listed in T1's picker; or the refusal reveals that the DID exists under another tenant | LIVE | |

> **LIVE-09 and LIVE-10 were targets when this matrix was written; both
> are now implemented, so they are regression checks.** The voice control is
> a `<select>` populated from `useVoicesQuery()`, with the saved id unioned
> in so an id the catalogue no longer lists still renders as selected, and a
> free-text fallback only when the catalogue genuinely fails
> (`inbound-campaign-form.tsx:270-282`). The timezone control is generated
> from the runtime's own IANA set via `Intl.supportedValuesOf("timeZone")`,
> with the browser zone preselected and the saved zone unioned in
> (`inbound-campaign-form.tsx:43-59`). **Both rows are now expected to
> PASS.** See G-1 and G-2, both closed.

## A.4 Contract integrity and concurrency

| ID | Verifies | Endpoint / schema (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-24** | Optimistic concurrency surfaces as `conflict` | `expected_version` — `schemas:68`; `version_conflict` — `service:1538` | Open the same campaign in two tabs. Save in tab A, then save in tab B | Tab B renders the **`conflict`** state: "changed in another session", **with a reload control**. It does **not** auto-retry and does not overwrite | Tab B overwrites tab A; or it retries silently; or it shows a generic error | LIVE | |
| **LIVE-25** | 403 and 503 are not conflated | `permission_denied` — `inbound_campaigns.py:74`; `authorization_unavailable` — `inbound_campaigns.py:68` | Sign in as the P3 read-only user and open `/inbound-campaigns/new`. Separately, block `/rbac/users/me/permissions` in DevTools and reload | Read-only → **`no-permission`**, naming the missing capability. Blocked lookup → **`authorization-unavailable`**, offering retry and **not** claiming the user lacks access | Both render the same state; or a failed lookup reads as "you do not have permission" | LIVE, UT — `inbound-permissions.test.ts` | |
| **LIVE-26** | Capabilities fail closed | `getInboundCapabilities` — `inbound-permissions.ts` | Same blocked-lookup condition | Every action is hidden or disabled; nothing is inferred from the display role | Any create/edit/lifecycle action remains offered | LIVE, UT | |
| **LIVE-30** | Tenant admission switch requires a reason | `reason` min 3 chars — `schemas/inbound_campaigns.py:121` | As a `inbound:controls` holder, toggle the switch with a 2-character reason | Confirm is **disabled** below 3 characters | The request is sent and rejected server-side | LIVE | |
| **LIVE-31** | Tenant admission switch is authoritative | `PATCH /inbound-campaigns/controls` — `inbound_campaigns.py:170` | Disable admission, call an **active** campaign's DID | The call is **not** answered; a call already in progress is not cut off | A new call is answered while admission is disabled | LIVE | |

## A.5 Navigation and redirects

| ID | Verifies | Source | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-27** | **Every `/inbound*` URL redirects** | `next.config.ts` `redirects()`; manifest at `.next/routes-manifest.json` | Visit each, **with and without** a trailing slash: `/inbound`, `/inbound/`, `/inbound/new`, `/inbound/new/`, `/inbound/calls`, `/inbound/calls/`, `/inbound/cfg-1001/edit`, `/inbound/cfg-1001/edit/` | All 8 redirect. `/inbound*` → `/inbound-campaigns`, `/inbound/new*` → `/inbound-campaigns/new`, `/inbound/calls*` → `/calls?direction=inbound`, `/inbound/:id/edit*` → `/inbound-campaigns` (the **list** — old ids are not UUIDs, and `service:91` rejects a non-UUID) | Any of the 8 returns 404, or lands anywhere else | LIVE, BV | |
| **LIVE-28** | `/inbound` is gone from the route table | `.next/routes-manifest.json` | `node -e` over `staticRoutes` + `dynamicRoutes` | No page matches `^/inbound(/|$)` | Any `/inbound` page is still built | BV | |
| **LIVE-29** | One sidebar entry | `sidebar.tsx` navigation array | Sign in, read the sidebar | Exactly **one** row labelled "Inbound", pointing at `/inbound-campaigns`. No duplicate-key warning in the console | Two rows; or a React duplicate-key warning | LIVE, TE, BV | |
| **LIVE-32** | Campaign detail links to its own call history | `inbound_campaign_id` — `calls.py:1087` | Open a campaign with ≥1 call, press Call history | Lands on `/calls?direction=inbound&inbound_campaign_id=<id>`; only that campaign's calls are listed | The filter is ignored, or other campaigns' calls appear | LIVE | |

## A.6 Inbound call history

| ID | Verifies | Endpoint / schema (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-33** | Direction filter reaches the server | `direction` — `calls.py:1084` | `/calls`, choose Inbound | The request carries `direction=inbound`; every row is inbound | Filtering happens only client-side | LIVE, UT — `dashboard-api.inbound.test.ts` | |
| **LIVE-34** | Caller and DID are direction-correct | `from_number` — `calls.py:183`; `caller_ani` — `:201`; `called_did` — `:202`; projection `_display_caller_ani` — `calls.py:43` | Place a real inbound call, open the row | Caller column shows the **caller's** number, not the dialled DID. A carrier-private caller renders as private rather than blank or fabricated | Caller and DID transposed; or a private caller invents a number | LIVE, UT | |
| **LIVE-35** | Inbound route snapshot on call detail | `inbound_campaign_id` — `calls.py:203`; `_inbound_config_id` — `calls.py:66` | Open an inbound call's detail | Inbound campaign, assignment, route version, config version and checksum are shown; the link opens the right campaign | Shows `campaign_id` (the base campaign) in place of the inbound config id | LIVE | |
| **LIVE-36** | Recording state respects the pinned config | `_recording_state` — `calls.py:131-142` | Open a call whose campaign had recording disabled | Recording reads `disabled`; no player is offered | A player is offered for a call that was never recorded | LIVE | |
| **LIVE-37** | Search and sort are page-local **and say so** | No `search`/`sort`/`order` on `calls.py:1080-1093` — OQ-CH-05, OQ-CH-10 | With >1 page of inbound calls, search for a caller known to be on page 2 | The UI states the limitation; the row is not silently reported absent | The UI implies a full-set search, so a real call looks missing | LIVE | |

## A.7 Automated suite (no staging needed)

| ID | Verifies | Test | Pass | Class | Verdict |
|---|---|---|---|---|---|
| **AUTO-01** | Create sends only the confirmed contract + idempotency key | *create sends only the confirmed inbound contract and an idempotency key* — `inbound-api.test.ts` | Test passes | UT | |
| **AUTO-02** | Update uses `PUT` and carries `expected_version` | *update uses PUT and includes the stale-edit token* | Test passes | UT | |
| **AUTO-03** | Assignment uses the audited endpoint | *assignment uses the explicit audited endpoint and optimistic version* | Test passes | UT | |
| **AUTO-04** | Lifecycle uses the confirmed endpoint | *archive uses the confirmed lifecycle endpoint and optimistic version* | Test passes | UT | |
| **AUTO-05** | An ambiguous retry replays its original key | *an ambiguous retry reuses its idempotency key…* / *an expired ambiguous retry is blocked…* | Both pass | UT | |
| **AUTO-06** | Readiness fails closed without an explicit flag | *readiness fails closed without an explicit server ready flag* | Test passes | UT | |
| **AUTO-07** | Transfer needs every explicit gate | *transfer capability parsing requires all explicit server gates* | Test passes | UT | |
| **AUTO-08** | DID masking derives from `e164` | *verified inventory is masked from e164, the only key that carries the number* | Test passes | UT | |
| **AUTO-09** | Docs state register ≡ TS unions, both directions | `inbound-state-parity.test.ts` — 8 tests | All pass; 23 states | UT | |
| **AUTO-10** | Every mapped backend code resolves to exactly one state | *every mapped backend code resolves to exactly one state* | Test passes | UT | |
| **AUTO-11** | 403/503/409-not-ready are not conflated | *conflict, readiness and authorization codes are not conflated* | Test passes | UT | |
| **AUTO-12** | Form validation matches server boundaries | `inbound-campaign-form.test.ts` — 10 tests | All pass | UT | |
| **AUTO-13** | Capabilities fail closed | `inbound-permissions.test.ts` — 2 tests | Both pass | UT | |
| **AUTO-14** | Field components hold no validation rule | *no field component contains a validation rule or reaches a data source* | Test passes | UT | |
| **AUTO-15** | The selected opening mode reaches the wire, verbatim, for **both** values | *create emits the selected opening_mode verbatim for both caller_first and agent_first* — `inbound-api.test.ts`. Both branches are asserted because a hard-coded default satisfies a single-value check | Test passes | UT | |
| **AUTO-16** | `weekly_schedule` day 0 is Monday on both sides | *weekly_schedule day 0 is Monday on both the form and the backend scheduler* — `inbound-campaign-form.test.ts`. Reads `business_hours.py` at test time, the same technique as AUTO-09; also pins `defaultInboundWeeklySchedule()` to exactly Monday–Friday open | Test passes | UT | |
| **AUTO-17** | Agent-first cannot be saved without the audio it will speak | *agent_first requires a greeting and caller_first does not* — `inbound-campaign-form.test.ts`. Whitespace is rejected; caller-first stays optional | Test passes | UT | |
| **AUTO-18** | Caller and DID on `/calls/[id]` render exactly as the wire sent them, never gated on a capability | `CallPartiesPanel` — *an inbound call shows Caller ANI and the Called DID the wire sent* / *an outbound call shows Phone Number, never a Called DID row* — `src/app/calls/[id]/page.test.tsx` | Both pass | UT | |
| **AUTO-19** | The inbound route snapshot (assignment/route/config, campaign link) is gated on `inbound:read`/`inbound:manage`, and the campaign link never renders without the wire's id | `InboundRouteSnapshot` — 4 tests including *the route snapshot renders nothing without inbound:read/inbound:manage…* and *no campaign link renders without an id* — `src/app/calls/[id]/page.test.tsx`. Mutation-proved: forcing the gate open makes the "renders nothing" row fail | All 4 pass | UT | |
| **AUTO-20** | Consent and media state on `/calls/[id]` is gated the same way, and renders nothing when the server sent none of the six fields | `InboundConsentStatePanel` — 3 tests — `src/app/calls/[id]/page.test.tsx` | All 3 pass | UT | |
| **AUTO-21** | The `/calls` DID and campaign filters list only what the loaded page's wire data actually carries — deduplicated, sorted, no invented label, no option without an id | `distinctDidOptions` / `inboundCampaignFilterOptions` — 5 tests — `src/app/calls/page.test.tsx`. Mutation-proved: including a null DID makes two of the five fail | All 5 pass | UT | |

## A.8 Opening mode — heard behaviour

> **Scope note.** Rows A.8 to A.12 reach past the `/inbound-campaigns`
> surface into what a caller hears. The 2.0.0 scope line is deliberately
> widened here: a control that writes a field nobody ever proves at call
> time is an unverified control, and every field below is one this frontend
> writes. Each row is **staging-owned** — none can be closed from a desk or
> by any amount of frontend code.

| ID | Verifies | Backend anchor (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-40** | **Agent-first actually speaks first, with the saved greeting** | `_pinned_inbound_opening` returns `"agent"` — `lifecycle.py:3133-3147`; the pinned greeting becomes the opening audio — `lifecycle.py:3348-3349` | Activate a campaign with `opening_mode = agent_first` and a distinctive greeting. Call the DID **inside** business hours and stay silent for 10s | The agent speaks first, unprompted, within a few seconds of answer, and the words are **exactly** the saved greeting. The call does not stall on caller silence | Silence until the caller speaks; a generic or different greeting; the greeting spoken twice; or the call drops on silence | LIVE | |
| **LIVE-41** | **Caller-first plays no intro and does not talk over the caller** | first speaker `"user"` — `lifecycle.py:3146`; `caller_first_no_greeting` — `lifecycle.py:4005` | Same campaign switched to `caller_first`. Call, stay silent 10s, then speak | **No audio at all** until the caller speaks. The agent's first utterance is a response to what the caller said, not a scripted intro, and does not overlap the caller's opening turn | Any auto intro plays; two voices overlap on the first turn; or the caller's opening words are lost | LIVE | |
| **LIVE-42** | **The heard mode is the saved mode, not a stale pin** | mode pinned into the admission snapshot — `inbound_admission.py:874`; runtime refuses a snapshot that disagrees — `lifecycle.py:3142-3143` | Flip the mode on an existing campaign, save, deactivate/reactivate, call again | Heard behaviour flips to match, and the call detail shows an advanced `config_version` / `config_checksum` | The previous mode is still heard; or the call fails with an inconsistent-snapshot error | LIVE | |

## A.9 Business hours, timezone and after-hours actions

| ID | Verifies | Backend anchor (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-43** | **A call inside business hours reaches the agent** | `evaluate_business_hours` — `business_hours.py:99-156`, called pre-answer at `inbound_admission.py:720-726`; open hours select `"agent"` — `:726-729` | With a window open now in the tenant's zone, call the DID | The AI answers and converses. The call record carries no after-hours action | Any after-hours action is taken while the window is open | LIVE | |
| **LIVE-44** | **A call outside every window takes the configured action** | `selected_action = route["after_hours_action"]` when `is_after_hours` — `inbound_admission.py:726-732` | Same campaign, same day, called outside every window | Exactly the campaign's configured `after_hours_action` happens — no other | The AI answers normally; or an action other than the one configured | LIVE | |
| **LIVE-45** | **The boundary falls at the tenant's local minute, not the server's** | local time resolved before weekday/minute — `business_hours.py:126`, `:139-149`; windows are start-inclusive and **end-exclusive** | Set the tenant zone to one **offset from the server's**. Place two calls: one at `end - 1 min` tenant-local, one at exactly `end` | The first is answered by the agent; the second takes the after-hours action. The switch happens at the tenant-local minute | The switch happens at UTC/server time; both calls behave the same; or the call at exactly `end` is still treated as open | LIVE | |
| **LIVE-46** | **A DST shift moves the hours with the wall clock** | `local = instant.astimezone(zone)` — `business_hours.py:126` | In a DST-observing zone, place the same wall-clock call on either side of a transition, or re-run LIVE-43/44 in a zone that has just transitioned | Open/closed follows the tenant's **local wall clock** after the shift: 09:00 local stays 09:00 local | The window moves by an hour; a window that should be open reads closed | LIVE | |
| **LIVE-47** | **Day 0 is Monday end-to-end** *(closes the live half of G-3)* | `day = local.weekday()`, Monday=0 — `business_hours.py:140`, contract stated at `:105`; unvalidated at write time — `schemas/inbound_campaigns.py:39` | Configure **Monday only** open, every other day closed. Call on Monday and again on Sunday, both tenant-local | Monday is answered by the agent; Sunday takes the after-hours action | The behaviour is offset by a day in either direction | LIVE *(statically guarded by AUTO-16)* | |
| **LIVE-48** | **After-hours intake speaks the saved message verbatim and stores the reply** | intake message required before admission — `inbound_admission.py:783-791`; pinned as the opening audio — `lifecycle.py:3340-3350`, session-level at `:3855-3860` | With `after_hours_action = voicemail` and a distinctive intake message, call outside hours and leave a message | The agent's **first audio** is the saved message word-for-word; the caller's reply appears in the normal call transcript and history | The generic runtime fallback is spoken instead; the reply is missing from call history; or a separate voicemail artefact or notification is created, which this action does not produce | LIVE | |
| **LIVE-49** | **Hangup rejects before answer and is never billed as answered** | `_schedule_preanswer_hangup_and_release(..., "after_hours_closed")` — `asterisk_adapter.py:3689-3701`; normal clearing preserved — `:3560-3562`; history projection — `calls.py:841-842` | With `after_hours_action = hangup`, call outside hours | The caller is cleared **without the call being answered** — no media, no agent audio. The record shows `admission_reason = after_hours_closed` and surfaces as `after_hours`, not `denied`. Nothing is billed as an answered call | The call answers then hangs up; it is billed as answered; or it is reported as a generic denial | LIVE | |

## A.10 After-hours transfer — **BLOCKED on P8**

> **Every row in A.10 is BLOCKED until the transfer gates open.** The code
> switch is `CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE = False`
> (`inbound_transfer.py:25`). While it is false the only way to run these is
> the staging proof window: `ENVIRONMENT=staging` **and**
> `INBOUND_TRANSFER_STAGING_PROOF_ENABLED=true` (default `false` —
> `backend/.env.example:158`) **and** both
> `INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID` and
> `INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID` set to the exact campaign under
> test (`inbound_transfer.py:46-93`), **and** the platform switch
> `inbound_transfer_enabled` on. Record P8 before running. BLOCKED is the
> correct outcome in production and in any default staging — do not soften a
> row to make it runnable.

| ID | Verifies | Backend anchor (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-50** | **Transfer connects to an approved destination** | admission gates — `inbound_admission.py:753-775`; execution — `lifecycle.py:3558-3600`; success and ownership accepted — `:3706-3717` | With P8 open on the proof-scoped campaign, call outside hours with the target free to answer | Target answers; caller and target are connected; the attempt is recorded terminal-answered; lifecycle ownership is accepted. The destination dialled is one the policy allowlist already held | Any unapproved destination is dialled; the caller is dropped; or ownership is not accepted | LIVE | |
| **LIVE-51** | **A refused transfer takes the configured failure action, with provider proof** | `inbound_after_hours_transfer_failed` — `lifecycle.py:3723`; hangup branch `:3728`; proof required before fallback `:3746-3749`; fallback selection `:3750` | Same, with the target rejecting immediately. Run once per `failure_action` | The configured action happens: `hangup` clears the caller, `voicemail` continues into AI intake, `return_to_agent` returns the caller to the agent. The fallback runs **only** with `target_termination_confirmed` **and** `caller_media_retained` | The caller is dropped when the action is not hangup; a fallback runs without provider proof; or the caller is left bridged to an unproved leg | LIVE | |
| **LIVE-52** | **A target that never answers times out cleanly** | dial timeout resolves the leg as `no_answer` — `asterisk_adapter.py:5194-5196` | Same, with the target ringing out to the full answer timeout | The target rings to the configured timeout, then the failure action runs. The caller hears no dead air beyond the timeout and no second leg survives | Dead air past the timeout; the caller is dropped silently; or an orphan target leg remains | LIVE *(no automated test covers this branch — see R3)* | |

## A.11 Paused and archived campaigns — the DID stops answering

> LIVE-16 covers deactivate, LIVE-17 the archive affordance and LIVE-18 the
> read-only panel. These two rows cover what those do not: the **archived**
> DID's live behaviour, and whether the refusal is the documented reason
> rather than an unexplained failure.

| ID | Verifies | Backend anchor (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-53** | **An archived campaign's DID is not answered** | admission requires `config_status == "active"`, else `campaign_inactive` — `inbound_admission.py:671`; a live assignment is required — `:636`; routing SQL — `inbound_router.py:195`, `:208` | Archive a paused campaign, then call its DID from an external line | The call is **not** answered by the agent and no AI session is created | The DID still answers; or a session is created and then torn down | LIVE | |
| **LIVE-54** | **The refusal is recorded as `campaign_inactive`, not a generic failure** | same eligibility tuple — `inbound_admission.py:671` | After LIVE-16 (paused) and LIVE-53 (archived), read the rejection record for each call | Each rejection carries `campaign_inactive` and is visible in the rejected-inbound-calls panel with a reason an operator can act on | The reason is absent, generic, or presented as a carrier or unknown error | LIVE | |

## A.12 Recording review — one row per scenario

> **Recording review, not code.** Each row is closed by a person opening the
> artefact and listening to it. The common gates are
> `_inbound_recording_persist_allowed` — `telephony/recording.py:48-61` —
> the pinned per-campaign consent text — `lifecycle.py:3865` — and the
> projection an operator reads — `calls.py:131-142`. Recording must be
> enabled on the campaign **and** at platform level for any row here to
> produce an artefact; where it is not, record that as the reason rather
> than as a pass.

| ID | Scenario reviewed | Backend anchor (file:line) | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|
| **LIVE-55** | **Agent-first call** (LIVE-40) | disclosure precedes any opener audio — `telephony/recording.py:48-61`; consent override — `lifecycle.py:3865` | A recording exists and plays from the call detail. The **disclosure is the first thing on the tape**, before the greeting. Both sides are audible and in sync | No artefact; the greeting precedes the disclosure; one side missing or out of sync | LIVE | |
| **LIVE-56** | **Caller-first call** (LIVE-41) | same | Recording exists; the disclosure is first; the caller's opening words are **present and complete** — the first turn is the one most easily lost | The caller's opening utterance is clipped or absent | LIVE | |
| **LIVE-57** | **Open-hours conversation** (LIVE-43) | same | Recording exists and covers the whole call from disclosure to hangup, with no gap where the agent was speaking | Truncated at either end; silence over audio that was spoken | LIVE | |
| **LIVE-58** | **After-hours AI intake** (LIVE-48) | intake message pinned — `lifecycle.py:3340-3350` | Recording exists; the saved intake message is audible verbatim; the caller's message is captured in full | The fallback text is heard instead; the caller's message is cut off | LIVE | |
| **LIVE-59** | **After-hours hangup** (LIVE-49) — the negative case | pre-answer reject, no media created — `asterisk_adapter.py:3689-3701` | **No recording exists and none is offered**, because the call was never answered. The detail shows recording absent or `disabled` rather than an empty player | An artefact exists for a call that was never answered; or a player is offered with nothing behind it | LIVE | |
| **LIVE-60** | **After-hours transfer** (LIVE-50/51/52) — **BLOCKED on P8** | `lifecycle.py:3558-3745`; gate `inbound_transfer.py:25` | With P8 open: a recording exists for the caller-side leg, the disclosure precedes the transfer, and the point of transfer is audible rather than a silent cut | No artefact; the transfer happens before any disclosure; the tape ends at the transfer with no explanation | LIVE | |

## A.13 Provider and gateway failure — **fault injection required**

> **These cannot be closed by code inspection.** Unit tests exercise the
> resilience *classes*; they say nothing about whether the orchestrator
> builds the wrapper on a real originate, whether the secondary initialises
> with the tenant's credential, whether the replayed buffer reaches it, or
> whether the pipeline survives a mid-call provider swap. The backend says so
> itself — `stt_fault_injection.py:11-19`.
>
> **Record P10 before running.** Each row has two legitimate outcomes and
> which one applies is decided by the flag, not by the tester: with failover
> **on**, the call must survive; with failover **off**, there is no secondary
> and the call must fail *honestly* — terminate, release its slot, and
> persist a truthful outcome. Both are passes. Neither is a pass if the
> caller is left on dead air or the row settles as a success.
>
> The injectors are deliberately hard to leave on: the switch is a campaign
> UUID rather than a boolean, an expiry is mandatory, and each refuses to run
> when its failover flag is off (`stt_fault_injection.py:20-48`). Do not work
> around a refusal — record it as the reason the row did not run.

| ID | Verifies | Backend anchor (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-61** | **AI (LLM) failure mid-call** | breaker + first-token deadline — `resilient_llm.py:58-60`, `:177-227`; exhaustion speaks a fallback line rather than going silent — `:223-227`; wiring gate — `voice_orchestrator.py:1349`; injector — `llm_fault_injection.py:62-64` (`VOICE_LLM_FAULT_CAMPAIGN`, `VOICE_LLM_FAULT_UNTIL`, `VOICE_LLM_FAULT_TURNS`) | Scope the injector to the campaign under test with an expiry, call in hours, and speak a turn that forces a model reply | Flag **on**: the secondary takes the turn and the caller hears a reply; dead air is bounded by the ~2.5 s first-token deadline, not the 10 s wall clock. Flag **off**: the caller hears the fallback line and the call ends with a terminal row and a released slot | Dead air past the deadline; the caller is dropped silently; the call row settles as answered-successful; or the slot is not released | LIVE | |
| **LIVE-62** | **STT failure — the connect-and-go-deaf case** | `_SilentStreamWatchdog` — `resilient_stt.py:36-53`, `:294`; wiring — `voice_orchestrator.py:1259-1263`; gate — `:1218`; injector refuses without a safety net — `stt_fault_injection.py:33-38` | Scope `VOICE_STT_FAULT_SILENT_CAMPAIGN` to the campaign with `VOICE_STT_FAULT_SILENT_UNTIL` set, call, and **speak continuously** — the injected fault is a socket that accepts audio and returns no events | Flag **on**: `resilient_stt_stream_silent` appears, the secondary is promoted, the buffered utterance is replayed and transcribed, and the conversation continues through the swap. Flag **off**: the injector declines and logs why — record that, do not force it | The caller talks and is never heard, with no failover and no teardown; the call runs to the 300 s inactivity timeout (`lifecycle.py:75`) with the caller on dead air | LIVE | |
| **LIVE-63** | **TTS failure — the agent has no voice** | failover wiring — `voice_orchestrator.py:1521-1530`; delivery failure fails closed on the first typed proof — `telephony_media_gateway.py:856-880`; outbound pre-ring gate for contrast — `prewarm.py:515-539` | With the TTS credential made invalid for the campaign's provider, call the DID | The caller is never left on silent dead air: either the secondary speaks, or the call is ended and the row carries a terminal status. Undelivered speech is **never** marked spoken | The transcript records agent turns the caller never heard; or the caller sits in silence until a watchdog fires | LIVE | |
| **LIVE-64** | **Gateway media session lost while the channel is up** | detect — `lifecycle.py:304`; reconcile — `:347`; 1 s watchdog — `:392`, interval `:246-251`, 2-tick debounce `:244` | With a call answered and audio flowing, restart the C++ media gateway | The call is force-ended within roughly two seconds; `gateway_media_session_lost` is logged with the call id; the concurrency slot is released; the row reaches a terminal status | The channel stays up and billing with no audio; no teardown; or the slot leaks | LIVE | |

## A.14 Network interruption — the control plane drops, calls must not

> The consequence of an interruption is unit-tested; **the interruption is
> not**. `grep -rl "reconnect" backend/tests/unit/` matches no telephony
> adapter test, so the ARI reconnect loop has no automated coverage at all.

| ID | Verifies | Backend anchor (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-65** | **ARI control socket drops mid-call** | reconnect loop, exponential backoff `0.25·2ⁿ` capped 30 s with jitter, `heartbeat=20` — `asterisk_adapter.py:1429-1475` | With a call answered, sever the ARI websocket (drop the connection, do not stop Asterisk) | The adapter logs `ari_reconnect_attempt` with a growing delay and reconnects. The **live call's audio is unaffected** throughout. Once reconnected, the call's terminal event is still processed and the row settles | The live call drops or goes silent; the adapter never reconnects; or the call's terminal event is lost and the row is left non-terminal | LIVE *(no automated test — see R-NET)* | |
| **LIVE-66** | **An unreachable gateway never mass-hangs-up live calls** | `live_gateway_session_ids is None` is an explicit no-op that advances no counter — `lifecycle.py:317-322` | With two or more calls up, make the gateway's session-inventory endpoint unreachable for several watchdog ticks, then restore it | **No call is torn down** while the inventory cannot be read. After restore, only genuinely dead sessions are reaped | Live calls are force-ended during the outage — the failure mode this no-op exists to prevent | LIVE | |

## A.15 Caller hangup at the two moments that leak resources

> Pre-answer and mid-setup hangup are well covered by unit tests
> (`test_asterisk_preanswer_admission.py`). **Post-answer, mid-greeting is
> not** — no automated test hangs up while the agent is speaking.

| ID | Verifies | Backend anchor (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-67** | **Caller hangs up during the greeting** | terminal path — `lifecycle.py:4600`; duplicate-end fence — `:4633-4644`; pre-answer contrast — `asterisk_adapter.py:3571`; cause classification — `call_status.py:186-215` | Call the DID and hang up **while the agent is mid-greeting**, before speaking. Repeat at ~1 s and at ~3 s into the greeting | The session is torn down once; the row reaches a terminal status with a duration matching the real answered time; the outcome is a hangup classification, not `failed`; the concurrency slot and any STT/TTS sockets are released; no orphan appears in the next watchdog sweep | The row stays non-terminal or `termination_pending`; duration is zero or invented; the slot leaks; or a second teardown runs | LIVE | |
| **LIVE-68** | **Caller hangs up during a transfer** — **BLOCKED on P8** | parent termination fences the target-only retry owner, then requires all-leg proof — `asterisk_adapter.py:2117-2166`; the leg future resolves `{"status": "failed", "error": "caller_hung_up"}` — `:2130-2134`; gate — `inbound_transfer.py:25` | With P8 open, transfer to a target that is still ringing, then hang up the **caller** | Both legs are proved absent under one deadline; the transfer leg persists `failed` with `caller_hung_up`; no target leg survives the caller; settlement runs only after proof | The target stays live after the caller is gone; the leg settles as answered; or settlement runs on parent-only proof | LIVE | |

## A.16 Repeated, duplicate and simultaneous call events

> Idempotency **is** implemented and unit-tested at every layer (see R-IDEM
> below). What no test covers is the same guards under **real** event timing
> and two genuinely concurrent calls.

| ID | Verifies | Backend anchor (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-69** | **Repeated and duplicate call events are absorbed once** | duplicate `StasisStart` fence — `asterisk_adapter.py:3619-3630` (`inbound_setup_duplicate_ignored`); duplicate call-end fence — `lifecycle.py:4633-4644`; admission replay — `duplicate_replay`, `inbound_admission.py:1044`+ | Run a normal inbound call and read the logs for the whole lifecycle | Exactly **one** admission, one answer, one session and one terminal settlement per call. Any `inbound_setup_duplicate_ignored` or `duplicate call-end ignored` line is followed by **no** second row, second billing entry or second live-feed transition | A second session, a second terminal write, a duplicated billing entry, or a contradictory terminal status | LIVE | |
| **LIVE-70** | **Two simultaneous inbound calls on one DID** | per-tenant leases — `telephony_concurrency_limiter.py`; cluster reconcile — `global_concurrency.py:306`; zombie sweep threshold — `lifecycle.py:231-233`, sweep `:905-935` | Call the same DID from two external lines within the same second. Then repeat with the tenant at its concurrency cap | Both calls are admitted and answered independently with **no crossed audio** and no shared session; each settles on its own row. At the cap, the excess call is refused with a recorded reason and the admitted call is unaffected | Audio crosses between calls; one call's teardown settles the other's row; both consume one slot; or the cap refusal is unrecorded or hits the wrong call | LIVE | |

## A.17 A failed call terminates, persists truthfully, and the dashboard says so

> The end-to-end claim behind A.13: it is not enough that the pipeline
> reacts — the call must **stop**, the row must say what happened, and an
> operator must be able to see it. Note the standing limitation: the call
> contract carries **no per-call failure-reason field**
> (`calls.py:177-215`), so "visible" here means status, outcome and — for a
> transfer — the leg rows, not a rendered cause.

| ID | Verifies | Backend anchor (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-71** | **A provider-failed call reaches a terminal row with a truthful outcome** | terminal vocabulary — `call_status.py:82-91`; `termination_pending` is non-terminal — `:97`, `termination.py:262-270`; outcome resolution — `outcome_resolver.py`; `_pipeline_failed → FAILED` — `outcome_resolver.py:16` | After each of LIVE-61…LIVE-64, re-read the call through `GET /calls/{call_id}` | The row is **terminal** — one of `ended, completed, failed, cancelled, canceled, rejected, busy, no_answer` — never left at `termination_pending`. The outcome distinguishes a pipeline failure from a normal hangup. Duration matches the real answered time. The concurrency slot is released | The row is non-terminal minutes later; a failed call is recorded as answered-successful; duration is zero for a call that carried audio; or the slot is still held | LIVE | |
| **LIVE-72** | **The failure is visible to an operator without log access** | list/detail projection — `calls.py:177-215`; termination fields — `calls.py:341-343`; transfer legs — `calls.py:265`, `:1328-1372` | With the row from LIVE-71, open `/calls` and `/calls/{id}` as the tenant user | The call is findable in call history and its status/outcome pill reflects the failure rather than a neutral grey unknown. `termination_pending` reads as **Ending** in amber, not as a raw token. Where a transfer leg exists, the Transfer legs panel shows its status and the server's `terminal_reason` | The failed call looks identical to a successful one; a raw internal token is shown to the operator; or a transfer leg the server returned is not rendered anywhere | LIVE | |

## A.18 Inbound billing under load — balance, duplicate events, concurrency, isolation

> Added 2026-09-03, staging-owned. These rows were named in a verify-only
> discovery pass over the inbound billing connection and could only be
> anchored to backend code — none can be closed from the frontend. All
> verdicts are blank by the same rule as the rest of Part A.

| ID | Verifies | Backend anchor (file:line) | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-73** | **A call against a sufficient balance settles normally** | quota gate admits — `inbound_admission.py:1069-1112`; settlement write — `:1518-1547`; terminal `billing_status='finalized'` — `:1571-1573` | With a tenant well under its monthly `minutes_allocated`, call the DID and let the call run to a natural end | The caller is connected and hears the agent with no early cutoff. The call's `billing_status` reaches `finalized`. `GET /campaigns/minutes/status` (`campaigns.py:994`) shows `used_minutes` risen by the call's duration in whole minutes. The call detail page's Billing panel shows "Settled" with a billed-duration figure | The call is denied `insufficient_minutes`; `billing_status` never reaches `finalized`; or `used_minutes` does not move | LIVE | |
| **LIVE-74** | **A call against a low remaining balance is answered but capped at what remains, not at the campaign's configured maximum** | reservation clipped to remaining seconds when `allocated_minutes > 0` — `inbound_admission.py:1093-1100` | With a tenant whose remaining minutes this month is small but ≥1 full minute (e.g. 2 minutes), call the DID and let the agent talk past that remaining allowance | The caller is connected and hears the agent normally — nothing in the call itself signals "low balance" to the caller. The call is force-ended at the remaining-seconds boundary, not at the campaign's `max_call_duration_seconds`. Before and after the call, the dashboard's "Minutes remaining" figure renders amber (`isLowBalance`, under 15% of allocation — `topup-api.ts:167-171`, rendered `topup-card.tsx:190`) | The call is denied outright despite ≥60s remaining; or the call is allowed to run past the remaining-seconds boundary uncapped and unbilled | LIVE | |
| **LIVE-75** | **A call against an exhausted balance is rejected before answer** | `reservation_seconds < 60` denies `insufficient_minutes` — `inbound_admission.py:1101-1132` | With a tenant at or effectively at its monthly allocation (remaining < 60 seconds; does not apply to an unlimited tenant, `minutes_allocated <= 0`), call the DID | The caller never reaches the agent. The attempt appears in the campaign's Rejected calls panel labelled "Insufficient minutes" (`rejected-inbound-calls-panel.tsx:22`). No `calls` row for this attempt reaches `billing_status='reserved'` or beyond | The caller is connected to the agent; the rejection is absent from the Rejected calls panel; or a usage reservation row is created for the denied attempt | LIVE | |
| **LIVE-76** | **A duplicate billing event produces exactly one usage transaction** | idempotency key — `inbound:{finalize\|release}:{provider}:{provider_call_id}`, `inbound_admission.py:1521`; `ON CONFLICT (tenant_id, idempotency_key) DO NOTHING` + re-select — `:1531-1547` | Force the carrier/provider to redeliver the terminal (hangup/finalize) callback a second time for one call, against the same `call_id` | Exactly one row exists in `inbound_usage_transactions` for that idempotency key — `SELECT count(*) FROM inbound_usage_transactions WHERE tenant_id=<t> AND idempotency_key='inbound:finalize:<provider>:<provider_call_id>'` returns 1. The call's `billing_status`, `duration_seconds` and `cost` are unchanged by the replay | A second row exists for the same idempotency key; or `billing_status`/`duration_seconds`/`cost` changed on the replay | LIVE | |
| **LIVE-77** | **Two simultaneous inbound calls against the tenant's concurrency limit** | deny reason `max_active_calls_reached` — `telephony_concurrency_limiter.py:294-299` | With the tenant's `tenant_telephony_concurrency_policies.max_active_calls` set to 1, call the DID from two external lines within the same second | One call is admitted and answered; the second is refused `max_active_calls_reached`, recorded in the Rejected calls panel as "All lines in use" (`rejected-inbound-calls-panel.tsx:19`). No audio or session state crosses between the two attempts | Both calls are admitted; the refusal is unrecorded; or state crosses between the two attempts | LIVE | |
| **LIVE-78** | **Five simultaneous inbound calls against the tenant's concurrency limit** | same gate as LIVE-77 — `telephony_concurrency_limiter.py:294-299`, policy `max_active_calls` | With `max_active_calls` set below 5 (e.g. 3), call the DID from **five** external lines within the same second. Note: P12 names two lines as sufficient for LIVE-70/LIVE-77; this row needs five and is not closed by P12 alone | Exactly `max_active_calls` calls are admitted and settle independently on their own rows; every call beyond the cap is refused `max_active_calls_reached` and each refusal is individually recorded. No call's teardown affects another's row | More than the policy cap is admitted; any refusal is unrecorded; or one call's settlement affects another's row | LIVE | |
| **LIVE-79** | **Unknown DID is rejected under strict routing** — **BLOCKED**, see below | `strict_inbound_enabled()` returns `True` unconditionally, no runtime flag selects it — `inbound_router.py:44-47`; `decide_inbound_route(..., strict: bool = True)` discards the parameter — `:95`, `:111` (`del strict`) | Call a DID with no active `inbound_did_assignments` row | The call is denied `unknown_did` before answer; the attempt appears in the Rejected calls panel labelled "Unknown number" (`rejected-inbound-calls-panel.tsx:14`) | The call is answered by any agent; or the rejection is unrecorded | LIVE | |
| **LIVE-80** | **Tenant billing isolation: one tenant's calls never appear in or charge another tenant's usage** | RLS `ENABLE`/`FORCE ROW LEVEL SECURITY` + `inbound_usage_transactions_tenant_isolation` policy on `inbound_usage_transactions` — `Alembic/versions/0022_inbound_calling_foundation.py:41`, `:47-60`; every ledger read/write carries an explicit `tenant_id=$1` predicate, e.g. `inbound_admission.py:1544-1547` | With two seeded tenants A and B, each holding an active inbound assignment, place one inbound call to tenant A's DID. Then, authenticated as tenant B, read `GET /campaigns/minutes/status` and `GET /billing/topups/balance`; separately, as platform admin, query `inbound_usage_transactions` directly for tenant B | Tenant B's minutes/balance figures are unchanged by tenant A's call. `SELECT * FROM inbound_usage_transactions WHERE tenant_id=<B> AND call_id=<A's call id>` returns zero rows | Tenant B's used-minutes or balance figure changed after tenant A's call; or a ledger row exists under tenant B referencing tenant A's call | LIVE | |

**LIVE-79 is BLOCKED.** Routing has no flag that selects strict vs
non-strict at runtime to contrast against — the code is unconditionally
strict (`inbound_router.py:111`). The only related flags are negative-only,
and each is **unset by default**: `TELEPHONY_INBOUND_REQUIRE_TENANT`,
`INBOUND_STRICT_ROUTING`, `TELEPHONY_STRICT_INBOUND_ROUTING`. Setting any of
them to a false value (`0`/`false`/`no`/`off`/`disabled`) makes production
**refuse to boot** (`_check_inbound_strict_routing`, `prod_gate.py:128-155`).
So LIVE-79 can only ever be run against the single strict mode that always
executes — there is no deployable non-strict arm to contrast it with, in
staging or in production.

## A.19 Full workflow parity, role/access coverage, layout, and outbound regression

> Added 2026-09-03, staging-owned except where a row names an automated test
> directly. Rows here answer a verify-only pass over the whole-workflow,
> role-testing, layout-testing and outbound-regression lines: what already
> has automated coverage, what needs a staging login, and what desktop/mobile
> parity still requires a person at a real device. All verdicts ship blank.

| ID | Verifies | Anchor | Staging step | Pass | Fail | Class | Verdict |
|---|---|---|---|---|---|---|---|
| **LIVE-81** | **One inbound campaign, followed end to end: create → configure → activate → receive → history → detail** | Chains LIVE-01 (create), LIVE-05 (configure), LIVE-15 (activate), LIVE-40/43 (receive), LIVE-33 (history), LIVE-35 (detail) | Run those six as one continuous pass against a single campaign and a single real call, in order, without substituting a different campaign or a synthetic call partway through | The same `config_id` and the same `call_id` thread through every step: the campaign created in step 1 is the one activated in step 3; the call received in step 4 is the one found in history (step 5) and opened in detail (step 6), with `inbound_campaign_id` on that call equal to the created campaign's id | Any step substitutes a different campaign or call than the one the sequence started with; or any individual step fails per its own row | LIVE | |
| **LIVE-82** | **A viewer (`inbound:read` only, no `inbound:manage`) sees inbound routing/consent detail but no create or edit action** | `getInboundCapabilities` — `inbound-permissions.ts:50-91`; gate wired at `src/app/calls/[id]/page.tsx` (`canViewInboundDetails`) and `src/app/inbound-campaigns/page.tsx` (`capabilities.canCreate`) | Sign in as a user holding `inbound:read` only. Open an inbound call's detail page, then `/inbound-campaigns` | The Inbound route snapshot and Consent and media state panels render on the call detail page (AUTO-19, AUTO-20 prove the gate opens for `canView`). On `/inbound-campaigns`, the list renders but **New inbound campaign** does not, and campaign cards show no **Edit** action | Either panel is hidden for a `inbound:read` holder; or a create/edit control renders for a role without `inbound:manage` | LIVE — capability logic is UT-proved (AUTO-13, AUTO-19, AUTO-20); this row is the staging confirmation that the real permission lookup, not a seeded test value, produces the same result | |
| **LIVE-83** | **A user with neither `inbound:read` nor `inbound:manage` sees ordinary call fields but no inbound routing/consent detail** | Same gate as LIVE-82 | Sign in as a user holding neither permission (e.g. only `calls:read`). Open the same inbound call's detail page | Caller/ANI, Called DID, duration, outcome and date still render (never gated — AUTO-18). The Inbound route snapshot and Consent and media state panels **do not render at all** — no card, no placeholder, no denial message | Either panel renders without the capability; or basic call identity is also hidden, which would be a regression on AUTO-18 | LIVE, UT — the gate itself is AUTO-19/AUTO-20; this row is the staging round-trip with a real unprivileged account | |
| **LIVE-84** | **Unauthorized access (no session / expired token) to inbound and calls screens** | Existing auth guard, `route-guard.tsx`; `/inbound-campaigns*` additionally gates on `capabilities.canView` once authenticated | With no valid session, request `/inbound-campaigns`, `/inbound-campaigns/new`, `/inbound-campaigns/<id>`, `/calls`, `/calls/<id>` directly | Every route redirects to sign-in (or renders the app's standard unauthenticated state) before any inbound data is requested | Any route renders inbound campaign or call data without a valid session | LIVE — no automated test exercises a real unauthenticated HTTP round-trip against these routes; this can only be closed with a staging login/logout pass | |
| **LIVE-85** | **Desktop and mobile layout: `/inbound-campaigns` list, new, detail, edit — real data** | `tests/responsive.overlap.spec.ts` — `appRoutes` now includes `/inbound-campaigns`, `/inbound-campaigns/new`, `/inbound-campaigns/campaign-001`, `/inbound-campaigns/campaign-001/edit` (added 2026-09-03) | Run `npm run test:visual` (12 viewports, 1920px down to 320px) against a build with a real backend and at least one real campaign; separately, open each of the four routes by hand on a real desktop browser and a real phone with a real campaign id | The automated pass reports no occluded interactive element, no clipped text, no horizontal overflow and CLS ≤ 0.05 at all 12 viewports (BV/UT-class evidence). The manual pass additionally confirms real campaign content (readiness checklist, lifecycle buttons, the ~25-field edit form) is usable at each size — the automated pass runs against `campaign-001`, which resolves to whatever that id's state is in the environment under test, not guaranteed real content | The automated pass fails at any viewport; or the manual pass finds a control unusable (e.g. the readiness checklist or transfer-policy fields unreachable) that the automated occlusion check did not catch, since it audits geometry, not task completion | LIVE, UT — `tests/responsive.overlap.spec.ts` closes the geometry half; the content-usability half stays manual | |
| **LIVE-86** | **Desktop and mobile layout: `/calls` and `/calls/[id]` with an inbound call, including the new DID/campaign filters** | Same spec; `/calls` and `/calls/call-001` already covered pre-2026-09-03 | Run the same automated pass; separately, on a real device, open `/calls`, use the new DID and campaign `<select>` filters, and open an inbound call's detail | No occlusion/clipping/overflow at any viewport (automated). The DID and campaign filter `<select>` elements are reachable and usable at 320px width, and selecting an option narrows the list (manual — a native `<select>`'s open/closed dropdown rendering is outside what the occlusion auditor inspects) | The automated pass fails; or a filter control is unreachable or unusable at a small viewport | LIVE, UT | |
| **LIVE-87** | **Outbound campaigns remain functional after this branch's frontend changes** | Shared-component test coverage: `live-calls-panel.test.tsx` (in-call controls, direction-agnostic), `CallSummaryCard.test.tsx` (no `direction`/`inbound`/`outbound` reference in the component itself), `campaign-performance.test.ts` (campaign list/filter/sort utilities the outbound campaigns list uses), `dashboard-api.campaign-start.test.ts` (outbound campaign-start 402 handling) | Place one real outbound call through the dialer on staging after this branch is deployed | The call originates, connects, and its record appears in `/campaigns/<id>` and `/calls` exactly as before this branch. Recorded baseline: full local suite run 2026-09-03, `368` tests before this branch's work, `368` passed / `2` skipped / `0` failed at that baseline (the one pre-existing failure, `billing-api.test.ts`, was a whole-file hang/timeout artifact traced to an uncleared `QueryClient`, fixed in this branch — see AUTO-18…AUTO-21 for what this branch added on top). What the suite proves: the shared components an outbound call renders through still behave correctly against their mocked contracts. What it cannot prove: that the outbound dialer, telephony provider integration, or campaign-start flow still work end to end in production — no unit test calls a real telephony provider | The staging call fails to originate or is missing from history; or its record shows any inbound-only field | LIVE, UT | |

---

# Part B — Known gaps

Recorded so a blank verdict is not mistaken for an untested oversight.

| # | Gap | Consequence |
|---|---|---|
| **G-1** | ~~**Voice is a free-text input.**~~ **RESOLVED 2026-09-02.** The voice control is a `<select>` fed by `useVoicesQuery()`, with the saved id unioned in and free text used only when the catalogue itself errors — `inbound-campaign-form.tsx:270-282` | No longer a gap. LIVE-09 becomes a regression check rather than a target. A voice id can no longer be mistyped into a readiness failure, though the server remains the final authority on whether the chosen id resolves. |
| **G-2** | ~~**Timezone is a hard-coded 12-entry list.**~~ **RESOLVED 2026-09-02.** The list is generated from `Intl.supportedValuesOf("timeZone")`, the browser zone is preselected, and the saved zone is unioned in; the 12-entry literal survives only as an unreachable fallback for a runtime that cannot enumerate zones — `inbound-campaign-form.tsx:43-59` | No longer a gap. LIVE-10 becomes a regression check. A tenant in any IANA zone can select their own, so hours are evaluated in the zone they actually operate in. |
| **G-3** | ~~**`weekly_schedule[].day` indexing is unverified.**~~ **RESOLVED IN CODE AND GUARDED 2026-09-02.** The backend is Monday=0 — `day = local.weekday()` and the stated "Monday=0 windows" contract, `business_hours.py:140` and `:105` — and the form labels index 0 as Monday. It is still unvalidated at write time (`schemas/inbound_campaigns.py:39` remains an opaque `dict`), so the guard is a test, not a schema: **AUTO-16** — *weekly_schedule day 0 is Monday on both the form and the backend scheduler* (`inbound-campaign-form.test.ts`), which reads `business_hours.py` at test time and fails on `isoweekday()` (Monday=1) or `strftime("%w")` (Sunday=0) | The day-offset risk is closed for regressions on either side. It is **not** closed end-to-end: no live call has yet proved a Monday-only schedule answers on Monday. **LIVE-47** is that row. |
| **G-4** | **No component render test exists for the campaign form.** | Every state row in A.1–A.6 must be observed by a person; none is machine-asserted end to end. |
| **G-5** | **Transfer rows are unrunnable while the gates are shut** (P8). The environment switch is only the outer gate: the inner one is the code constant `CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE = False` (`inbound_transfer.py:25`), and the proof window is additionally scoped to one exact tenant/config pair (`inbound_transfer.py:46-93`) | LIVE-14, **all of A.10 (LIVE-50, LIVE-51, LIVE-52)** and **LIVE-60** are BLOCKED in any environment where `INBOUND_TRANSFER_STAGING_PROOF_ENABLED=false` (`backend/.env.example:158`) — which is the default and the production requirement. Opening the window is a backend release decision, not a test-setup step. |
| **G-6** | **Six OPEN questions are backend-owned** — OQ-CFG-05, OQ-CH-03, OQ-CH-04, OQ-CH-05, OQ-CH-06, OQ-CH-07 | LIVE-37 asserts a *documented limitation*, not correct behaviour. It cannot be made to pass properly from the frontend. |
| **G-7** *(R-NET)* | **The ARI reconnect loop has no automated test.** `asterisk_adapter.py:1429-1475` is uncovered: `grep -rl "reconnect" backend/tests/unit/` matches five files, none a telephony-adapter test. What *is* covered is the consequence of an interruption — `test_ari_failure_without_inventory_proof_is_unconfirmed`, `test_reconcile_is_a_noop_when_the_gateway_cannot_be_queried` — not the interruption itself | **LIVE-65** and **LIVE-66** are the only evidence that exists for this path. Closing it needs chaos against a live socket plus log access (P11); it is not closable by code. |
| **G-8** *(R-IDEM)* | **Duplicate/repeated call events are handled idempotently, and this is well covered — at unit level.** `test_duplicate_stasis_start_admits_only_once`, `test_duplicate_stasis_start_is_fenced_while_cleanup_owns_admission`, `test_terminal_event_burst_claims_active_teardown_once` (`test_asterisk_preanswer_admission.py`); `test_provider_identity_is_idempotently_replayed`, `test_reversal_is_one_idempotent_compensating_entry` (`test_inbound_admission.py`); `test_durable_same_key_replays_without_creating_a_second_leg` (`test_inbound_transfer_controls.py`); `test_concurrent_recovery_skips_call_already_in_flight` (`test_telephony_orphan_recovery_confirmation.py`). Frontend: AUTO-05, and the duplicate-hangup guard in `live-calls-panel.test.tsx` | The guards are proven against injected event orders, **not** against real carrier event timing or two genuinely concurrent calls. **LIVE-69** and **LIVE-70** are those rows. |
| **G-9** | **Provider failover may not be enabled in production at all.** `STT_FAILOVER_ENABLED`, `LLM_FAILOVER_ENABLED` and `TTS_FAILOVER_ENABLED` are set in **no file in this repo** — the only env file present is `backend/.env.example`. With a flag off the orchestrator never builds the wrapper (`voice_orchestrator.py:1218`, `:1349`, `:1521`) and there is no secondary | **P10 gates all of A.13.** Until the deployed unit's environment is read, it is unknown whether the resilience these rows test is running. The fault injectors also refuse to run without their flag (`stt_fault_injection.py:33-38`), so the flag blocks both the fix and its test. Backend/ops-owned. |
| **G-10** | **The call contract carries no per-call failure reason.** `CallListItem` / `CallDetail` (`calls.py:177-215`) expose no `failure_reason` or `failure_category`. The backend *has* the vocabulary — `humanize_failure()` returns `tts \| llm \| stt \| telephony \| prewarm \| other` (`failure_reasons.py:13`) — but its only call site is the outbound-originate 503 (`telephony_bridge.py:999`) | **LIVE-72** can only assert status, outcome and transfer legs. A call that failed because TTS was out of credits is, in the dashboard, indistinguishable from one that failed for any other reason. Backend-owned: closing it needs the field on the contract before any frontend work. |

**LIVE-14** is reserved for the transfer-configuration row and is
deliberately unwritten while P8 is closed: writing a pass condition for a
runtime nobody can exercise would be inventing evidence.

---

# Part C — Retired rows

**All 54 rows of the 1.0.0 matrix are retired, not deleted.** Each tested
the fixture-backed `/inbound` surface removed on 2026-09-01. Retained
because the reasoning is a fair record of what was and was not checked.

**Common retirement reason (R-FIX):** the row exercised
`lib/inbound/inbound-data.ts` with `INBOUND_DATA_SOURCE = "fixture"`. No
request was made; a passing row proved a placeholder resolved, not that any
contract held. Module, fixtures, `?state=` harness and the four `/inbound`
routes were all removed.

| Old # | Row | Old verdict | Retirement | Replaced by |
|---|---|---|---|---|
| 1–5 | `/inbound` list — 5 states | 1 NV, 4 PARTIAL | **RETIRED** — R-FIX; route removed | LIVE-25, LIVE-26 |
| 6–8 | `/inbound?config=` detail panel — 3 states | 2 NV, 1 PARTIAL | **RETIRED** — R-FIX; panel replaced by `/inbound-campaigns/[id]` | LIVE-05, LIVE-19…LIVE-22 |
| 9–14 | Wizard — 6 step states | 4 NV, 2 PARTIAL | **RETIRED** — R-FIX; both wizards removed (OQ-FE-09) | LIVE-01, LIVE-03, LIVE-04 |
| 15 | Wizard resume prompt | NV | **RETIRED** — draft store re-keyed onto `InboundCampaignInput`; the six-field shape it stored no longer exists | *(no live row — OQ-CFG-05 open)* |
| 16 | Wizard `no-permission` via `canCreateInbound` | NV | **RETIRED** — `inbound-access.ts` removed; role-string permissions had no backend basis | LIVE-25, LIVE-26, AUTO-13 |
| 17–24 | `/inbound/calls` — 7 states + no-permission | 3 NV, 5 PARTIAL | **RETIRED** — R-FIX; route removed, history is now a filter on `/calls` (reverses COL-07) | LIVE-33…LIVE-37 |
| 25–27 | Call detail panel — 3 states | 1 NV, 2 PARTIAL | **RETIRED** — R-FIX; replaced by `/calls/[id]` | LIVE-34, LIVE-35, LIVE-36 |
| 28–30 | Roles × `/inbound` routes | 3 NV | **RETIRED** — routes removed; capabilities now come from `/rbac/users/me/permissions`, not a role string | LIVE-25, LIVE-26, AUTO-13 |
| 31–36 | Call-history columns and sort | 6 NV | **RETIRED** — R-FIX; the shared `/calls` table supersedes them | LIVE-34, LIVE-37 |
| 37–43 | Filters, reset, pagination envelope | 7 VERIFIED | **RETIRED** — verified against the fixture filter module, which is removed. `GET /calls/` has no `search` or `sort` at all (OQ-CH-05, OQ-CH-10), so rows 37, 38 and 42 asserted client behaviour with no server counterpart | LIVE-33, LIVE-37 |
| 44–45 | Total-count line; prev/next bounds | 2 NV | **RETIRED** — R-FIX | *(covered by the shared `/calls` screen)* |
| 46–49 | Field-table integrity — 18 fields, defaults, messages, step gating | 4 VERIFIED | **RETIRED** — the 13-field wizard table is superseded by the 2.0.0 field tables, whose keys are the backend's own names | AUTO-12; forms spec §2 |
| 50–53 | Fixture containment — `fx_` prefix, single importer, no shape leak | 4 VERIFIED | **RETIRED** — **the rules held; the thing they protected is gone.** `structural-inbound-fixture-isolation.test.ts` removed with the fixtures. Superseded by a stricter rule: every rendered value comes from a backend response or a documented frontend-only field (forms spec §6) | forms spec §2.7, §6 |
| 54 | Unknown `?state=` falls back to default | VERIFIED | **RETIRED** — harness removed with no replacement (OQ-FE-06, decision R6) | *(none — deliberate loss)* |

**Retired totals:** 54 rows — 17 formerly VERIFIED, 14 PARTIAL, 23 NOT
VERIFIED. **No retired row is evidence for anything in Part A.**

---

# Part D — Acceptance scorecard

**UNSCORED.** Filled on the live staging pass.

Criteria are the 2.0.0 restatement of the original brief's DONE WHEN
clauses, re-pointed at the real contract.

## D.1 Freeze Inbound section, form and call-history requirements

| # | Criterion | Evidence to cite | Verdict |
|---|---|---|---|
| 1 | The spec exists and is marked frozen | `SPEC-inbound-v2.0.0-FROZEN.md` header | |
| 2 | Every screen listed with route and purpose | §4.1–§4.6 | |
| 3 | Every lifecycle transition listed with its server rule | §4.3, §6; `service:1601` | |
| 4 | Every call-history column named against its backend field | forms spec §2.8; `calls.py:177` | |
| 5 | Every filter control listed with allowed values and limitations | forms spec §2.8 | |
| 6 | Every named state listed, and the list matches the code | §5.5 register; AUTO-09 | |
| 7 | A reviewer can point at any screen or state **in the running frontend** and find it in the spec, and vice versa | Part A live pass | |

## D.2 Define form fields, states and API payloads

| # | Criterion | Evidence to cite | Verdict |
|---|---|---|---|
| 8 | Every field row carries all eight attributes with no blanks | forms spec §2.1–§2.8 | |
| 9 | Every field names its backend destination, or is marked frontend-only with a reason | forms spec §2, §2.7 | |
| 10 | Every screen state has trigger, output, controls and exit | forms spec §3 | |
| 11 | Every endpoint has method, path, permission, payload and response, cited to source | forms spec §4.2–§4.5 | |
| 12 | `Idempotency-Key` and `extra="forbid"` are stated as hard requirements | forms spec §1.1; LIVE-03, LIVE-04 | |
| 13 | Every backend error code maps to exactly one state | forms spec §5; AUTO-10, AUTO-11 | |
| 14 | Every unconfirmed item is marked OPEN with an owner | `OPEN-QUESTIONS.md` — 8 ANSWERED / 6 OPEN, all six backend-owned | |
| 15 | No placeholder, fixture or hard-coded response data remains | forms spec §6; `grep fx_` and `grep INBOUND_DATA_SOURCE\|inboundData` both zero | |

## D.3 Totals

| | |
|---|---|
| **PASS** | *(unscored)* |
| **FAIL** | *(unscored)* |
| **BLOCKED** | *(unscored)* |

**Do not fill D.3 from Part A's automated rows alone.** Criteria 7 and 15
are the two the 1.0.0 scorecard could not honestly close — criterion 7
because nothing had ever been rendered, criterion 15 because fixtures were
the whole data layer. Both are now answerable, but only by running Part A
against staging.

---

# Part E — Provenance

This document was produced by inspection of the frozen 2.0.0 specs, the
backend contract extract, and the backend sources cited in each row. **No
test was written, altered or removed to produce it, and no source file was
changed.** Every file:line reference was read at the time of writing.

Rows LIVE-09 and LIVE-10 were written as targets for behaviour that did not
exist on 2026-09-01. Both are now implemented and the rows read as regression
checks; G-1 and G-2 are closed accordingly.

## Revision 2.0.1 — 2026-09-02

Documentation and tests only. **No production source file was created,
edited or deleted, and no backend file was changed.** What this revision did:

- Closed **G-1** and **G-2** against the code that resolved them
  (`inbound-campaign-form.tsx:270-282` and `:43-59`), and rewrote the
  LIVE-09/LIVE-10 note that still called them targets.
- Closed **G-3** as resolved-in-code and named its new guard, **AUTO-16**.
  The write-time schema is still an opaque `dict`, so the guard is a test,
  and the end-to-end half stays open as **LIVE-47**.
- Added **AUTO-15 to AUTO-17** for three tests added the same day to
  `inbound-api.test.ts` and `inbound-campaign-form.test.ts`. Each was proved
  to bite by mutating the behaviour it guards and observing the failure.
- Added **LIVE-40 to LIVE-60** across new sections A.8 to A.12, covering the
  call-time behaviour the four inbound features produce. Every one is
  staging-owned, carries a backend file:line anchor and an explicit
  pass/fail condition, and **ships blank** like every other row.
- Recorded that A.10 and LIVE-60 are BLOCKED on precondition **P8**, naming
  `CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE` (`inbound_transfer.py:25`)
  as the code switch, not only the staging environment variable.

Still not done here, and not doable from the frontend: every LIVE row above
remains unrun. **The matrix stays UNSCORED.**

## Revision 2.0.2 — 2026-09-03

Frontend code, tests and this document only. **No backend file was read for
new claims beyond what LIVE-87 already cites, and no backend file was
changed.** What this revision did:

- Extracted the inbound-only sections of `/calls/[id]` (`CallPartiesPanel`,
  `InboundRouteSnapshot`, `InboundConsentStatePanel`) out of the route file
  into `src/components/calls/call-panels.tsx`, gaining direct component tests
  for a branch that previously had none — **AUTO-18, AUTO-19, AUTO-20**.
  `InboundRouteSnapshot` and `InboundConsentStatePanel` now also gate on
  `inbound:read`/`inbound:manage` via `getInboundCapabilities`, matching the
  gate `/inbound-campaigns` already used; caller identity (phone/ANI, Called
  DID) is deliberately **not** gated, per AUTO-18. Both new panels' tests
  were proved to bite by mutation (disabling the gate, observing the "renders
  nothing" row fail, then restoring).
- Added a DID filter and an inbound-campaign filter `<select>` to `/calls`,
  sourced client-side from the already-loaded page of calls
  (`distinctDidOptions`, `inboundCampaignFilterOptions` in
  `call-panels.tsx`) — no new endpoint, no new client method. Labeled by the
  wire's own `campaign_name`, falling back to the id itself, never a
  resolved or invented name — **AUTO-21**, also mutation-proved.
- Extended `tests/responsive.overlap.spec.ts` `appRoutes` with
  `/inbound-campaigns`, `/inbound-campaigns/new`,
  `/inbound-campaigns/campaign-001`, `/inbound-campaigns/campaign-001/edit`,
  reusing the same fixture-id convention the spec already used for
  `/campaigns/camp-001` and `/calls/call-001`. Closes the geometry half of
  **LIVE-85**; content-usability and real-data review stay manual, named in
  that row.
- Fixed the whole-file hang in `src/lib/billing-api.test.ts`
  (`renderWithQueryClient`'s per-call `QueryClient` was never cleared, so its
  default ~5-minute `gcTime` kept the process alive past the test that
  created it) by tracking and clearing every client the file creates.
  Confirmed standalone: this file went from never reaching a summary line to
  exiting cleanly in ~7s. Also cleared the `QueryClient`s created in
  `src/components/calls/conversation-review-panel.test.tsx` for the same
  reason — real cleanup, but **not** the fix for that file's own standalone
  hang: traced with `net.Socket.prototype.connect` instrumented to two
  named-pipe sockets the `tsx` loader opens to its own transform server at
  process start, unrelated to this file or its QueryClients and not
  resolvable by editing it. Confirmed this is not what breaks the aggregate
  `npm test`: in the full multi-file run this file's own tests report ✔ with
  no file-level failure — only `billing-api.test.ts` did, both before and
  independently of this finding.
- Added **A.19** (LIVE-81 to LIVE-87), covering the full create-to-detail
  workflow as one sequence, viewer/no-permission/unauthorized access per
  screen (naming which half AUTO-13/AUTO-19/AUTO-20 already prove and which
  needs a staging login), desktop/mobile layout per surface split into its
  automated-geometry and manual-content halves, and outbound-regression
  parity naming the exact recorded local-suite baseline. Every row ships
  blank, per the standing rule.

Local suite baseline this revision worked from: **368** tests before this
revision (365 pass, 1 fail — the `billing-api.test.ts` hang, 2 skipped),
read from a bounded `npm test` run's own summary line before any file in
this revision was edited. This revision adds 14 named tests (AUTO-18
through AUTO-21) and fixes the one pre-existing failure. Final `npm test`
after every change above, read to its own summary line: **381** tests,
**379** passed, **0** failed, **2** skipped — the process exited on its own
(no `timeout` kill needed), confirming `billing-api.test.ts` no longer holds
it open.
