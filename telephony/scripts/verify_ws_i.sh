#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
BACKEND_ROOT="$REPO_ROOT/backend"
OPENSIPS_CFG="$REPO_ROOT/telephony/opensips/conf/opensips.cfg"

echo "[1/6] Running WS-H verifier (prerequisite)..."
"$SCRIPT_DIR/verify_ws_h.sh"

echo "[2/6] Running WS-I unit tests..."
(
  cd "$BACKEND_ROOT"
  ./venv/bin/pytest -q \
    tests/unit/test_telephony_rate_limiter.py \
    tests/unit/test_telephony_sip_api.py \
    tests/unit/test_telephony_runtime_api.py
)

echo "[3/6] Validating WS-I endpoint markers..."
for marker in \
  '@router.get("/quotas/status"' \
  "_enforce_ws_i_quota(" \
  "telephony-rate-limited" \
  "runtime_mutation"; do
  if ! rg -nF "$marker" \
    "$BACKEND_ROOT/app/api/v1/endpoints/telephony_sip.py" \
    "$BACKEND_ROOT/app/api/v1/endpoints/telephony_runtime.py" >/dev/null; then
    echo "[ERROR] Missing WS-I endpoint marker: $marker"
    exit 1
  fi
done

echo "[4/6] Validating WS-I limiter implementation markers..."
for marker in \
  "class TelephonyRateLimiter" \
  "RateLimitAction" \
  "telephony:quota:count" \
  "tenant_telephony_quota_events" \
  "_ALERT_CHANNEL"; do
  if ! rg -nF "$marker" "$BACKEND_ROOT/app/domain/services/telephony_rate_limiter.py" >/dev/null; then
    echo "[ERROR] Missing WS-I limiter marker: $marker"
    exit 1
  fi
done

echo "[5/6] Validating WS-I schema/migration markers..."
for marker in \
  "CREATE TABLE IF NOT EXISTS tenant_telephony_threshold_policies" \
  "CREATE TABLE IF NOT EXISTS tenant_telephony_quota_events" \
  "CREATE POLICY p_tenant_telephony_threshold_policies_select" \
  "CREATE POLICY p_tenant_telephony_quota_events_select"; do
  if ! rg -nF "$marker" \
    "$BACKEND_ROOT/database/migrations/20260224_add_tenant_quota_abuse_controls_ws_i.sql" \
    "$BACKEND_ROOT/database/complete_schema.sql" >/dev/null; then
    echo "[ERROR] Missing WS-I schema marker: $marker"
    exit 1
  fi
done

echo "[6/6] Validating SIP-edge abuse control markers..."
for marker in \
  'loadmodule "pike.so"' \
  'loadmodule "ratelimit.so"' \
  'modparam("ratelimit", "timer_interval", 5)' \
  'pike_check_req()'; do
  if ! grep -Fq "$marker" "$OPENSIPS_CFG"; then
    echo "[ERROR] Missing SIP-edge abuse marker: $marker"
    exit 1
  fi
done

echo
echo "WS-I verification PASSED."
