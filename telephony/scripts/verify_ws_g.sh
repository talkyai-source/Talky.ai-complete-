#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
BACKEND_ROOT="$REPO_ROOT/backend"

echo "[1/6] Running WS-F baseline tests (prerequisite)..."
(
  cd "$BACKEND_ROOT"
  ./venv/bin/pytest -q tests/unit/test_telephony_sip_api.py
)

echo "[2/6] Running WS-G compiler and runtime API tests..."
(
  cd "$BACKEND_ROOT"
  ./venv/bin/pytest -q \
    tests/unit/test_telephony_runtime_policy_compiler.py \
    tests/unit/test_telephony_runtime_api.py
)

echo "[3/6] Validating WS-G endpoint markers..."
for marker in \
  '@router.post("/compile/preview"' \
  '@router.post("/activate"' \
  '@router.post("/rollback"' \
  '@router.get("/versions"'; do
  if ! grep -Fq "$marker" "$BACKEND_ROOT/app/api/v1/endpoints/telephony_runtime.py"; then
    echo "[ERROR] Missing WS-G endpoint marker: $marker"
    exit 1
  fi
done

echo "[4/6] Validating WS-G compiler and adapter markers..."
for marker in \
  "def compile_tenant_runtime_policy" \
  "class PolicyCompilationError" \
  "ds_reload" \
  "reloadxml"; do
  if ! rg -n "$marker" \
    "$BACKEND_ROOT/app/domain/services/telephony_runtime_policy.py" \
    "$BACKEND_ROOT/app/infrastructure/telephony/runtime_policy_adapter.py" >/dev/null; then
    echo "[ERROR] Missing WS-G compiler/adapter marker: $marker"
    exit 1
  fi
done

echo "[5/6] Validating WS-G schema migration markers..."
for marker in \
  "CREATE TABLE IF NOT EXISTS tenant_runtime_policy_versions" \
  "CREATE TABLE IF NOT EXISTS tenant_runtime_policy_events" \
  "idx_tenant_runtime_policy_versions_tenant_active_unique"; do
  if ! grep -Fq "$marker" "$BACKEND_ROOT/database/migrations/20260224_add_tenant_runtime_policy_versions.sql"; then
    echo "[ERROR] Missing WS-G schema marker: $marker"
    exit 1
  fi
done

echo "[6/6] WS-G verification complete"
echo
echo "WS-G verification PASSED."
