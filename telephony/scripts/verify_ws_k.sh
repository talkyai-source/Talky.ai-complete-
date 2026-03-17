#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
BACKEND_ROOT="$REPO_ROOT/backend"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony}"

PROM_CONFIG="$REPO_ROOT/telephony/observability/prometheus/prometheus.yml"
PROM_RULES="$REPO_ROOT/telephony/observability/prometheus/rules/telephony_ws_k_rules.yml"
ALERTMANAGER_CONFIG="$REPO_ROOT/telephony/observability/alertmanager/alertmanager.yml"
WSK_DOC="$REPO_ROOT/telephony/docs/phase_3/03_ws_k_completion.md"
CHECKLIST_DOC="$REPO_ROOT/telephony/docs/phase_3/02_phase_three_gated_checklist.md"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

echo "[1/8] Running WS-J verifier (prerequisite)..."
"$SCRIPT_DIR/verify_ws_j.sh"

echo "[2/8] Running WS-K backend unit tests..."
(
  cd "$BACKEND_ROOT"
  ./venv/bin/pytest -q tests/unit/test_telephony_observability.py
)

echo "[3/8] Validating WS-K backend markers..."
for marker in \
  '@app.get("/metrics")' \
  "talky_telephony_calls_setup_success_ratio" \
  "talky_telephony_runtime_activation_success_ratio" \
  "is_metrics_request_authorized" \
  "X-Metrics-Token"; do
  if ! rg -nF "$marker" \
    "$BACKEND_ROOT/app/main.py" \
    "$BACKEND_ROOT/app/core/telephony_observability.py" >/dev/null; then
    echo "[ERROR] Missing WS-K backend marker: $marker"
    exit 1
  fi
done

echo "[4/8] Validating WS-K observability files..."
for path in "$PROM_CONFIG" "$PROM_RULES" "$ALERTMANAGER_CONFIG"; do
  if [[ ! -f "$path" ]]; then
    echo "[ERROR] Missing observability file: $path"
    exit 1
  fi
done

echo "[5/8] Validating YAML syntax..."
(
  cd "$BACKEND_ROOT"
  ./venv/bin/python - <<'PY'
from pathlib import Path
import yaml

paths = [
    Path("../telephony/observability/prometheus/prometheus.yml"),
    Path("../telephony/observability/prometheus/rules/telephony_ws_k_rules.yml"),
    Path("../telephony/observability/alertmanager/alertmanager.yml"),
]
for path in paths:
    with path.open("r", encoding="utf-8") as f:
        yaml.safe_load(f)
print("YAML validation passed")
PY
)

echo "[6/8] Validating WS-K rule and alert markers..."
for marker in \
  "TalkyTelephonyCallSetupSLOViolation" \
  "TalkyTelephonyTransferSuccessLow" \
  "job:talky_telephony_calls_setup_success_ratio:avg5m" \
  "job:talky_telephony_runtime_activation_success_ratio:avg5m"; do
  if ! rg -nF "$marker" "$PROM_RULES" >/dev/null; then
    echo "[ERROR] Missing WS-K rule marker: $marker"
    exit 1
  fi
done

echo "[7/8] Validating WS-K docs markers..."
for marker in \
  "WS-K Completion Record" \
  "SLO Contract" \
  "Prometheus-compatible" \
  "X-Metrics-Token"; do
  if ! rg -n "$marker" "$WSK_DOC" "$CHECKLIST_DOC" >/dev/null; then
    echo "[ERROR] Missing WS-K documentation marker: $marker"
    exit 1
  fi
done

echo "[8/8] Optional promtool validation (if Docker available)..."
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker run --rm \
    --entrypoint /bin/promtool \
    -v "$PROM_CONFIG:/etc/prometheus/prometheus.yml:ro" \
    -v "$PROM_RULES:/etc/prometheus/rules/telephony_ws_k_rules.yml:ro" \
    prom/prometheus:v2.54.1 \
    check config /etc/prometheus/prometheus.yml >/dev/null
  docker run --rm \
    --entrypoint /bin/promtool \
    -v "$PROM_RULES:/etc/prometheus/rules/telephony_ws_k_rules.yml:ro" \
    prom/prometheus:v2.54.1 \
    check rules /etc/prometheus/rules/telephony_ws_k_rules.yml >/dev/null
  echo "[OK] promtool config and rules checks passed"
else
  echo "[WARN] Docker is unavailable/inaccessible; skipped promtool validation"
fi

echo
echo "WS-K verification PASSED."
