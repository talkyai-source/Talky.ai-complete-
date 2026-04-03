#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OPENSIPS_CFG="$TELEPHONY_ROOT/opensips/conf/opensips.cfg"
ROLLBACK_SCRIPT="$SCRIPT_DIR/canary_rollback.sh"
STAGE_CONTROLLER_SCRIPT="$SCRIPT_DIR/canary_stage_controller.sh"
FREEZE_SCRIPT="$SCRIPT_DIR/canary_freeze.sh"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

if [[ ! -f "$OPENSIPS_CFG" ]]; then
  echo "[ERROR] Missing OpenSIPS config: $OPENSIPS_CFG"
  exit 1
fi

for script in \
  "$STAGE_CONTROLLER_SCRIPT" \
  "$FREEZE_SCRIPT" \
  "$ROLLBACK_SCRIPT"; do
  if [[ ! -x "$script" ]]; then
    echo "[ERROR] Missing executable script: $script"
    exit 1
  fi
done

ENV_BACKUP="$(mktemp)"
RUN_DIR="$(mktemp -d)"
cp "$ENV_FILE" "$ENV_BACKUP"

cleanup() {
  cp "$ENV_BACKUP" "$ENV_FILE"
  rm -f "$ENV_BACKUP"
  rm -rf "$RUN_DIR"
}
trap cleanup EXIT

set_kv() {
  local key="$1"
  local value="$2"
  local file="$3"
  if grep -q "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    echo "${key}=${value}" >> "$file"
  fi
}

read_kv() {
  local key="$1"
  local file="$2"
  grep -E "^${key}=" "$file" | tail -n1 | cut -d= -f2- || true
}

metrics_pass="$RUN_DIR/metrics_pass.prom"
metrics_fail="$RUN_DIR/metrics_fail.prom"
decision_file="$RUN_DIR/ws_l_stage_decisions.jsonl"
rollback_timing_file="$RUN_DIR/ws_l_rollback_timing_seconds.txt"

cat >"$metrics_pass" <<'EOF'
talky_telephony_metrics_scrape_success 1
talky_telephony_calls_setup_success_ratio 0.992
talky_telephony_calls_answer_latency_p95_seconds 1.2
talky_telephony_transfers_success_ratio 0.98
talky_telephony_transfers_attempts 30
talky_telephony_runtime_activation_success_ratio 1
talky_telephony_runtime_activation_attempts 20
talky_telephony_runtime_rollback_latency_p95_seconds 12
talky_telephony_runtime_rollback_attempts 0
EOF

cat >"$metrics_fail" <<'EOF'
talky_telephony_metrics_scrape_success 1
talky_telephony_calls_setup_success_ratio 0.90
talky_telephony_calls_answer_latency_p95_seconds 2.1
talky_telephony_transfers_success_ratio 0.60
talky_telephony_transfers_attempts 30
talky_telephony_runtime_activation_success_ratio 0.90
talky_telephony_runtime_activation_attempts 20
talky_telephony_runtime_rollback_latency_p95_seconds 120
talky_telephony_runtime_rollback_attempts 3
EOF

echo "[1/12] Running WS-K verifier (prerequisite)..."
"$SCRIPT_DIR/verify_ws_k.sh" "$ENV_FILE"

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[2/12] Running WS-E verifier (live canary prerequisite)..."
  "$SCRIPT_DIR/verify_ws_e.sh" "$ENV_FILE"
else
  echo "[2/12] Docker unavailable; skipping live WS-E prerequisite and live WS-L checks"
fi

echo "[3/12] Validating WS-L OpenSIPS probing and canary markers..."
for marker in \
  'modparam("dispatcher", "ds_ping_interval", 10)' \
  'modparam("dispatcher", "ds_probing_threshold", 2)' \
  'modparam("dispatcher", "options_reply_codes", "501,403,404,483")' \
  'ds_select_dst("1", "4")' \
  'ds_select_dst("2", "4")' \
  'rand_set_prob("$def(OPENSIPS_CANARY_PERCENT)")'; do
  if ! grep -Fq "$marker" "$OPENSIPS_CFG"; then
    echo "[ERROR] Missing WS-L marker in opensips.cfg: $marker"
    exit 1
  fi
done

if ! grep -Fq "opensips-cli -x mi ds_set_state i 2" "$ROLLBACK_SCRIPT"; then
  echo "[ERROR] Missing dispatcher runtime state transition command in rollback script"
  exit 1
fi

echo "[4/12] Resetting dry-run canary state to baseline..."
"$STAGE_CONTROLLER_SCRIPT" set 0 "$ENV_FILE" \
  --reason "ws-l verifier reset baseline" \
  --dry-run \
  --skip-gates \
  --evidence-dir "$RUN_DIR"

echo "[5/12] Validating non-sequential progression guard..."
if "$STAGE_CONTROLLER_SCRIPT" set 50 "$ENV_FILE" \
  --reason "ws-l verifier non-sequential check" \
  --dry-run \
  --skip-gates \
  --evidence-dir "$RUN_DIR"; then
  echo "[ERROR] Non-sequential stage change unexpectedly succeeded"
  exit 1
fi

echo "[6/12] Validating gate rejection behavior..."
if "$STAGE_CONTROLLER_SCRIPT" set 5 "$ENV_FILE" \
  --reason "ws-l verifier should reject metrics" \
  --dry-run \
  --metrics-url "file://$metrics_fail" \
  --evidence-dir "$RUN_DIR"; then
  echo "[ERROR] Gate rejection test unexpectedly succeeded"
  exit 1
fi

if [[ "$(read_kv OPENSIPS_CANARY_PERCENT "$ENV_FILE")" != "0" ]]; then
  echo "[ERROR] Canary percent changed after rejected gate"
  exit 1
fi

echo "[7/12] Running staged dry-run progression with passing gates (0->5->25->50->100)..."
"$STAGE_CONTROLLER_SCRIPT" set 5 "$ENV_FILE" \
  --reason "ws-l verifier dry-run stage 5" \
  --dry-run \
  --metrics-url "file://$metrics_pass" \
  --evidence-dir "$RUN_DIR"
"$STAGE_CONTROLLER_SCRIPT" advance "$ENV_FILE" \
  --reason "ws-l verifier dry-run stage 25" \
  --dry-run \
  --metrics-url "file://$metrics_pass" \
  --evidence-dir "$RUN_DIR"

set_kv "OPENSIPS_CANARY_FREEZE" "1" "$ENV_FILE"
if "$STAGE_CONTROLLER_SCRIPT" advance "$ENV_FILE" \
  --reason "ws-l verifier freeze guard check" \
  --dry-run \
  --metrics-url "file://$metrics_pass" \
  --evidence-dir "$RUN_DIR"; then
  echo "[ERROR] Stage advance unexpectedly succeeded while frozen"
  exit 1
fi
set_kv "OPENSIPS_CANARY_FREEZE" "0" "$ENV_FILE"

"$STAGE_CONTROLLER_SCRIPT" advance "$ENV_FILE" \
  --reason "ws-l verifier dry-run stage 50" \
  --dry-run \
  --metrics-url "file://$metrics_pass" \
  --evidence-dir "$RUN_DIR"
"$STAGE_CONTROLLER_SCRIPT" advance "$ENV_FILE" \
  --reason "ws-l verifier dry-run stage 100" \
  --dry-run \
  --metrics-url "file://$metrics_pass" \
  --evidence-dir "$RUN_DIR"

if [[ "$(read_kv OPENSIPS_CANARY_PERCENT "$ENV_FILE")" != "100" ]]; then
  echo "[ERROR] Expected canary stage 100 after progression"
  exit 1
fi

echo "[8/12] Validating dry-run rollback path..."
"$STAGE_CONTROLLER_SCRIPT" rollback "$ENV_FILE" \
  --reason "ws-l verifier dry-run rollback" \
  --dry-run \
  --skip-gates \
  --evidence-dir "$RUN_DIR"

if [[ "$(read_kv OPENSIPS_CANARY_PERCENT "$ENV_FILE")" != "0" ]]; then
  echo "[ERROR] Expected canary stage 0 after rollback"
  exit 1
fi
if [[ "$(read_kv OPENSIPS_CANARY_ENABLED "$ENV_FILE")" != "0" ]]; then
  echo "[ERROR] Expected canary enabled=0 after rollback"
  exit 1
fi

echo "[9/12] Validating decision records..."
if [[ ! -f "$decision_file" ]]; then
  echo "[ERROR] Decision log not created: $decision_file"
  exit 1
fi

python3 - "$decision_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
if len(rows) < 6:
    raise SystemExit("Decision log has insufficient entries")
results = {row.get("result") for row in rows}
if "applied" not in results or "rejected" not in results:
    raise SystemExit("Decision log must contain both applied and rejected entries")
print("Decision log validation passed")
PY

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[10/12] Running live WS-L controller smoke (set 5 -> advance 25 -> rollback)..."
  "$STAGE_CONTROLLER_SCRIPT" set 5 "$ENV_FILE" \
    --reason "ws-l verifier live stage 5" \
    --skip-gates \
    --evidence-dir "$RUN_DIR"
  "$STAGE_CONTROLLER_SCRIPT" advance "$ENV_FILE" \
    --reason "ws-l verifier live stage 25" \
    --skip-gates \
    --evidence-dir "$RUN_DIR"

  start_epoch="$(date +%s)"
  "$STAGE_CONTROLLER_SCRIPT" rollback "$ENV_FILE" \
    --reason "ws-l verifier live rollback" \
    --skip-gates \
    --evidence-dir "$RUN_DIR"
  end_epoch="$(date +%s)"
  rollback_seconds="$((end_epoch - start_epoch))"
  printf "%s\n" "$rollback_seconds" >"$rollback_timing_file"

  rollback_max="${TELEPHONY_CANARY_ROLLBACK_MAX_SECONDS:-120}"
  if (( rollback_seconds > rollback_max )); then
    echo "[ERROR] Live rollback exceeded threshold: ${rollback_seconds}s > ${rollback_max}s"
    exit 1
  fi
else
  echo "[10/12] Live WS-L smoke skipped (docker unavailable)"
fi

echo "[11/12] Verifying WS-L docs and checklist markers..."
for marker in \
  "WS-L Completion Record" \
  "canary_stage_controller.sh" \
  "ws_l_stage_decisions.jsonl" \
  "WS-L Gate: SIP Edge Canary Orchestration" \
  "[x] Stage controller implemented"; do
  if ! rg -nF "$marker" \
    "$TELEPHONY_ROOT/docs/phase_3/02_phase_three_gated_checklist.md" \
    "$TELEPHONY_ROOT/docs/phase_3/04_ws_l_stage_controller_runbook.md" \
    "$TELEPHONY_ROOT/docs/phase_3/05_ws_l_completion.md" >/dev/null; then
    echo "[ERROR] Missing WS-L documentation marker: $marker"
    exit 1
  fi
done

echo "[12/12] WS-L verification complete"
echo "[OK] Temporary WS-L evidence generated at: $RUN_DIR"
echo
echo "WS-L verification PASSED."
