# WS-G Completion Record

Date: February 24, 2026  
Phase: 2 (Tenant Self-Service + Policy Automation)  
Workstream: WS-G (Runtime Policy Compiler)  
Status: Complete

---

## Delivered Scope

1. Deterministic runtime policy compiler implemented:
   - input: active tenant SIP trunk/route/codec policy records
   - output: versioned runtime artifact for Kamailio + FreeSWITCH
2. Dry-run validation implemented before activation:
   - invalid regex blocked
   - missing/inactive trunk reference blocked
   - missing/inactive codec reference blocked
   - route/trunk direction mismatch blocked
3. Activation workflow implemented with explicit stages:
   - `precheck -> apply -> verify -> commit`
4. Rollback workflow implemented:
   - rollback to explicit target version
   - fallback to prior eligible version when target omitted
5. Runtime policy version ledger + event log schema implemented:
   - versioned artifact table
   - per-stage event table for activation/rollback

---

## Backend Endpoints Added

Base prefix: `/api/v1/telephony/sip/runtime`

1. `POST /compile/preview`
   - compiles active policy set without activating
   - returns compiled artifact and source hash
2. `POST /activate`
   - requires `Idempotency-Key`
   - executes full staged activation sequence
3. `POST /rollback`
   - requires `Idempotency-Key`
   - reactivates target or prior version
4. `GET /versions`
   - returns version history and active state

---

## Files Added / Updated

1. Added:
   - `backend/app/domain/services/telephony_runtime_policy.py`
   - `backend/app/infrastructure/telephony/runtime_policy_adapter.py`
   - `backend/app/api/v1/endpoints/telephony_runtime.py`
   - `backend/database/migrations/20260224_add_tenant_runtime_policy_versions.sql`
   - `backend/tests/unit/test_telephony_runtime_policy_compiler.py`
   - `backend/tests/unit/test_telephony_runtime_api.py`
   - `telephony/scripts/verify_ws_g.sh`
2. Updated:
   - `backend/app/api/v1/routes.py`
   - `backend/database/complete_schema.sql`

---

## Test and Validation Evidence

Executed:

```bash
cd backend
./venv/bin/python -m py_compile \
  app/domain/services/telephony_runtime_policy.py \
  app/infrastructure/telephony/runtime_policy_adapter.py \
  app/api/v1/endpoints/telephony_runtime.py \
  tests/unit/test_telephony_runtime_policy_compiler.py \
  tests/unit/test_telephony_runtime_api.py

./venv/bin/pytest -q \
  tests/unit/test_telephony_sip_api.py \
  tests/unit/test_telephony_runtime_policy_compiler.py \
  tests/unit/test_telephony_runtime_api.py

bash telephony/scripts/verify_ws_g.sh

export PGPASSWORD='talkyai_secret'
psql -h 127.0.0.1 -U talkyai -d talkyai -v ON_ERROR_STOP=1 \
  -f database/migrations/20260224_add_tenant_runtime_policy_versions.sql
```

Results:
1. `20 passed` across WS-F + WS-G unit tests.
2. Py-compile checks passed for all WS-G modules.
3. WS-G migration applied successfully (`CREATE TABLE/INDEX/TRIGGER` complete).
4. WS-G verifier script passed (`WS-G verification PASSED`).

---

## Production Design Notes

1. Activation and rollback are idempotent via `tenant_telephony_idempotency`.
2. Compiler output is deterministic by sorted canonical snapshot and stable hash.
3. Invalid policy cannot be activated (hard fail at precheck/compile stage).
4. Runtime adapter defaults to simulation mode in development for safe local runs.
5. Command paths align with official runtime controls:
   - Kamailio dispatcher reload RPC: `dispatcher.reload`
   - FreeSWITCH XML refresh command: `reloadxml`
