# Inbound — open questions

Everything the frozen spec could not settle, and what has happened to it
since.

Opened 2026-08-31 alongside the 1.0.0 freeze. **Revised 2026-09-01** against
the inbound backend delivered by `origin/main` and recorded in
[BACKEND-CONTRACT-EXTRACT.md](./BACKEND-CONTRACT-EXTRACT.md).

---

## Status vocabulary

| Status | Meaning |
|---|---|
| **ANSWERED** | A backend contract now answers it. Cited with file and line. |
| **OPEN** | Still genuinely unanswered. Owner named. Cannot be closed from the frontend. |
| **RESOLVED** | Closed without a new backend contract — either the contract answers it *in the negative* (no such field exists), or a frontend decision settled it. |

## Scoreboard

| | Count | Ids |
|---|---|---|
| **ANSWERED** | **8** | OQ-CFG-01, OQ-CFG-02, OQ-CFG-03, OQ-CFG-04, OQ-CFG-06, OQ-CH-01, OQ-CH-02, OQ-CH-08 |
| **OPEN** | **7** | OQ-CFG-05, OQ-CH-03, OQ-CH-04, OQ-CH-05, OQ-CH-06, OQ-CH-07, OQ-REL-01 |
| **RESOLVED** | 14 | OQ-CFG-07 … OQ-CFG-16, OQ-CH-09, OQ-CH-10 |

**Six of the seven OPEN items are backend-owned;** none of those six can be
closed by frontend work, and they are listed with their owner in §2.1. The
seventh, **OQ-REL-01** (§5), is frontend-owned — a written rollback plan, not
a contract question — and must not be closed by inventing a value either.

---

## 1. Inbound configuration endpoints

> **CORRECTION — 2026-09-01.** This section previously stated: *"No inbound
> configuration endpoint exists in `backend/app/api/v1/endpoints/` — there is
> no `inbound.py` and no inbound route registered on the v1 router."*
>
> **That is false.** The module is named `inbound_campaigns.py`, not
> `inbound.py`. It declares fifteen tenant-facing routes under the
> `/inbound-campaigns` prefix
> (`backend/app/api/v1/endpoints/inbound_campaigns.py:41`) and is registered
> on the v1 router at `backend/app/api/v1/routes.py:83`. The original claim
> was reached by looking for a filename rather than by reading the router.

| ID | Endpoint | Status | Resolution |
|---|---|---|---|
| **OQ-CFG-01** | List inbound campaigns | **ANSWERED** | `GET /inbound-campaigns/` — `inbound_campaigns.py:101`, `INBOUND_READ`. Envelope is `{items, total}` (`schemas/inbound_campaigns.py:178`). **Unpaginated** — the only query parameter is `include_archived` (`inbound_campaigns.py:102`). It does **not** page like `GET /calls/`. |
| **OQ-CFG-02** | Create an inbound campaign | **ANSWERED** | `POST /inbound-campaigns/` — `inbound_campaigns.py:120`. Requires **both** `INBOUND_MANAGE` and `INBOUND_ASSIGN` (`:124`, `:128`) and a mandatory `Idempotency-Key` header, 8–255 chars (`:82`). Body is `InboundCampaignCreateRequest` (`schemas/inbound_campaigns.py:27`), which is **`extra="forbid"`** (`:16`) — any key not on the model is a 422. Required: `name`, `did_number`, `campaign_id`, `sip_trunk_id`. |
| **OQ-CFG-03** | Read one inbound campaign | **ANSWERED** | `GET /inbound-campaigns/{config_id}` — `inbound_campaigns.py:209`. Response `InboundCampaignResponse` (`schemas/inbound_campaigns.py:143`), 31 fields. `config_id` **must be a UUID** (`inbound_campaign_service.py:91`). |
| **OQ-CFG-04** | Inbound campaign status enum | **ANSWERED** | `CHECK (status IN ('draft', 'active', 'paused', 'archived'))`, default `'draft'` — `backend/Alembic/versions/0022_inbound_calling_foundation.py:459-460`. Database-enforced. |
| **OQ-CFG-05** | Wizard draft save / resume | **OPEN** | **Owner: backend.** No draft endpoint exists. A partial draft still lives in this browser's `localStorage` only and does not follow the user to another device, browser or cleared profile. The resume banner says so. Closing this needs a server-side draft contract. |
| **OQ-CFG-06** | Update an existing inbound campaign | **ANSWERED** | `PATCH /inbound-campaigns/{config_id}` (`:259`) and `PUT` (`:270`), both `INBOUND_MANAGE` + `Idempotency-Key`. Body `InboundCampaignUpdateRequest` (`schemas/inbound_campaigns.py:67`) with mandatory `expected_version >= 1` (`:68`). Editing an `active` campaign is refused with `pause_before_edit` (`inbound_campaign_service.py:1405`); an `archived` one with `campaign_archived` (`:1410`). Changing DID or trunk on this route is refused with `assignment_workflow_required` (`:1445`) — use `POST /{config_id}/assign` (`:283`). The base `campaign_id` cannot change at all (`campaign_change_forbidden`, `:1452`). |
| **OQ-CFG-07** | Knowledge upload | **RESOLVED** *(contract answers in the negative)* | **There is no inbound knowledge field of any kind.** `qualification_config` accepts exactly five keys — `purpose`, `persona`, `system_prompt`, `voice_id`, `silence_timeout_seconds` (`inbound_overrides.py:8-14`). No `knowledge_base_id`, no text, no upload target. Knowledge reaches an inbound call only through the base campaign named by `campaign_id`. The `.md`/`.txt` ≤10 MB validator described in 1.0.0 validated a file that was never uploaded anywhere. |
| **OQ-CFG-08** | Availability | **RESOLVED** *(by contract)* | Both halves answered. **Whose timezone:** an explicit `timezone` field, 1–64 chars, default `"UTC"` (`schemas/inbound_campaigns.py:32`), checked at activation by readiness `timezone_valid` (`inbound_campaign_service.py:712`). **Is one window enough:** no — the shape is a 7-day weekly schedule with multiple windows per day, carried in `business_hours` and checked by readiness `business_hours_valid` (`:722`). The 1.0.0 single global window was too narrow. |
| **OQ-CFG-09** | Tone | **RESOLVED** *(contract answers in the negative)* | The backend does not model tone. `professional` / `friendly` / `concise` were frontend-invented and map to nothing. The nearest real field is `qualification_config.persona`, a free-text behaviour description ≤2000 chars (`inbound_overrides.py:10`) that is prefixed `INBOUND AGENT STYLE` and layered over the approved base persona (`inbound_overrides.py:94-97`) — a different concept, not an enum. Dropped by decision D4. |

## 1a. The six-field campaign wizard — all resolved

Raised 2026-09-01 by the six-field brief, before the backend was read. Every
one is now settled by the contract or by an explicit decision. **None is a
frontend-owned open question any more.**

| ID | Field | Status | Resolution |
|---|---|---|---|
| **OQ-CFG-10** | `number` | **RESOLVED** *(by contract)* | It is `did_number`: a required top-level string on create (`schemas/inbound_campaigns.py:29`), strict E.164 `^\+[1-9]\d{6,14}$` (`:13`). **One** number per campaign, enforced by three partial unique indexes (`0022_inbound_calling_foundation.py:563`, `574`, `580`). The option list is `GET /tenant-phone-numbers/` (`tenant_phone_numbers.py:111`) filtered to `status = "verified"`, cross-checked per number against `GET /inbound-campaigns/dids/availability` (`inbound_campaigns.py:145`). The number is carried under the key **`e164`** (`domain/models/tenant_phone_number.py:38`). |
| **OQ-CFG-11** | `prompt` | **RESOLVED** *(by contract)* | It is `qualification_config.system_prompt`, **optional**, ≤20 000 chars (`inbound_overrides.py:11`). It is **appended to, never substituted for**, the approved base campaign instructions (`inbound_overrides.py:98`) — the outbound layering model, not a whole-prompt replacement. Not required: readiness `campaign_prompt` (`inbound_campaign_service.py:586`) is satisfied by the base campaign's own prompt or `script_config`. |
| **OQ-CFG-12** | `voice` | **RESOLVED** *(by contract, with a residual gap)* | It is `qualification_config.voice_id`, optional, a plain string ≤255 chars (`inbound_overrides.py:12`). No provider is chosen alongside it — the tenant TTS provider supplies that, and readiness `voice_configured` (`inbound_campaign_service.py:623`) requires both a voice and a provider. **Residual:** no endpoint lists selectable voices, so the control is free text rather than a picker. That is a UX gap, not a contract gap, and is not tracked as an open question. |
| **OQ-CFG-13** | prompt / voice / knowledge on a saved campaign | **RESOLVED** *(by contract)* | A read returns `qualification_config` verbatim (`inbound_campaign_service.py:1015`) — complete and unaltered, never summarised. Prompt and voice therefore round-trip exactly. Knowledge does not appear because it does not exist (OQ-CFG-07). |
| **OQ-CFG-14** | `knowledge` | **RESOLVED** *(decision D3)* | Knowledge is inherited from the base campaign. No inbound knowledge field is built, in paste or upload form. See OQ-CFG-07. |
| **OQ-CFG-15** | Wizard structure | **RESOLVED** *(decision D1)* | `/inbound-campaigns` is canonical. Its form is a single page carrying the full contract, not a stepped wizard over a subset. The 1.0.0 five-step wizard and the six-field wizard were both removed on 2026-09-01. The question of which wizard survives is moot: neither did. |
| **OQ-CFG-16** | Drafts in edit mode | **RESOLVED** *(scoped to OQ-CFG-05)* | Draft persistence remains create-mode-only and browser-local, so a half-finished edit cannot silently overwrite a saved campaign. Whether it should become server-side is OQ-CFG-05 and is not duplicated here. |

---

## 2. Call history endpoints

`GET /calls/` (`calls.py:1079`) and `GET /calls/{call_id}` (`calls.py:1260`)
are confirmed. Three of the ten questions below are now answered by them.

| ID | Field | Status | Resolution |
|---|---|---|---|
| **OQ-CH-01** | `direction` | **ANSWERED** | `direction: Literal["inbound", "outbound"]` — `calls.py:1084`. Additionally `inbound_campaign_id: UUID` — `calls.py:1087` — which implies `direction=inbound` and resolves against the pinned route snapshot rather than `campaign_id` (`calls.py:1128-1130`). **This was the single item keeping the call-history screen on fixtures. It is closed.** |
| **OQ-CH-02** | caller number | **ANSWERED** | `from_number` (`calls.py:183`), `caller_ani` (`:201`) and `called_did` (`:202`) all exist on `CallListItem`. Inbound ANI is exposed only when the carrier did not mark it private (`_display_caller_ani`, `calls.py:43-44`), and the source number is direction-projected server-side (`_display_from_number`, `:83-84`). |
| **OQ-CH-03** | `status` enum | **OPEN** | **Owner: backend.** Still declared as a bare `str` (`calls.py:185`) with no enum. The filter's options must stay runtime-supplied; no component may hard-code a status value. |
| **OQ-CH-04** | `outcome` enum | **OPEN** | **Owner: backend.** Still `Optional[str]` (`calls.py:187`) with no enum. Rendered verbatim; nothing branches on it. |
| **OQ-CH-05** | sort | **OPEN** | **Owner: backend.** Verified absent on the handler signature (`calls.py:1080-1093`): no `sort`, no `order`. Column sorting is therefore page-local and wrong across pages. |
| **OQ-CH-06** | transcript response shape | **OPEN** | **Owner: backend.** `GET /calls/{call_id}/transcript` (`calls.py:1438`) still declares no `response_model`. The `format` parameter's enum (`json` / `text`) still appears only in prose. |
| **OQ-CH-07** | summary response shape | **OPEN** | **Owner: backend.** `GET /calls/{call_id}/summary` (`calls.py:1526`) still declares no `response_model`. |
| **OQ-CH-08** | answering inbound campaign | **ANSWERED** | `inbound_campaign_id` (`calls.py:203`) plus `assignment_id` (`:204`), `route_id` (`:205`), `route_version` (`:206`), `config_version` (`:207`) and `config_checksum` (`:208`). Derived from the route snapshot pinned at admission (`_inbound_config_id`, `calls.py:66`), **not** from `campaign_id`. |
| **OQ-CH-09** | `page_size` | **RESOLVED** *(by contract)* | Two different lists, two different answers. `GET /calls/` pages with `page` ≥ 1 and `page_size` 1–100, default 20 (`calls.py:1081-1082`). The inbound **campaign** list does not page at all (OQ-CFG-01). The 1.0.0 fixed page size of 25 was a frontend choice about a list that no longer exists in that form. |
| **OQ-CH-10** | caller search | **RESOLVED** *(as a documented limitation)* | Verified absent on the handler signature: `GET /calls/` has **no search parameter of any kind**. This is not reopened as a backend request; it is recorded as a stated limitation — the search box filters the current page only, and the UI must say so. Distinct from OQ-CH-05, which is about ordering. |

### 2.1 The six residual OPEN items

Every one is a backend decision. None can be closed by frontend work, and
none may be closed by inventing a frontend-side value.

| ID | What is needed | Owner | Frontend behaviour until then |
|---|---|---|---|
| **OQ-CFG-05** | A server-side wizard-draft contract | Backend | Drafts stay in `localStorage`; the limitation is stated in the UI |
| **OQ-CH-03** | Publish the call `status` enum | Backend | Filter options stay runtime-supplied; no hard-coded value |
| **OQ-CH-04** | Publish the call `outcome` enum | Backend | Rendered verbatim; nothing branches on it |
| **OQ-CH-05** | Add a `sort` / `order` parameter | Backend | Sorting is page-local; the UI must not imply otherwise |
| **OQ-CH-06** | Declare the transcript `response_model` | Backend | Transcript rendering stays defensive |
| **OQ-CH-07** | Declare the summary `response_model` | Backend | Summary rendering stays defensive |

---

## 3. Fixtures — removed

> **CLOSED — 2026-09-01.** The placeholder fixtures this section catalogued
> were removed along with the fixture-backed `/inbound` surface. The
> `fx_`-prefix rule, the single-importer rule and the structural test that
> enforced them are all gone with them, because there is no longer any
> fixture to contain.

Every fixture field listed here previously stood in for a contract that did
not exist. The mapping that replaced each one:

| Former fixture field | Replaced by |
|---|---|
| `fx_id` | `InboundCampaignResponse.id` — a UUID (`schemas/inbound_campaigns.py:144`) |
| `fx_name` | `name` (`:146`) |
| `fx_business` | *nothing* — no such field; dropped by decision D4 |
| `fx_agent` | `qualification_config.persona` — a behaviour description, not a spoken name |
| `fx_state_label` | `status`, now a closed four-value enum (OQ-CFG-04) |
| `fx_updated` | `updated_at` (`:175`) |
| `fx_description` | `qualification_config.purpose` (`inbound_overrides.py:9`) |
| `fx_greeting` | `greeting`, ≤2000 chars (`schemas/inbound_campaigns.py:165`) |
| `fx_tone_label` | *nothing* — see OQ-CFG-09 |
| `fx_knowledge_blurb`, `fx_knowledge` | *nothing* — see OQ-CFG-07 |
| `fx_availability_blurb` | `business_hours` + `timezone` — see OQ-CFG-08 |
| `fx_after_hours_line` | `business_hours.after_hours_message`, required when `after_hours_action = "voicemail"` (readiness `after_hours_message_valid`, `inbound_campaign_service.py:739`) |
| `fx_number` | `did_number`, strict E.164 — see OQ-CFG-10 |
| `fx_prompt` | `qualification_config.system_prompt` — see OQ-CFG-11 |
| `fx_voice` | `qualification_config.voice_id` — see OQ-CFG-12 |
| `FIXTURE_INBOUND_NUMBER_LABELS` | `GET /tenant-phone-numbers/` + `GET /inbound-campaigns/dids/availability` |
| `FIXTURE_INBOUND_VOICE_LABELS` | *no endpoint* — free-text `voice_id`; see OQ-CFG-12 |
| `fx_call_ref` | `CallListItem.id` |
| `fx_received` | `timestamp` |
| `fx_from_digits` | `from_number` / `caller_ani` — see OQ-CH-02 |
| `fx_answered_by` | `inbound_campaign_id` + `campaign_name` — see OQ-CH-08 |
| `fx_result_label` | `outcome` — still enum-less, see OQ-CH-04 |
| `fx_seconds` | `duration_seconds` |
| `fx_recap` | `summary` — shape still open, see OQ-CH-07 |
| `fx_turns`, `fx_who`, `fx_line` | `GET /calls/{call_id}/transcript` — shape still open, see OQ-CH-06 |
| `FIXTURE_CALL_STATUS_LABELS` | *no enum published* — see OQ-CH-03 |

---

## 4. Frontend decisions taken under the freeze

| ID | Decision | Status |
|---|---|---|
| **OQ-FE-01** | Inbound is one sidebar entry; call history is a tab inside it | **SUPERSEDED.** One sidebar entry survives, pointing at `/inbound-campaigns`. Inbound call history is a direction filter on the shared `/calls` screen, not a tab. This reverses collision COL-07, which had declined to generalise `/calls`; the pulled backend added `direction` and made generalising the correct call. |
| **OQ-FE-02** | Screens load through `useEffect` + local state rather than TanStack Query | **SUPERSEDED.** The live contract landed, and with it the cache, refetch and optimistic-concurrency behaviour the note anticipated. The canonical surface uses TanStack Query. |
| **OQ-FE-03** | Wizard drafts in `localStorage` | **RETAINED**, re-keyed onto the real create/update payload. Still browser-local — see OQ-CFG-05. |
| **OQ-FE-04** | Page size fixed at 25 | **SUPERSEDED.** See OQ-CH-09. |
| **OQ-FE-05** | `no-permission` forked into `components/inbound/` | **SUPERSEDED.** The fork was removed; the canonical surface uses a single permission state derived from the server's effective permissions. |
| **OQ-FE-06** | `?state=<scenario>` honoured on inbound routes | **CLOSED — removed with no replacement** (decision R6). No mock transport was built. Named states are now reached only by exercising the real API. |
| **OQ-FE-07** | Read-model validators defined but not wired | **CLOSED.** The validators described a fixture-era model. They were removed rather than rewritten, because the backend already publishes Pydantic schemas and a second hand-maintained catalogue of the same shapes would drift. |
| **OQ-FE-08** | Only the campaign wizard used the reusable field components | **CARRIED FORWARD.** The field components survive; adopting them in the canonical form is follow-up work, not part of this task. |
| **OQ-FE-09** | The 13-field wizard left in the tree, unmounted | **CLOSED.** *"Whichever wizard loses, delete the other rather than leaving two."* Both lost. Both deleted. |
| **OQ-FE-10** | The detail panel gained an Edit action | **SUPERSEDED.** Editing is a real route, gated on `INBOUND_MANAGE` and on the campaign not being `active` or `archived` (OQ-CFG-06). |
| **OQ-FE-11** | `number` and `voice` selects show an empty "Select…" row | **SUPERSEDED.** `did_number` is chosen from verified inventory; `voice_id` is free text with an inherit-from-campaign placeholder. |

---

## 5. Release readiness

| ID | Question | Status | Resolution |
|---|---|---|---|
| **OQ-REL-01** | Frontend rollback plan / version pin | **OPEN.** Owner: frontend, before any release freeze. | Verified against the current repo (2026-09-04): no rollback plan, prior-version pin, or compatibility note for the inbound frontend exists anywhere under `Talk-Leee/` — not in this file, not in `docs/`, not in `package.json`. Nothing here asserts the current inbound UI is compatible with an older deployed backend contract, or names what to redeploy if a release must be reverted. Needs, before a frontend commit freeze: (1) the git ref/tag this frontend build pins against, (2) whether that build is safe to run against the previous backend release (given the contract dependencies catalogued in §1 and §2 above — a rollback that reverts the backend but not the frontend would put the UI in front of endpoints/fields it no longer expects), and (3) the exact redeploy steps. This is a written decision, not a code change, and is not resolved by anything in this document's other sections. |
