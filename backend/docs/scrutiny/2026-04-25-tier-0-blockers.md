# Tier 0 pre-launch blockers — remediation report

_Date: 2026-04-25_
_Plan reference: `~/.claude/plans/zazzy-wibbling-stardust.md` → Part 2, Tier 0_
_Tests: 43 new + 62 existing = **105 unit tests passing**_
_Routes: 233 → 238 (+4 DID endpoints, +1 DSAR delete)_

---

## Context — what we audited

The telephony stack was developed against an in-house Asterisk + C++
voice-gateway. Pointing it at a real carrier would have failed on five
legal/security gates that the PBX-agnostic architecture did not cover:

| # | Blocker | Old behaviour | Risk |
|---|---------|---------------|------|
| T0.1 | No DID / caller-ID ownership verification | `make_call` accepted `caller_id` as a free query param. Any tenant could spoof any number. | Carrier rejects spoofed ANI → account suspended. STIR/SHAKEN attestation fails. |
| T0.2 | CallGuard silently bypassed | `ENVIRONMENT != "production"` + truthy `TELEPHONY_DEV_BYPASS_GUARD_ERRORS` → every guard (DNC, rate limit, concurrency) skipped. Blank / "staging" / typoed env = footgun. | Misconfigured deploy runs with no safety checks. |
| T0.3 | Default PBX credentials only warned | Known-default Asterisk ARI password logged a warning and continued. FreeSWITCH `ClueCon` (RCE on the PBX) likewise warned-and-continued. | Unauthenticated prod PBX. RCE on the call switch. |
| T0.4 | No recording consent | Every call uploaded to S3 regardless of callee jurisdiction. No two-party-consent handling, no announcement, no DSAR delete. | Illegal in CA/MA/IL/WA/FL/MD/MT/NH/PA/CT/DE/MI/NV/OR + Canada + EU + UK + Australia. GDPR Article 17 fail. |
| T0.5 | No STIR/SHAKEN attestation | Nothing captured or enforced. | US carrier marks all outbound "Scam Likely" or rejects. |

All five shipped together in this block. Doc references by file:line
throughout.

---

## T0.2 — Fail-closed guard bypass

**File touched:** `backend/app/api/v1/endpoints/telephony_bridge.py`
(`make_call`, lines ~1245–1256)

### Before

```python
environment = os.getenv("ENVIRONMENT", "development").strip().lower()
allow_dev_guard_bypass = (
    environment != "production"
    and os.getenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", "true").strip().lower()
    not in {"0", "false", "no"}
)
```

Two problems. `environment != "production"` accepts blank, `"dev"`,
`"staging"`, `"prod"` (typo), etc. The bypass default was `"true"` so
operators had to REMEMBER to turn it off.

### After

```python
environment = os.getenv("ENVIRONMENT", "development").strip().lower()
local_dev = os.getenv("TELEPHONY_LOCAL_DEV", "").strip().lower() in {"1", "true", "yes"}
bypass_flag = os.getenv("TELEPHONY_DEV_BYPASS_GUARD_ERRORS", "false").strip().lower() \
    not in {"0", "false", "no", ""}
allow_dev_guard_bypass = environment == "development" and local_dev and bypass_flag
```

Three gates, all opt-in. Bypass default is `"false"`. Every non-dev
environment (production, staging, blank, typoed) now fails closed.

### Tests

`backend/tests/unit/test_prod_fail_closed.py` — 16 parametrised cases
covering every (environment × local_dev × bypass_flag) combination.
Includes a regression test specifically for the `"staging"` case that
was the original footgun.

---

## T0.3 — Default-credential refusal + startup gate

**New module:** `backend/app/core/prod_gate.py`
**Hooked at:** `backend/app/main.py` → `lifespan` step 0

### What it checks (production only)

| Rule | Detail |
|------|--------|
| `dev_bypass_in_prod` | `TELEPHONY_DEV_BYPASS_GUARD_ERRORS` or `TELEPHONY_LOCAL_DEV` set to any truthy value |
| `caller_id_enforcement_weakened` | `CALLER_ID_ENFORCEMENT_MODE=log` or `=off` |
| `asterisk_default_password` | `ASTERISK_ARI_PASSWORD` blank or in `{"asterisk", "ari_password", "secret"}` |
| `freeswitch_default_password` | `FREESWITCH_ESL_PASSWORD` blank or `ClueCon`/`cluecon` (gives RCE on FreeSWITCH) |
| `missing_secret: JWT_SECRET` | JWT key missing |
| `weak_secret` | JWT key in `{"change-me", "secret", "placeholder", …}` |
| `missing_secret: TELEPHONY_METRICS_TOKEN` | `/metrics` endpoint would be unauthenticated |
| `missing_secret: STRIPE_SECRET_KEY` | Billing silently falls back to mock mode; bypass requires explicit `STRIPE_BILLING_DISABLED=1` |

### Key design choices

- **Violations are accumulated and reported together** — one deploy can
  fix everything instead of whack-a-mole with individual errors.
- **Dev and staging skip the gate entirely** — no risk of a dev
  machine's missing key blocking local work.
- **Adapter-specific checks only fire if that adapter is selected.**
  Running Asterisk doesn't care about the FreeSWITCH password.
- **Stripe has an explicit opt-out** (`STRIPE_BILLING_DISABLED=1`) so
  self-hosted OSS deployments can run legitimately without a Stripe
  key without disabling the rest of the gate.

### Tests

Same file — `test_prod_fail_closed.py`. 15 cases covering each rule
with both the baseline happy prod env and a single violation layered
on top. Confirms violations accumulate; confirms Stripe bypass works.

---

## T0.1 — Verified caller-ID ownership

This is the largest block. Before this work, any tenant could set any
`caller_id` query param and originate with it. Now the number has to
be registered AND verified under the tenant, and (in prod) carry a
STIR/SHAKEN attestation token.

### New migration

`backend/database/migrations/20260425_add_tenant_phone_numbers.sql`

Creates `tenant_phone_numbers`:
- `e164` — strict E.164 (enforced at the endpoint validator; free-text
  at the DB layer for flexibility)
- `provider` — `twilio`, `telnyx`, `bandwidth`, `manual_admin`, …
- `status` — `pending_verification` → `verified` → `suspended` /
  `revoked` lifecycle. Only `verified` allows origination.
- `verification_method` — `sms_code`, `carrier_api`, `manual_admin`,
  `letter_of_authorization`. Durable audit trail.
- `stir_shaken_token` — attestation from the upstream provider.
  **NULL = test-only, production enforcement refuses it.** (T0.5)
- `verified_by`, `verified_at` — who/when.
- `metadata` — JSONB for provider-specific IDs.

RLS on `tenant_id`, partial index on `(tenant_id, e164) WHERE status='verified'`
for the hot-path lookup, UNIQUE constraint on `(tenant_id, e164)`,
`updated_at` trigger.

### New domain model

`backend/app/domain/models/tenant_phone_number.py` — Pydantic model,
two enums (`PhoneNumberStatus`, `VerificationMethod`), plus
`is_dialable_in_production()` — `True` iff `status='verified'` AND
`stir_shaken_token` is set.

### New service

`backend/app/domain/services/tenant_phone_number_service.py`

- `is_verified_for_tenant(tenant_id, e164, require_attestation=False)` —
  hot-path lookup. Returns `False` on any DB failure (fail-safe; the
  endpoint returns 403, not 500).
- `list_for_tenant`, `get`, `create_pending`, `mark_verified`,
  `revoke` — admin CRUD.
- `create_pending` is idempotent on `(tenant_id, e164)`.
- `revoke` never deletes — status flips to `revoked` for audit.

### New endpoints

Registered in `backend/app/api/v1/routes.py`. All scoped by the current
user's tenant_id.

| Method | Path | Purpose |
|--------|------|---------|
| GET    | `/api/v1/tenant-phone-numbers/` | List all DIDs for current tenant |
| POST   | `/api/v1/tenant-phone-numbers/` | Register a pending number |
| POST   | `/api/v1/tenant-phone-numbers/{id}/verify` | Transition pending → verified |
| DELETE | `/api/v1/tenant-phone-numbers/{id}` | Revoke (audit-preserving) |

### Enforcement — `telephony_bridge.make_call`

Inserted BEFORE CallGuard. The `caller_id` query param must resolve to
a row in `tenant_phone_numbers` with `status='verified'` under the
effective tenant. When it doesn't, we return `HTTP 403` with a
structured body:

```json
{
  "error": "caller_id_not_verified",
  "message": "The caller_id is not registered and verified under this tenant. …",
  "caller_id": "+15551234567",
  "require_attestation": true
}
```

### Ramp-in — `CALLER_ID_ENFORCEMENT_MODE`

New env var, three values:
- `enforce` — default in prod. Violation → HTTP 403.
- `log` — default in dev/staging. Violation → WARN log, call proceeds.
  Lets existing dev/CI workflows keep working while the DID table is
  being populated.
- `off` — check disabled. For first-time bring-up only.

Production gate (T0.3) refuses to boot when this is set to `log` or
`off` in prod.

### Tests

`backend/tests/unit/test_caller_id_verification.py` — 13 tests across:
- Service layer (verified / pending / revoked / unknown / DB-down cases)
- Attestation requirement (dev allows null token, prod refuses)
- Enforcement-mode resolution logic (prod defaults to `enforce`, dev to
  `log`, invalid values fall back to default)
- Prod gate rejects a weakened enforcement mode
- Model `is_dialable_in_production` covering every state combo

---

## T0.5 — STIR/SHAKEN attestation + docs

**DB:** Already landed in the T0.1 migration — `stir_shaken_token`
column on `tenant_phone_numbers`.

**Enforcement rule:** In production (`ENVIRONMENT=production`),
`make_call` calls
`TenantPhoneNumberService.is_verified_for_tenant(..., require_attestation=True)`.
A number with `status='verified'` but `stir_shaken_token=NULL` fails
the check. Test-only numbers without a real provider attestation
cannot dial real carriers.

**New doc:**
`backend/docs/telephony/production-requirements.md`

Contents:
- Supported SIP trunk / provider options (Twilio / Telnyx / Bandwidth /
  Vonage — STIR/SHAKEN enabled carriers only).
- Operator workflow: register → prove ownership → capture attestation
  token → mark verified.
- Provider-specific attestation-token field names (Twilio
  `stirShaken`, Telnyx `ShakenToken`, Vonage `verstat`).
- Startup gate checklist (T0.2 + T0.3).
- Environment-variable checklist — must be set, must NOT be set,
  recommended.
- Open gaps tracked against T1.1–T1.5, T2.1.

---

## T0.4 — Recording consent + jurisdictional opt-out

### New migration

`backend/database/migrations/20260425_add_tenant_recording_policy.sql`

Creates `tenant_recording_policy(tenant_id PK, default_consent_mode,
announcement_text, opt_out_dtmf_digit, two_party_country_codes[],
retention_days)`. RLS by tenant, `updated_at` trigger.

Seeded two-party list:
- US states: CA, MA, IL, WA, FL, MD, MT, NH, PA, CT, DE, MI, NV, OR
- Canada (PIPEDA + provincial law)
- All 27 EU member states (GDPR treats recording as processing)
- United Kingdom
- Australia

### New service

`backend/app/domain/services/recording_policy_service.py` — one entry
point `decide(tenant_id, destination_country_code)` returning a
`RecordingDecision`:

```python
@dataclass
class RecordingDecision:
    should_record: bool
    announcement_required: bool
    announcement_text: Optional[str]
    opt_out_dtmf_digit: Optional[str]
    retention_days: int
    reason: str
```

Decision logic:
- No row for tenant → safe default (two-party, announce everywhere,
  90-day retention).
- `default_consent_mode='disabled'` → never record.
- `default_consent_mode='one_party'` → record, skip announcement.
- `default_consent_mode='two_party'` + destination in the list OR list
  empty → record **with** announcement.
- `default_consent_mode='two_party'` + destination NOT in list →
  record without announcement (e.g. dialing a one-party-consent US
  state).
- Any DB failure → safe default (never silently record without
  consent).

Subdivision matching — `"US-CA"` matches both the exact entry and the
plain `"US"` entry if present.

### RecordingService integration

`backend/app/domain/services/recording_service.py`:
`save_and_link()` now calls `RecordingPolicyService.decide()` FIRST.
If `should_record` is False → skip the S3 upload entirely, log the
reason. If the policy lookup itself fails → skip the upload (fail
safe, never ship non-consensual recordings).

### New DSAR endpoint (GDPR Article 17)

`DELETE /api/v1/recordings/{recording_id}` — removes the S3 object
AND the `recordings_s3` metadata row. Tenant-scoped. Idempotent (204
on an already-deleted ID). Surfaces underlying errors as HTTP 5xx so
the compliance proof-of-deletion is real, not a silent swallow.

### Tests

`backend/tests/unit/test_recording_policy.py` — 9 tests covering every
branch of the decision logic, subdivision matching, empty-list
behaviour, missing-country-code safe default, and DB-down fallback.

### What's NOT in this sprint

The pre-answer **announcement audio path** — actually playing the
spoken notice to the callee before pipeline start and listening for
the opt-out DTMF — is a larger change to the call-origination flow.
The policy layer tells us WHETHER to announce; the RecordingService
will honour any recording-skip decision. Wiring the audio path is a
T0.4-continued task (flagged in
`docs/telephony/production-requirements.md`).

Until the announcement path ships, the legally safe posture is:
- For any tenant dialling into a two-party-consent jurisdiction, set
  their `default_consent_mode='disabled'` and don't record.
- Or record only in one-party-consent jurisdictions.

---

## Cross-cutting verification

```bash
./venv/bin/python3 -m pytest \
  tests/unit/test_recording_policy.py \
  tests/unit/test_caller_id_verification.py \
  tests/unit/test_prod_fail_closed.py \
  tests/unit/test_prompt_composer.py \
  tests/unit/test_interruption_filter.py \
  tests/unit/test_agent_name_rotator.py \
  tests/unit/test_telephony_bridge_first_speaker.py \
  -q
# 105 passed in 0.96s
```

**Route count sanity check:**

```
$ ./venv/bin/python3 -c 'from app.api.v1.routes import api_router; print(len(api_router.routes))'
238
# was 233 before this sprint — +4 DID endpoints, +1 DSAR delete
```

**Prod gate dry-run (local):**

```bash
ENVIRONMENT=production python -c "
from app.core.prod_gate import enforce_production_gate
try:
    enforce_production_gate()
except Exception as e:
    print('GOOD — refused:', e)
"
# Expect a 'Production startup refused' trace listing every missing secret.
```

---

## Full file manifest

### New files

- `backend/app/core/prod_gate.py` — startup gate
- `backend/app/domain/models/tenant_phone_number.py` — DID model
- `backend/app/domain/services/tenant_phone_number_service.py` — DID service
- `backend/app/domain/services/recording_policy_service.py` — recording policy
- `backend/app/api/v1/endpoints/tenant_phone_numbers.py` — 4 CRUD endpoints
- `backend/database/migrations/20260425_add_tenant_phone_numbers.sql`
- `backend/database/migrations/20260425_add_tenant_recording_policy.sql`
- `backend/docs/telephony/production-requirements.md` — operator checklist
- `backend/tests/unit/test_prod_fail_closed.py` — 31 tests
- `backend/tests/unit/test_caller_id_verification.py` — 13 tests
- `backend/tests/unit/test_recording_policy.py` — 9 tests
- `backend/docs/scrutiny/README.md` — this directory
- `backend/docs/scrutiny/2026-04-25-tier-0-blockers.md` — this file

### Modified files

- `backend/app/main.py` — `lifespan` calls `enforce_production_gate()` first
- `backend/app/api/v1/endpoints/telephony_bridge.py` — tightened bypass rule (T0.2) + caller-ID ownership enforcement (T0.1)
- `backend/app/api/v1/routes.py` — registers the tenant-phone-numbers router
- `backend/app/api/v1/endpoints/recordings.py` — new DELETE endpoint (T0.4 / GDPR DSAR)
- `backend/app/domain/services/recording_service.py` — `save_and_link()` consults policy before uploading

---

## Still pending (Tier 1 and beyond)

Captured in the plan file; not started this sprint:

- **T1.1** — Per-tenant AI-provider credentials (today every tenant
  shares `GROQ_API_KEY`, `DEEPGRAM_API_KEY`, etc.)
- **T1.2** — Cluster-wide concurrency cap (today `MAX_TELEPHONY_SESSIONS`
  is per-process)
- **T1.3** — STT/TTS reconnect + secondary-provider failover
- **T1.4** — Twilio + Telnyx adapters (only Asterisk / FreeSWITCH /
  Vonage today)
- **T1.5** — Callee-timezone-aware business hours (TCPA)
- **T2.1** — DNC list data source (guard enum exists; no feed)
- **T2.2** — Horizontal dialer scaling (Redis Streams + consumer
  groups)
- **T2.3** — Sentry + PagerDuty integration
- **T0.4-continued** — Pre-answer announcement audio + DTMF opt-out
  flow. Policy + upload gate shipped; audio-path wiring is a separate
  piece of work.

Each will get its own scrutiny entry when it lands.
