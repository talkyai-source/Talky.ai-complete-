#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
BACKEND_ROOT="$REPO_ROOT/backend"

echo "[1/6] Running WS-G verifier (prerequisite)..."
"$SCRIPT_DIR/verify_ws_g.sh"

echo "[2/6] Running WS-H security and isolation unit tests..."
(
  cd "$BACKEND_ROOT"
  ./venv/bin/pytest -q \
    tests/unit/test_telephony_sip_api.py \
    tests/unit/test_telephony_runtime_policy_compiler.py \
    tests/unit/test_telephony_runtime_api.py \
    tests/unit/test_jwt_security.py \
    tests/unit/test_tenant_rls.py \
    tests/unit/test_tenant_middleware.py
)

echo "[3/6] Validating JWT hardening markers..."
for marker in \
  "ALLOWED_HMAC_ALGORITHMS" \
  "REQUIRED_CLAIMS" \
  "decode_and_validate_token" \
  "encode_access_token"; do
  if ! rg -n "$marker" "$BACKEND_ROOT/app/core/jwt_security.py" >/dev/null; then
    echo "[ERROR] Missing JWT hardening marker: $marker"
    exit 1
  fi
done

echo "[4/6] Validating endpoint RLS context markers..."
for marker in \
  "apply_tenant_rls_context\\(" \
  "tenant_sip_trust_policies"; do
  if ! rg -n "$marker" \
    "$BACKEND_ROOT/app/api/v1/endpoints/telephony_sip.py" \
    "$BACKEND_ROOT/app/api/v1/endpoints/telephony_runtime.py" >/dev/null; then
    echo "[ERROR] Missing WS-H endpoint marker: $marker"
    exit 1
  fi
done

echo "[5/6] Validating WS-H migration and schema markers..."
for marker in \
  "CREATE TABLE IF NOT EXISTS tenant_sip_trust_policies" \
  "ENABLE ROW LEVEL SECURITY" \
  "FORCE ROW LEVEL SECURITY" \
  "CREATE POLICY p_tenant_sip_trust_policies_select"; do
  if ! rg -n "$marker" \
    "$BACKEND_ROOT/database/migrations/20260224_add_tenant_policy_security_ws_h.sql" \
    "$BACKEND_ROOT/database/complete_schema.sql" >/dev/null; then
    echo "[ERROR] Missing WS-H schema marker: $marker"
    exit 1
  fi
done

echo "[6/6] WS-H verification complete"
echo
echo "WS-H verification PASSED."
