#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
BACKEND_ROOT="$REPO_ROOT/backend"

echo "[1/6] Running WS-I verifier (prerequisite)..."
"$SCRIPT_DIR/verify_ws_i.sh"

echo "[2/6] Running WS-J unit tests..."
(
  cd "$BACKEND_ROOT"
  ./venv/bin/pytest -q \
    tests/unit/test_tenant_rls.py \
    tests/unit/test_telephony_runtime_api.py \
    tests/unit/test_telephony_sip_api.py
)

echo "[3/6] Validating WS-J runtime and context markers..."
for marker in \
  "set_config('app.current_request_id'" \
  '@router.get("/metrics/activation"' \
  "status=\"started\"" \
  "RuntimeActivationMetricsResponse"; do
  if ! rg -nF "$marker" \
    "$BACKEND_ROOT/app/core/tenant_rls.py" \
    "$BACKEND_ROOT/app/api/v1/endpoints/telephony_runtime.py" >/dev/null; then
    echo "[ERROR] Missing WS-J runtime/context marker: $marker"
    exit 1
  fi
done

echo "[4/6] Validating WS-J migration and schema markers..."
for marker in \
  "CREATE TABLE IF NOT EXISTS tenant_policy_audit_log" \
  "CREATE OR REPLACE FUNCTION log_tenant_policy_mutation()" \
  "CREATE OR REPLACE FUNCTION prevent_tenant_policy_audit_log_mutation()" \
  "CREATE OR REPLACE FUNCTION prune_tenant_policy_audit_log" \
  "CREATE POLICY p_tenant_policy_audit_log_select" \
  "CREATE TRIGGER trg_audit_tenant_sip_trunks"; do
  if ! rg -nF "$marker" \
    "$BACKEND_ROOT/database/migrations/20260224_add_tenant_policy_audit_ws_j.sql" \
    "$BACKEND_ROOT/database/complete_schema.sql" >/dev/null; then
    echo "[ERROR] Missing WS-J schema marker: $marker"
    exit 1
  fi
done

echo "[5/6] Validating WS-J trigger coverage markers..."
for marker in \
  "trg_audit_tenant_codec_policies" \
  "trg_audit_tenant_route_policies" \
  "trg_audit_tenant_sip_trust_policies" \
  "trg_audit_tenant_runtime_policy_versions" \
  "trg_audit_tenant_telephony_threshold_policies"; do
  if ! rg -nF "$marker" "$BACKEND_ROOT/database/migrations/20260224_add_tenant_policy_audit_ws_j.sql" >/dev/null; then
    echo "[ERROR] Missing WS-J trigger marker: $marker"
    exit 1
  fi
done

echo "[6/6] Validating WS-J documentation markers..."
for marker in \
  "WS-J Completion Record" \
  "WS-J Operations Runbook" \
  "Official Standards and Docs" \
  "WS-J Official Reference Addendum" \
  "RFC 9457" \
  "RFC 8725" \
  "activation failure" \
  "rollback latency"; do
  if ! rg -n "$marker" \
    "$REPO_ROOT/telephony/docs/phase_2/08_ws_j_completion.md" \
    "$REPO_ROOT/telephony/docs/phase_2/09_ws_j_operations_runbook.md" \
    "$REPO_ROOT/telephony/docs/phase_2/11_ws_j_official_reference_addendum.md" \
    "$REPO_ROOT/telephony/docs/phase_2/02_phase_two_gated_checklist.md" >/dev/null; then
    echo "[ERROR] Missing WS-J documentation marker: $marker"
    exit 1
  fi
done

echo
echo "WS-J verification PASSED."
