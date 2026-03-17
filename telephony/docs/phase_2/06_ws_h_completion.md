# WS-H Completion Record

Date: February 24, 2026  
Phase: 2 (Tenant Self-Service + Policy Automation)  
Workstream: WS-H (Isolation + Security Enforcement)  
Status: Complete

---

## Delivered Scope

1. Tenant-policy PostgreSQL RLS enabled and forced for WS-F/WS-G/WS-H tables.
2. Per-connection tenant context propagation added to telephony SIP/runtime endpoints (`set_config` for tenant/user).
3. Centralized JWT encode/decode hardening implemented with RFC 8725-aligned controls:
   - strict algorithm allow-list
   - explicit header algorithm validation
   - required registered claims (`sub`, `iat`, `exp`)
   - optional issuer/audience validation
   - leeway handling
4. SIP trust control model implemented and mapped into runtime artifacts:
   - `tenant_sip_trust_policies` schema
   - compiler output includes `kamailio.permissions.rules`

---

## Backend Changes

1. New security and isolation modules:
   - `backend/app/core/jwt_security.py`
   - `backend/app/core/tenant_rls.py`
2. Auth and middleware wiring updates:
   - `backend/app/api/v1/endpoints/auth.py`
   - `backend/app/api/v1/dependencies.py`
   - `backend/app/core/tenant_middleware.py`
   - `backend/app/core/config.py`
3. Runtime trust mapping updates:
   - `backend/app/api/v1/endpoints/telephony_runtime.py`
   - `backend/app/domain/services/telephony_runtime_policy.py`
4. Schema and migration:
   - `backend/database/migrations/20260224_add_tenant_policy_security_ws_h.sql`
   - `backend/database/complete_schema.sql`

---

## Tests Added / Updated

1. Added:
   - `backend/tests/unit/test_jwt_security.py`
   - `backend/tests/unit/test_tenant_rls.py`
2. Updated:
   - `backend/tests/unit/test_tenant_middleware.py`
   - `backend/tests/unit/test_telephony_sip_api.py`
   - `backend/tests/unit/test_telephony_runtime_api.py`
   - `backend/tests/unit/test_telephony_runtime_policy_compiler.py`

---

## Verification Evidence

### 1) Unit test suite

Executed:

```bash
cd backend
./venv/bin/pytest -q \
  tests/unit/test_telephony_sip_api.py \
  tests/unit/test_telephony_runtime_policy_compiler.py \
  tests/unit/test_telephony_runtime_api.py \
  tests/unit/test_jwt_security.py \
  tests/unit/test_tenant_rls.py \
  tests/unit/test_tenant_middleware.py
```

Result:
- `30 passed`

### 2) Workstream verifier

Executed:

```bash
bash telephony/scripts/verify_ws_h.sh
```

Result:
- `WS-H verification PASSED.`

### 3) Migration apply

Executed:

```bash
cd backend
export PGPASSWORD='talkyai_secret'
psql -h 127.0.0.1 -U talkyai -d talkyai -v ON_ERROR_STOP=1 \
  -f database/migrations/20260224_add_tenant_policy_security_ws_h.sql
```

Result:
- migration applied successfully (table/index/trigger/policy creation completed).

### 4) RLS validation

Executed:

```sql
SELECT tablename, rowsecurity, relforcerowsecurity
FROM ...
WHERE tablename IN (
  'tenant_sip_trunks',
  'tenant_codec_policies',
  'tenant_route_policies',
  'tenant_telephony_idempotency',
  'tenant_runtime_policy_versions',
  'tenant_runtime_policy_events',
  'tenant_sip_trust_policies'
);
```

Observed:
- all listed tables reported `rowsecurity=t` and `relforcerowsecurity=t`.
- each table has 4 policies (select/insert/update/delete).

---

## Production Notes

1. RLS is now an active database guardrail for tenant-policy data; application filters are no longer the only isolation layer.
2. Runtime artifact now includes explicit trust rules for Kamailio permissions integration.
3. JWT handling no longer duplicates logic across auth/dependencies/middleware; validation behavior is centralized and consistent.
4. `complete_schema.sql` trigger generation now targets only tables that actually include `updated_at`, avoiding invalid trigger attachment.
