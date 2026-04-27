# Telephony production requirements

_Last updated: 2026-04-25 — shipped alongside the T0.1–T0.3 remediation block._

This is the operator's checklist for going live with a real PBX / SIP
trunk / cloud voice provider. Dev and staging do not need any of this;
production does.

If you cannot tick every box below, **do not point the system at a real
carrier.** The startup gate (`app.core.prod_gate.enforce_production_gate`)
blocks boot on the known-common misconfigs, but the rest of the checklist
is policy we cannot enforce in code alone.

---

## 1. SIP trunk / provider requirements

The `CallControlAdapter` interface is PBX-agnostic. Today we ship three
concrete adapters out of the box (`Asterisk` ARI, `FreeSWITCH` ESL,
`Vonage` NCCO). Twilio and Telnyx adapters are on the T1 roadmap.

Production **must** use one of:

- **Cloud voice provider** — Twilio Programmable Voice, Telnyx Call
  Control, Bandwidth, or Vonage Voice. These sign outbound calls with
  STIR/SHAKEN attestation on your behalf — you do not need to run your
  own certificate authority.
- **On-prem PBX (Asterisk or FreeSWITCH) + SIP trunk from a
  STIR/SHAKEN-enabled carrier** — Twilio Elastic SIP, Telnyx, Flowroute,
  SIPTRUNK.com etc. The PBX hands the INVITE to the trunk; the trunk
  attaches the attestation.

> **Pure un-attested SIP is not acceptable for US originating traffic.**
> The FCC requires STIR/SHAKEN for all voice service providers as of
> 2021 (full-participation deadline was 2023). Calls without an
> attestation are increasingly blocked at the terminating carrier and
> flagged as "Scam Likely" to callees.

---

## 2. Verified DID (caller_id) ownership — T0.1

Before a call can originate with a given `caller_id`, that number MUST
exist in `tenant_phone_numbers` with `status='verified'` AND (in prod)
a non-NULL `stir_shaken_token`. The enforcement path is
`telephony_bridge.make_call` → `TenantPhoneNumberService.is_verified_for_tenant`.

### Operator workflow

1. Register the number:
   ```
   POST /api/v1/tenant-phone-numbers
   {"e164": "+14155551234", "provider": "twilio", "label": "Main outbound"}
   ```
2. Prove ownership with the upstream provider (Twilio Verify, Telnyx
   number ownership check, SMS code, or a signed Letter of
   Authorization).
3. Capture the provider's STIR/SHAKEN attestation token — the
   provider-specific field names:
   - Twilio: `Caller ID Verification` → `stirShaken` response object
   - Telnyx: `ShakenToken` header on outbound INVITE
   - Vonage: `sip.stir_shaken.verstat` from verification request
4. Mark verified:
   ```
   POST /api/v1/tenant-phone-numbers/{id}/verify
   {"method": "carrier_api", "stir_shaken_token": "<attestation>"}
   ```

A number with `status='verified'` but **no** `stir_shaken_token` is
usable for test deploys only — the prod enforcement layer refuses it.

### Enforcement modes

Controlled by `CALLER_ID_ENFORCEMENT_MODE`:

| Mode       | Prod default | Behaviour                                       |
|------------|--------------|-------------------------------------------------|
| `enforce`  | ✅ required  | Unverified caller_id → HTTP 403                 |
| `log`      | dev default  | WARN log; call proceeds                         |
| `off`      | —            | Check disabled entirely (first-time bring-up)   |

Production boot refuses to start when this is set to anything other
than `enforce` or blank (default). This is checked by
`prod_gate._check_caller_id_enforcement`.

---

## 3. Startup gate — T0.2 + T0.3

`app.core.prod_gate.enforce_production_gate()` runs at the top of
`lifespan`. In `ENVIRONMENT=production` it refuses to boot when:

- `TELEPHONY_DEV_BYPASS_GUARD_ERRORS` or `TELEPHONY_LOCAL_DEV` is truthy
- Asterisk ARI password is blank or a known default (`asterisk`,
  `ari_password`, `secret`)
- FreeSWITCH ESL password is blank or `ClueCon` (the factory default —
  gives remote code execution)
- `JWT_SECRET` is missing or a placeholder (`change-me`, `secret`, …)
- `TELEPHONY_METRICS_TOKEN` is missing (`/metrics` would be unauthenticated)
- `STRIPE_SECRET_KEY` is missing AND `STRIPE_BILLING_DISABLED=1` is not
  set to explicitly acknowledge running without billing
- `CALLER_ID_ENFORCEMENT_MODE` is set to `log` or `off`

All violations are accumulated and reported together so one deploy
cycle can fix every issue, not whack-a-mole.

---

## 4. Environment checklist

Copy this list into your prod `.env`. Unset anything listed under
"must not be set in prod".

### Must be set

- `ENVIRONMENT=production`
- `TELEPHONY_ADAPTER` — one of `asterisk`, `freeswitch`, `vonage`, `twilio`, `telnyx`
- `ASTERISK_ARI_PASSWORD` — strong random, 32+ chars (if Asterisk)
- `FREESWITCH_ESL_PASSWORD` — strong random, 32+ chars (if FreeSWITCH)
- `JWT_SECRET` — 64+ random bytes
- `TELEPHONY_METRICS_TOKEN` — random bearer token for `/metrics`
- `STRIPE_SECRET_KEY` — or `STRIPE_BILLING_DISABLED=1` if self-hosted OSS
- `DATABASE_URL`, `REDIS_URL` — production instances
- All AI-provider keys currently needed (`GROQ_API_KEY`, `DEEPGRAM_API_KEY`,
  `CARTESIA_API_KEY` / `ELEVENLABS_API_KEY`, etc.)

### Must NOT be set in prod

- `TELEPHONY_DEV_BYPASS_GUARD_ERRORS`
- `TELEPHONY_LOCAL_DEV`
- `CALLER_ID_ENFORCEMENT_MODE=log` or `off`

### Recommended

- `MAX_TELEPHONY_SESSIONS` — per-process cap. Keep in mind this is
  per-process today (T1.2 introduces a Redis-backed global cap).
- `OTEL_ENABLED=true`, `OTEL_EXPORTER_ENDPOINT` — observability.

---

## 5. Open gaps (tracked in plan file)

The Tier 0 sprint addressed caller-ID ownership, fail-closed boot,
default credentials, and recording consent. Still pending before a
broader production rollout:

- **T1.1** — per-tenant AI-provider credentials (today everyone shares
  one Groq / Deepgram key)
- **T1.2** — cluster-wide concurrency cap (today `MAX_TELEPHONY_SESSIONS`
  is per-process)
- **T1.3** — STT/TTS reconnect + secondary-provider failover
- **T1.4** — Twilio / Telnyx adapters (only Asterisk / FreeSWITCH /
  Vonage today)
- **T1.5** — callee-timezone-aware business hours (TCPA compliance)
- **T2.1** — DNC list data source (guard enum exists; no feed wired)

Until T1.5 in particular is done, the `business_hours` check in
`CallGuard` compares against the **tenant's** timezone, not the
**callee's**. For US campaigns this is fine if the tenant timezone is
Eastern/Central and calls stay within CONUS. For any international
dialing, treat the check as informational only.

---

## 6. Test-only numbers

You can mark a number `verified` without a `stir_shaken_token` for
staging / test deploys. These numbers can dial:

- Other numbers verified inside the same tenant (echo tests, loopback)
- Upstream provider test numbers (Twilio magic numbers, Telnyx test DIDs)

They cannot dial real carrier traffic from prod because
`is_verified_for_tenant(..., require_attestation=True)` short-circuits
on the NULL token.
