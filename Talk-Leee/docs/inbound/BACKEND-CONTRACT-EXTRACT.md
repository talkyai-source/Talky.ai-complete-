# Inbound — backend contract extract

**Purpose.** The authoritative record of what the inbound backend actually
accepts and returns, extracted from source. This is the input to
`SPEC-inbound-v2.0.0-FROZEN.md` and
`SPEC-inbound-forms-states-api-v2.0.0-FROZEN.md`.

**Rule.** Every row below cites a file and a line. Nothing here is inferred
from a filename, a route name or a variable name. Where the frozen 1.0.0
specification and this document disagree, **this document is correct** — it
was read from the running contract, the 1.0.0 specification was written
before that contract existed.

**Read-only.** No backend file was modified to produce this. Line numbers are
as of the extraction date below.

- Extracted: 2026-09-01
- Backend root: `backend/`
- Frontend root: `Talk-Leee/`

---

## 1. Router registration

| Item | Source | Value |
|---|---|---|
| Router prefix | `backend/app/api/v1/endpoints/inbound_campaigns.py:41` | `/inbound-campaigns`, tag `Inbound Campaigns` |
| Registered on v1 | `backend/app/api/v1/routes.py:83` | `api_router.include_router(inbound_campaigns_router)` |
| Import | `backend/app/api/v1/routes.py:37` | `from app.api.v1.endpoints.inbound_campaigns import router as inbound_campaigns_router` |

> This single fact retires the 1.0.0 claim that "no inbound configuration
> endpoint exists in `backend/app/api/v1/endpoints/` — there is no
> `inbound.py` and no inbound route registered on the v1 router."
> The module is named `inbound_campaigns.py`, and it is registered.

---

## 2. Tenant-facing routes

All paths below are relative to the v1 API base and the `/inbound-campaigns`
prefix in §1.

| # | Method + path | Handler line | Permission | Idempotency-Key | Response model |
|---|---|---|---|---|---|
| R1 | `GET /` | `inbound_campaigns.py:101` | `INBOUND_READ` | no | `InboundCampaignListResponse` |
| R2 | `POST /` | `inbound_campaigns.py:120` | `INBOUND_MANAGE` **and** `INBOUND_ASSIGN` | **required** | `InboundCampaignResponse`, 201 |
| R3 | `GET /dids/availability` | `inbound_campaigns.py:145` | `INBOUND_READ` | no | `InboundDidAvailabilityResponse` |
| R4 | `GET /controls` | `inbound_campaigns.py:159` | `INBOUND_CONTROLS` | no | `TenantInboundControlsResponse` |
| R5 | `PATCH /controls` | `inbound_campaigns.py:170` | `INBOUND_CONTROLS` | **required** | `TenantInboundControlsResponse` |
| R6 | `GET /capabilities` | `inbound_campaigns.py:191` | `INBOUND_READ` | no | `InboundRuntimeCapabilitiesResponse` |
| R7 | `GET /{config_id}` | `inbound_campaigns.py:209` | `INBOUND_READ` | no | `InboundCampaignResponse` |
| R8 | `GET /{config_id}/readiness` | `inbound_campaigns.py:223` | `INBOUND_READ` | no | `InboundReadiness` |
| R9 | `PATCH /{config_id}` | `inbound_campaigns.py:259` | `INBOUND_MANAGE` | **required** | `InboundCampaignResponse` |
| R10 | `PUT /{config_id}` | `inbound_campaigns.py:270` | `INBOUND_MANAGE` | **required** | `InboundCampaignResponse` |
| R11 | `POST /{config_id}/assign` | `inbound_campaigns.py:283` | `INBOUND_ASSIGN` | **required** | `InboundCampaignResponse` |
| R12 | `POST /{config_id}/activate` | `inbound_campaigns.py:331` | `INBOUND_MANAGE` | **required** | `InboundCampaignResponse` |
| R13 | `POST /{config_id}/pause` | `inbound_campaigns.py:349` | `INBOUND_MANAGE` | **required** | `InboundCampaignResponse` |
| R14 | `POST /{config_id}/deactivate` | `inbound_campaigns.py:350` | `INBOUND_MANAGE` | **required** | `InboundCampaignResponse` |
| R15 | `POST /{config_id}/archive` | `inbound_campaigns.py:368` | `INBOUND_MANAGE` | **required** | `InboundCampaignResponse` |

Notes taken from source, not assumed:

- **R13 and R14 are the same handler.** Two decorators stack on one function
  (`inbound_campaigns.py:349-351`), both targeting status `paused`.
- **R2 carries two permission dependencies.** `INBOUND_ASSIGN` on the route
  decorator (`inbound_campaigns.py:124`) and `INBOUND_MANAGE` on the `user`
  parameter (`inbound_campaigns.py:128`). A caller needs **both**.
- **R3, R4, R5, R6 are literal paths declared before `/{config_id}`**, with a
  source comment at `inbound_campaigns.py:144` stating exactly that. A client
  must not treat `controls`, `capabilities` or `dids` as a config id.
- **`{config_id}` must be a UUID.** Coerced by `_uuid(config_id, "config_id")`
  — `inbound_campaign_service.py:91`, raising `invalid_identifier` (400) at
  `inbound_campaign_service.py:96`.

### 2.1 Idempotency-Key

| Item | Source | Value |
|---|---|---|
| Header name | `inbound_campaigns.py:82` | `Idempotency-Key`, required on every mutation |
| Length bound | `inbound_campaigns.py:83` | `8 <= len(value) <= 255` |
| Violation | `inbound_campaigns.py:85-90` | 400, `{"code": "invalid_idempotency_key"}` |

A mutation sent without this header is rejected by FastAPI before the handler
runs. There is no default and no fallback.

---

## 3. Request payloads

Base class `_StrictModel` — `schemas/inbound_campaigns.py:16` —
`model_config = ConfigDict(extra="forbid")`.

> **`extra="forbid"` applies to every request model in this file.** Any key
> not listed below causes a 422 before the handler is reached. The frontend
> must send exactly these keys and no others.

### 3.1 `InboundCampaignCreateRequest` — `schemas/inbound_campaigns.py:27`

| Field | Line | Type | Required | Default | Constraint |
|---|---|---|---|---|---|
| `name` | 28 | str | **yes** | — | 1–255, stripped (53) |
| `did_number` | 29 | str | **yes** | — | strict E.164 via `_normalize_e164` (44) |
| `campaign_id` | 30 | str | **yes** | — | UUID coerced service-side |
| `sip_trunk_id` | 31 | str | **yes** | — | UUID coerced service-side |
| `timezone` | 32 | str | no | `"UTC"` | 1–64, stripped (53) |
| `after_hours_action` | 33 | Literal | no | `"hangup"` | `hangup` \| `voicemail` \| `transfer` |
| `transfer_number` | 34 | str? | no | `None` | ≤32; E.164 when non-empty (46) |
| `recording_enabled` | 35 | bool | no | `False` | — |
| `consent_message` | 36 | str? | no | `None` | ≤2000 |
| `opening_mode` | 37 | Literal | no | `"caller_first"` | `caller_first` \| `agent_first` |
| `greeting` | 38 | str? | no | `None` | **≤2000** |
| `business_hours` | 39 | dict | no | `{}` | **opaque — see §5** |
| `recording_policy` | 40 | dict | no | `{}` | **opaque — see §5** |
| `transfer_policy` | 41 | dict | no | `{}` | **opaque — see §5** |
| `qualification_config` | 42 | dict | no | `{}` | **whitelisted — see §6** |

Cross-field rules — `_policy_requirements`, `schemas/inbound_campaigns.py:58`:

| Rule | Line | Effect |
|---|---|---|
| `after_hours_action == "transfer"` ⇒ `transfer_number` non-empty | 60-61 | else `ValueError` → 422 |
| `recording_enabled` ⇒ `consent_message` non-empty | 62-63 | else `ValueError` → 422 |

**E.164 pattern** — `schemas/inbound_campaigns.py:13`:
`^\+[1-9]\d{6,14}$`. Rejection message: "DID must be strict E.164
(+ followed by 7-15 digits)" (`schemas/inbound_campaigns.py:22`).

### 3.2 `InboundCampaignUpdateRequest` — `schemas/inbound_campaigns.py:67`

Every field of §3.1 repeated as `Optional`, plus:

| Field | Line | Type | Required | Constraint |
|---|---|---|---|---|
| `expected_version` | 68 | int | **yes** | `>= 1` |
| `reason` | 69 | str? | no | ≤1000 |

Handlers call `payload.model_dump(exclude_unset=True)`
(`inbound_campaigns.py:251`) — an omitted key is not "set to null", it is not
sent. This is what lets the frontend `PUT` a body without `did_number` and
`sip_trunk_id` without clearing them.

### 3.3 `InboundVersionRequest` — `schemas/inbound_campaigns.py:104`

Used by R12–R15. `expected_version: int >= 1` (105), `reason: str? ≤1000` (106).

### 3.4 `InboundDidAssignmentRequest` — `schemas/inbound_campaigns.py:109`

`did_number` (110, E.164 via 115), `sip_trunk_id` (111),
`expected_version >= 1` (112), `reason: str? ≤1000` (113).

### 3.5 `TenantInboundControlsPatch` — `schemas/inbound_campaigns.py:118`

`inbound_enabled: bool` (119), `expected_version: int >= 1` (120),
`reason: str` **min_length 3**, max 1000 (121). The reason is **mandatory and
non-trivial** here, unlike every other `reason` in this file.

---

## 4. Response payloads

### 4.1 `InboundCampaignResponse` — `schemas/inbound_campaigns.py:143`

Serialised by `_serialize_bundle` —
`inbound_campaign_service.py:988`. 31 fields:

| Field | Schema line | Serialiser line | Type | Note |
|---|---|---|---|---|
| `id` | 144 | 990 | str | `config_id` |
| `tenant_id` | 145 | 991 | str | |
| `name` | 146 | 992 | str | |
| `status` | 147 | 993 | str | enum in §7 |
| `version` | 148 | 994 | int | optimistic-concurrency token |
| `config_version` | 149 | 995 | int | same value as `version` |
| `config_checksum` | 150 | 996 | str | |
| `did_number` | 151 | 997 | str | **raw E.164, not masked** — `canonical_did` |
| `assignment_id` | 152 | 998 | str | |
| `assignment_status` | 153 | 999 | str | enum in §7 |
| `assignment_version` | 154 | 1000 | int | |
| `campaign_id` | 155 | 1001 | str | base campaign |
| `campaign_name` | 156 | 1002 | str? | |
| `sip_trunk_id` | 157 | 1003 | str | |
| `sip_trunk_name` | 158 | 1004 | str? | |
| `timezone` | 159 | 1005 | str | |
| `after_hours_action` | 160 | 1006 | Literal | `hangup`\|`voicemail`\|`transfer` |
| `transfer_number` | 161 | 1007 | str? | |
| `recording_enabled` | 162 | 1008 | bool | |
| `consent_message` | 163 | 1009 | str? | |
| `opening_mode` | 164 | 1010 | Literal | `caller_first`\|`agent_first` |
| `greeting` | 165 | 1011 | str? | |
| `business_hours` | 166 | 1012 | dict | echoed verbatim |
| `recording_policy` | 167 | 1013 | dict | echoed verbatim |
| `transfer_policy` | 168 | 1014 | dict | echoed verbatim |
| `qualification_config` | 169 | 1015 | dict | echoed verbatim |
| `readiness` | 170 | 1016 | `InboundReadiness` | recomputed per response |
| `last_call_at` | 171 | 1017 | datetime? | |
| `last_error` | 172 | 1018 | str? | |
| `active_at` | 173 | 1019 | datetime? | |
| `created_at` | 174 | 1020 | datetime | |
| `updated_at` | 175 | 1021 | datetime | |

**Two facts the frontend must not get wrong:**

1. **`did_number` is the raw number.** A `mask_did` helper exists at
   `inbound_campaign_service.py:201` but **is not called by
   `_serialize_bundle`**. Masking for display is the frontend's
   responsibility.
2. **There is no `phone_number` object.** The DID arrives flat, as
   `did_number` + `assignment_id` + `assignment_status` +
   `assignment_version`.

### 4.2 `InboundCampaignListResponse` — `schemas/inbound_campaigns.py:178`

`{ items: InboundCampaignResponse[], total: int }` — lines 179-180.
Built at `inbound_campaigns.py:113`: `{"items": items, "total": len(items)}`.

> **Not the `/calls/` envelope.** There is no `page` and no `page_size`. The
> only query parameter is `include_archived` (`inbound_campaigns.py:102`,
> default `False`). **The inbound campaign list is unpaginated.**

### 4.3 `InboundReadiness` — `schemas/inbound_campaigns.py:137`

| Field | Line | Type |
|---|---|---|
| `ready` | 138 | bool |
| `checks` | 139 | `InboundReadinessCheck[]` |
| `blockers` | 140 | `InboundReadinessBlocker[]`, default `[]` |

`InboundReadinessCheck` (`:124`): `key` (125), `label` (126), `passed` (127),
`detail` (128) — all required. `InboundReadinessBlocker` (`:131`): `code`
(132), `message` (133), `remediation` (134) — all required.

> **There is no `checked_at` field.** A frontend reading one will always get
> `null`.

### 4.4 `InboundDidAvailabilityResponse` — `schemas/inbound_campaigns.py:183`

`did_number` (184), `available: bool` (185), `reason: str` (186),
`owned_by_current_tenant: bool = False` (187),
`conflicting_assignment_id: str? = None` (188).

### 4.5 `TenantInboundControlsResponse` — `schemas/inbound_campaigns.py:191`

`inbound_enabled: bool` (192), `version: int` (193), `reason: str?` (194),
`updated_at: datetime?` (195).

### 4.6 `InboundRuntimeCapabilitiesResponse` — `schemas/inbound_campaigns.py:198`

`transfer_runtime_available: bool` (199),
`transfer_platform_enabled: bool` (200),
`transfer_configuration_available: bool` (201).

---

## 5. The four opaque JSON blobs

`business_hours`, `recording_policy`, `transfer_policy` and
`qualification_config` are declared `dict[str, Any]`
(`schemas/inbound_campaigns.py:39-42`), stored as `jsonb`
(`inbound_campaign_service.py:1264-1266`) and returned through `_json_obj`
(`inbound_campaign_service.py:1012-1015`).

**Consequence, and it must be stated in the specification:** except for
`qualification_config` (§6), **nothing on the server validates the shape of
these blobs at write time.** Whatever the frontend sends is stored and echoed
back. The frontend therefore *owns* these shapes, and they are only checked
later, at readiness (§8):

| Blob | Key the frontend writes | Where it is read back |
|---|---|---|
| `business_hours` | `weekly_schedule`, `holiday_policy`, `after_hours_message` | readiness `business_hours_valid` (722), `after_hours_message_valid` (739) |
| `transfer_policy` | `enabled`, `destinations`, `failure_action`, `max_attempts`, `max_hops`, `max_call_duration_seconds` | readiness `after_hours_complete` (772), `max_call_duration_valid` (873) |
| `recording_policy` | `enabled`, `consent_message` | readiness `recording_consent` (886) |

`transfer_number` and `recording_enabled`/`consent_message` also exist as
**top-level** fields (§3.1). The top-level values are the ones the schema
validates; the blob copies are what readiness inspects.

---

## 6. `qualification_config` — the only whitelisted blob

`validate_qualification_overrides` —
`backend/app/domain/services/telephony/inbound_overrides.py:30`.

| Key | Line | Type | Limit |
|---|---|---|---|
| `purpose` | 9 | str | ≤ 2 000 |
| `persona` | 10 | str | ≤ 2 000 |
| `system_prompt` | 11 | str | ≤ 20 000 |
| `voice_id` | 12 | str | ≤ 255 |
| `silence_timeout_seconds` | 14 | number | 3.0 ≤ x ≤ 60.0 (`inbound_overrides.py:58`) |

`_SUPPORTED` is exactly these five (`inbound_overrides.py:14`). Any other key
is collected into `unsupported` (`inbound_overrides.py:46-48`) and surfaces
through readiness check `qualification_runtime_supported` (918).

**Neutral values are dropped, not stored** — `_neutral`,
`inbound_overrides.py:17`:

- `None`, `""`, `[]`, `{}`, or whitespace-only string → skipped (18-21)
- `silence_timeout_seconds == 8.0` → skipped (22-24)

> A frontend that always sends `silence_timeout_seconds: 8` is sending a
> value the server discards. Omitting it is equivalent and cheaper.

**No knowledge key exists.** There is no `knowledge_base_id`, no
`knowledge_text`, no upload target. Knowledge reaches an inbound call only
through the base campaign identified by `campaign_id`. This closes
OQ-CFG-07, OQ-CFG-13 and OQ-CFG-14 in the negative.

How overrides are applied at call time — `apply_qualification_overrides`,
`inbound_overrides.py:74`: `voice_id` replaces (86-87); `purpose` becomes a
prefixed `INBOUND CALL PURPOSE` block and sets `goal` (89-93); `persona`
becomes a prefixed `INBOUND AGENT STYLE` block (94-97); `system_prompt` is
**appended, not substituted** (98).

---

## 7. Status enums — now closed

### 7.1 `inbound_campaign_configs.status`

`backend/Alembic/versions/0022_inbound_calling_foundation.py:459-460`:

```
status VARCHAR(16) NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'active', 'paused', 'archived'))
```

**Closes OQ-CFG-04.** Four values, database-enforced, default `draft`.

### 7.2 `inbound_did_assignments.status`

`0022_inbound_calling_foundation.py:521-522`:

```
status VARCHAR(16) NOT NULL DEFAULT 'paused'
    CHECK (status IN ('active', 'paused', 'quarantined', 'archived'))
```

### 7.3 DID uniqueness — the one-active-assignment rule

| Index | Line | Rule |
|---|---|---|
| `uq_inbound_active_canonical_did` | 563-564 | one `active` assignment per DID |
| `uq_inbound_live_canonical_did` | 574-576 | one **non-archived** assignment per DID |
| `uq_inbound_live_config_assignment` | 580-581 | one non-archived assignment per config |

Service-side friendly check `_assert_did_free` —
`inbound_campaign_service.py:1120`, raising `did_assignment_conflict` (1143).
The source comment at 567-570 states the index, not the service check, is the
race-safe authority.

---

## 8. Readiness — 28 checks

`_readiness` — `inbound_campaign_service.py:493`. `ready` is
`not blockers` (983); every failed check becomes a blocker (973-981).

| # | Key | Line | # | Key | Line |
|---|---|---|---|---|---|
| 1 | `campaign_direction` | 499 | 15 | `did_unambiguous` | 697 |
| 2 | `campaign_active` | 518 | 16 | `timezone_valid` | 712 |
| 3 | `tenant_active` | 535 | 17 | `business_hours_valid` | 722 |
| 4 | `tenant_inbound_enabled` | 546 | 18 | `after_hours_message_valid` | 739 |
| 5 | `platform_inbound_enabled` | 557 | 19 | `after_hours_complete` | 772 |
| 6 | `platform_settlement_enabled` | 568 | 20 | `after_hours_runtime_supported` | 791 |
| 7 | `campaign_prompt` | 586 | 21 | `transfer_trunk_bidirectional` | 815 |
| 8 | `ai_providers_configured` | 603 | 22 | `transfer_runtime_supported` | 833 |
| 9 | `voice_configured` | 623 | 23 | `platform_transfer_enabled` | 853 |
| 10 | `pipeline_mode_valid` | 635 | 24 | `max_call_duration_valid` | 873 |
| 11 | `did_verified` | 645 | 25 | `recording_consent` | 886 |
| 12 | `trunk_ready` | 656 | 26 | `platform_recording_enabled` | 895 |
| 13 | `billing_quota_configured` | 664 | 27 | `qualification_runtime_supported` | 918 |
| 14 | `concurrency_policy_configured` | 675 | 28 | `did_not_quarantined` | 686 |

Two checks the specification must describe accurately because they change
what the frontend may require of the user:

- **`campaign_prompt` (586).** Satisfied by *any* of: an override
  `system_prompt` (582), the base campaign's `system_prompt` (583), or a
  non-empty `script_config` (584). The inbound form does **not** have to
  collect a prompt.
- **`voice_configured` (623).** Satisfied by an override `voice_id`, the base
  campaign `voice_id`, or `tenant_tts_voice_id` (613-617) — **and**
  additionally requires a TTS provider (620-622).

---

## 9. Lifecycle transitions

`set_lifecycle` — `inbound_campaign_service.py:1601`.

| Rule | Line | Code | Status |
|---|---|---|---|
| Target ∈ `{active, paused, archived}` | 1613-1614 | `invalid_status` | 400 |
| `expected_version >= 1` | 1623-1626 | `expected_version_required` | 422 |
| Version must match current | 1646-1649 | `version_conflict` | 409 |
| Archive only from `draft`/`paused` | 1653-1657 | `pause_before_archive` | 409 |
| Archived is immutable | 1658-1662 | `campaign_archived` | 409 |
| Quarantined assignment blocks all | 1668-1672 | `assignment_quarantined` | 409 |
| Archived assignment is immutable | 1673-1677 | `assignment_archived` | 409 |

`update_campaign` — `inbound_campaign_service.py:1351`:

| Rule | Line | Code |
|---|---|---|
| Cannot edit while `active` | 1402-1406 | `pause_before_edit` (409) |
| Cannot edit when `archived` | 1408-1411 | `campaign_archived` (409) |
| DID/trunk change needs `POST /assign` | 1443-1447 | `assignment_workflow_required` (409) |
| Base campaign cannot change | 1448-1454 | `campaign_change_forbidden` (409) |

> `assignment_workflow_required` is why a `PUT` body must omit `did_number`
> and `sip_trunk_id`: sending an unchanged value is safe, but sending a
> *changed* one on `PUT` instead of `POST /assign` is a 409.

`create_campaign` — `inbound_campaign_service.py:1146`:

| Rule | Line | Code |
|---|---|---|
| DID must parse as E.164 | 1160-1161 | `invalid_did` (422) |
| `name` non-empty after strip | 1173-1174 | `invalid_name` (422) |
| transfer ⇒ `transfer_number` | 1176-1181 | `incomplete_after_hours` (422) |
| recording ⇒ `consent_message` | 1182-1187 | `missing_recording_consent` (422) |
| Outbound base campaign must be an unused `draft` | 1213-1237 | `campaign_direction_conflict` (409) |
| One inbound config per campaign | 1245-1254 | `config_already_exists` (409) |

---

## 10. Error taxonomy

### 10.1 Envelope

`_raise_service` — `inbound_campaigns.py:94-99`:

```
{"code": <exc.code>, "message": <str(exc)>}
```
placed in FastAPI's `detail`, with `readiness` added when the exception is an
`InboundReadinessError` (`inbound_campaigns.py:96-97`).

### 10.2 Class → HTTP status

| Class | Line | Status | Default code |
|---|---|---|---|
| `InboundCampaignError` | 36 | 400 | `inbound_error` |
| `InboundNotFoundError` | 45 | **404** | `not_found` |
| `InboundConflictError` | 50 | **409** | `version_conflict` |
| `InboundReadinessError` | 55 | **409** | `not_ready` + `readiness` payload |

### 10.3 Codes the tenant-facing surface can emit

Endpoint-level, raised before the service:

| Code | Line | Status |
|---|---|---|
| `authorization_unavailable` | `inbound_campaigns.py:68` | **503** |
| `permission_denied` (+ `required`) | `inbound_campaigns.py:74` | **403** |
| `invalid_idempotency_key` | `inbound_campaigns.py:86` | **400** |
| *(no code, plain detail)* Tenant context required | `inbound_campaigns.py:47` | **403** |

Service-level:

| Code | Line | Status |
|---|---|---|
| `not_found` | 47 | 404 |
| `not_ready` | 57 | 409 + readiness |
| `invalid_identifier` | 96 | 400 |
| `invalid_timezone` | 106 | 400 |
| `transfer_runtime_unavailable` | 159 | 400 |
| `transfer_staging_scope_mismatch` | 186 | 400 |
| `transfer_platform_disabled` | 197 | 400 |
| `idempotency_race` | 386 | 409 |
| `idempotency_mismatch` | 390 | 409 |
| `idempotency_in_progress` | 395 | 409 |
| `did_not_verified` | 1070 | 400 |
| `trunk_not_ready` | 1086 | 400 |
| `did_assignment_conflict` | 1143 | 409 |
| `invalid_did` | 1161 | 422 |
| `invalid_name` | 1174 | 422 |
| `incomplete_after_hours` | 1180 | 422 |
| `missing_recording_consent` | 1187 | 422 |
| `campaign_direction_conflict` | 1222 | 409 |
| `config_already_exists` | 1253 | 409 |
| `expected_version_required` | 1368 | 422 |
| `pause_before_edit` | 1405 | 409 |
| `campaign_archived` | 1410 | 409 |
| `assignment_quarantined` | 1421 | 409 |
| `assignment_state_conflict` | 1426 | 409 |
| `assignment_workflow_required` | 1445 | 409 |
| `campaign_change_forbidden` | 1452 | 409 |
| `version_conflict` | 1538 | 409 |
| `invalid_status` | 1614 | 400 |
| `pause_before_archive` | 1655 | 409 |
| `assignment_archived` | 1676 | 409 |
| `campaign_not_inbound` | 1693 | 409 |
| `campaign_terminal` | 1698 | 409 |
| `controls_missing` | 2117 | 400 |
| `inbound_runtime_restart_required` | 2185 | 409 |

Codes at `inbound_campaign_service.py:2439` and beyond belong to the
platform-admin reassignment and quarantine surfaces (`admin/inbound.py`) and
are **out of scope** for the tenant-facing specification.

> **The 1.0.0 four-kind taxonomy — `forbidden`, `unavailable`, `partial`,
> `not-found` — cannot express any of the 409 or 422 codes above.** Conflict
> is this backend's primary interaction model. This is the gap Phase C closes.

---

## 11. Permissions

`backend/app/core/security/rbac.py`:

| Permission | Line | Value |
|---|---|---|
| `INBOUND_READ` | 238 | `inbound:read` |
| `INBOUND_MANAGE` | 239 | `inbound:manage` |
| `INBOUND_ASSIGN` | 240 | `inbound:assign` |
| `INBOUND_CONTROLS` | 241 | `inbound:controls` |
| `PLATFORM_ADMIN` | 282 | `platform:admin` |

Role defaults — `ROLE_DEFAULT_PERMISSIONS`, `rbac.py:294`:

| Role | read | manage | assign | controls |
|---|---|---|---|---|
| `readonly` | ✅ 297 | — | — | — |
| `user` | ✅ 310 | — | — | — |
| `agent` | ✅ 337 | — | — | — |
| `billing_user` | — | — | — | — |
| `campaign_manager` | ✅ 387 | — | ✅ 388 | — |
| `tenant_admin` | ✅ 409 | ✅ 410 | ✅ 411 | ✅ 412 |
| `partner_admin` | ✅ 444 | ✅ 445 | ✅ 446 | ✅ 447 |
| `platform_admin` | ✅ 479 | ✅ 480 | ✅ 481 | ✅ 482 |

Effective-permission endpoint — `backend/app/api/v1/endpoints/rbac/users.py:32`,
`GET /rbac/users/me/permissions`, response `UserPermissionResponse`
(`rbac/schemas.py:32`): `user_id`, `tenant_id`, `permissions: List[str]`,
`role`, `grant_type`.

> **`readonly`, `user` and `agent` get `inbound:read` only.** A frontend that
> derives create/edit rights from a role string rather than from this
> endpoint will show actions the server rejects. `campaign_manager` is the
> sharpest case: it has `assign` but **not** `manage`, so it cannot create
> (create needs both — §2, R2).

---

## 12. Call history — `GET /calls/`

`backend/app/api/v1/endpoints/calls.py:1079`, `response_model=CallListResponse`.

### 12.1 Query parameters

| Name | Line | Type | Default | Constraint |
|---|---|---|---|---|
| `page` | 1081 | int | 1 | `>= 1` |
| `page_size` | 1082 | int | 20 | `1..100` |
| `status` | 1083 | str? | — | **no enum declared** |
| `direction` | 1084 | Literal? | — | `inbound` \| `outbound` |
| `inbound_campaign_id` | 1087 | UUID? | — | **implies `direction=inbound`** (1127) |
| `from` | 1091 | str? | — | `YYYY-MM-DD`, alias of `from_date` |
| `to` | 1092 | str? | — | `YYYY-MM-DD`, alias of `to_date` |

**Closes OQ-CH-01.** `direction` exists. The 1.0.0 instruction "There is no
`direction` parameter … Do not assume one exists" is false and must be
corrected.

Still absent, verified on the signature: **no `sort`, no `order`, no
`search`.** OQ-CH-05 and OQ-CH-10 remain open.

`inbound_campaign_id` resolves against the pinned route snapshot, not
`campaign_id` — `calls.py:1128-1130`:
`COALESCE(route_snapshot #>> '{inbound_config,id}', route_snapshot #>> '{route,config_id}')`.

### 12.2 `CallListItem` — `calls.py:177`

31 fields. The 1.0.0 spec documents 12. New since the freeze:

| Field | Line | Closes |
|---|---|---|
| `from_number` | 183 | **OQ-CH-02** |
| `direction` | 200 | OQ-CH-01 |
| `caller_ani` | 201 | OQ-CH-02 |
| `called_did` | 202 | — |
| `inbound_campaign_id` | 203 | **OQ-CH-08** |
| `assignment_id` | 204 | — |
| `route_id` | 205 | — |
| `route_version` | 206 | — |
| `config_version` | 207 | — |
| `config_checksum` | 208 | — |
| `admission_status` | 209 | — |
| `admission_reason` | 210 | — |
| `consent_status` | 211 | — |
| `processing_status` | 212 | — |
| `billing_status` | 213 | — |
| `billing_hold_reason` | 214 | — |
| `recording_status` | 215 | — |
| `transcript_status` | 216 | — |
| `media_state` | 217 | — |

`status` (185) and `outcome` (187) remain bare `str` — **OQ-CH-03 and
OQ-CH-04 stay open.**

### 12.3 `CallDetail` — `calls.py:220`

38 fields. Additional to `CallListItem`: `transcript`, `lead_id`,
`summary_json`, `provider`, `provider_call_id`, `ingress`, `route_snapshot`,
`reserved_seconds`, `answer_delay_seconds`,
`conversation_duration_seconds`, `billed_duration_seconds`, `cost`,
`transfer_legs`. `CallListResponse` is at `calls.py:268`.

### 12.4 Privacy projections

Applied server-side before serialisation:

| Helper | Line | Effect |
|---|---|---|
| `_display_caller_ani` | 43 | inbound ANI exposed only when the carrier did not mark it private (44) |
| `_route_metadata` | 53 | route id / version from the pinned snapshot |
| `_inbound_config_id` | 66 | inbound config identity from the route snapshot — **not** `campaign_id` |
| `_display_from_number` | 83 | direction-correct, durable source number (84) |
| `_media_state` | 91 | |
| `_transcript_state` | 119 | |
| `_recording_state` | 131 | suppressed to `"disabled"` when the pinned `inbound_config.recording_enabled` is false (139-140) or tenant `controls.recording_enabled` is false (141-142) |

### 12.5 Adjacent inbound call routes

| Route | Line | Response model |
|---|---|---|
| `GET /calls/live` | 650 | `LiveCallsResponse`, accepts `direction` (656) |
| `GET /calls/rejected` | 780 | `RejectedInboundCallsResponse` |
| `GET /calls/{call_id}/transcript` | 1438 | **none — OQ-CH-06 open** |
| `GET /calls/{call_id}/summary` | 1526 | **none — OQ-CH-07 open** |
| `GET /calls/{call_id}/events` | 1554 | — |
| `GET /calls/{call_id}/legs` | 1598 | — |

---

## 13. Verified DID inventory — `GET /tenant-phone-numbers/`

`backend/app/api/v1/endpoints/tenant_phone_numbers.py:111`,
`response_model=list[TenantPhoneNumber]` — **a bare JSON array**, not an
envelope. Router prefix `tenant_phone_numbers.py:46`.

`TenantPhoneNumber` — `backend/app/domain/models/tenant_phone_number.py:32`:

| Field | Line | Note |
|---|---|---|
| `id` | 36 | |
| `tenant_id` | 37 | |
| `e164` | 38 | **the number lives here, under this name** |
| `provider` | 39 | default `manual_admin` |
| `status` | 40 | `PhoneNumberStatus`, default `pending_verification` |
| `verification_method` | 41 | |
| `stir_shaken_token` | 45 | |
| `label` | 53 | |
| `metadata` | 54 | |

`PhoneNumberStatus` — `tenant_phone_number.py:16`: `pending_verification`,
`verified`, `suspended`, `revoked`.

> **There is no `masked_number`, no `display_number`, no `phone_number` and
> no `number` field.** A client whose masking helper does not read `e164`
> will render nothing. This is the root cause of `INB-MIS-003`, fixed in
> Phase D-1.

---

## 14. Environment configuration

`backend/.env.example`:

| Variable | Line | Effect |
|---|---|---|
| `INBOUND_TRANSFER_STAGING_PROOF_ENABLED` | 158 | honoured only when `ENVIRONMENT=staging`; production startup refuses it |
| `INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID` | 159 | must match one signed staging tenant |
| `INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID` | 160 | must match one pinned config |

All three must identify one signed staging fixture before the transfer
runtime can open (`.env.example:155-157`). This is what
`GET /inbound-campaigns/capabilities` reports and why the frontend must treat
transfer as unavailable by default.

---

## 15. Summary of open-question movement

| Closed by this extract | Evidence |
|---|---|
| OQ-CFG-01 | §2 R1 |
| OQ-CFG-02 | §2 R2, §3.1 |
| OQ-CFG-03 | §2 R7, §4.1 |
| OQ-CFG-04 | §7.1 |
| OQ-CFG-06 | §2 R9/R10, §3.2 |
| OQ-CFG-07 · 13 · 14 | §6 — no knowledge key exists |
| OQ-CFG-10 | §3.1 `did_number`, §13 |
| OQ-CFG-11 | §6 `system_prompt` |
| OQ-CFG-12 | §6 `voice_id` (accepted; no list endpoint) |
| OQ-CH-01 | §12.1 `direction` |
| OQ-CH-02 | §12.2 `from_number`, `caller_ani` |
| OQ-CH-08 | §12.2 `inbound_campaign_id` |

| Still open | Owner |
|---|---|
| OQ-CFG-05 — server-side wizard drafts | backend |
| OQ-CH-03 — call `status` enum | backend |
| OQ-CH-04 — call `outcome` enum | backend |
| OQ-CH-05 — server-side sort | backend |
| OQ-CH-06 — transcript response shape | backend |
| OQ-CH-07 — summary response shape | backend |

OQ-CFG-08, OQ-CFG-09, OQ-CFG-15 and OQ-CFG-16 are resolved by frontend
decision rather than by contract; see `OPEN-QUESTIONS.md`.
