# WS-I Completion Record

Date: February 24, 2026  
Phase: 2 (Tenant Self-Service + Policy Automation)  
Workstream: WS-I (Quotas + Abuse Controls)  
Status: Complete

---

## Delivered Scope

1. Redis-atomic limiter implemented for tenant telephony mutation paths using:
   - `INCR`
   - `EXPIRE`
2. Graduated threshold actions implemented per tenant:
   - `warn`
   - `throttle`
   - `block`
3. Tenant policy model introduced for threshold configuration:
   - `tenant_telephony_threshold_policies`
4. Structured quota/abuse event stream persisted:
   - `tenant_telephony_quota_events`
5. Tenant-facing quota visibility endpoint implemented:
   - `GET /api/v1/telephony/sip/quotas/status`
6. SIP-edge abuse-control baseline strengthened with explicit shared-table support:
   - Kamailio `pike`
   - Kamailio `ratelimit`
   - Kamailio `htable`

---

## Backend/API Changes

1. New limiter service:
   - `backend/app/domain/services/telephony_rate_limiter.py`
2. SIP onboarding endpoints now enforce WS-I controls for mutating actions:
   - `backend/app/api/v1/endpoints/telephony_sip.py`
3. Runtime activation/rollback endpoints now enforce WS-I controls:
   - `backend/app/api/v1/endpoints/telephony_runtime.py`
4. New status endpoint:
   - `GET /api/v1/telephony/sip/quotas/status`

---

## Schema and Security Changes

1. Added migration:
   - `backend/database/migrations/20260224_add_tenant_quota_abuse_controls_ws_i.sql`
2. Added tables:
   - `tenant_telephony_threshold_policies`
   - `tenant_telephony_quota_events`
3. Added tenant RLS policies for both WS-I tables (select/insert/update/delete).
4. Updated canonical schema:
   - `backend/database/complete_schema.sql`

---

## SIP-Edge Controls

Kamailio config updated to include explicit WS-I shared abuse-state support:

1. `loadmodule "htable.so"`
2. `modparam("htable", "htable", "abuse=>size=12;autoexpire=300")`

File:
- `telephony/kamailio/conf/kamailio.cfg`

---

## Tests and Verification

### Unit tests

Executed:

```bash
cd backend
./venv/bin/pytest -q \
  tests/unit/test_telephony_rate_limiter.py \
  tests/unit/test_telephony_sip_api.py \
  tests/unit/test_telephony_runtime_api.py
```

Result:
- `23 passed`

### Workstream verifier

Executed:

```bash
bash telephony/scripts/verify_ws_i.sh
```

Result:
- `WS-I verification PASSED.`

Verifier includes:
1. WS-H prerequisite verification
2. WS-I unit-test suite
3. Endpoint marker checks
4. Limiter marker checks
5. Migration/schema marker checks
6. Kamailio abuse-control marker checks

---

## Official Reference Alignment

1. Redis `INCR`:
   - https://redis.io/docs/latest/commands/incr/
2. Redis `EXPIRE`:
   - https://redis.io/docs/latest/commands/expire/
3. Kamailio `pike`:
   - https://www.kamailio.org/docs/modules/stable/modules/pike.html
4. Kamailio `ratelimit`:
   - https://www.kamailio.org/docs/modules/stable/modules/ratelimit.html
5. Kamailio `htable`:
   - https://www.kamailio.org/docs/modules/stable/modules/htable.html
6. PostgreSQL row security:
   - https://www.postgresql.org/docs/current/ddl-rowsecurity.html

---

## Production Notes

1. Keys are tenant-scoped and metric-scoped to prevent cross-tenant bleed.
2. Limiter denials are idempotency-safe on SIP/runtime mutating endpoints.
3. Breach events publish to Redis channel `telephony:quota_alerts` for downstream alert integration.
4. Default per-tenant policies are seeded with wildcard metrics for deterministic baseline behavior.
