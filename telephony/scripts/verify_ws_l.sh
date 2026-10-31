#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OPENSIPS_CFG="$TELEPHONY_ROOT/opensips/conf/opensips.cfg"
ROLLBACK_SCRIPT="$SCRIPT_DIR/canary_rollback.sh"
STAGE_CONTROLLER_SCRIPT="$SCRIPT_DIR/canary_stage_controller.sh"
FREEZE_SCRIPT="$SCRIPT_DIR/canary_freeze.sh"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony}"
PYTHON_BIN="${TELEPHONY_PYTHON_BIN:-python3}"

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

scope_tenant_id="${TELEPHONY_CANARY_TENANT_ID:-11111111-1111-4111-8111-111111111111}"
scope_config_id="${TELEPHONY_CANARY_CONFIG_ID:-22222222-2222-4222-8222-222222222222}"
scope_did="${TELEPHONY_CANARY_DID:-$(read_kv OPENSIPS_CANARY_DID "$ENV_FILE")}"
scope_candidate_digest="${TELEPHONY_CANARY_CANDIDATE_DIGEST:-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}"
scope_run_id="${TELEPHONY_CANARY_RUN_ID:-33333333-3333-4333-8333-333333333333}"
scope_gate_started_at="${TELEPHONY_CANARY_GATE_STARTED_AT:-$("$PYTHON_BIN" - <<'PY'
from datetime import UTC, datetime, timedelta
print((datetime.now(UTC) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
)}"
export TELEPHONY_CANARY_TENANT_ID="$scope_tenant_id"
export TELEPHONY_CANARY_CONFIG_ID="$scope_config_id"
export TELEPHONY_CANARY_DID="$scope_did"
export TELEPHONY_CANARY_CANDIDATE_DIGEST="$scope_candidate_digest"
export TELEPHONY_CANARY_RUN_ID="$scope_run_id"
export TELEPHONY_CANARY_GATE_STARTED_AT="$scope_gate_started_at"
read -r scope_hash scope_gate_started_at_epoch <<<"$(
  "$PYTHON_BIN" - \
    "$scope_tenant_id" "$scope_config_id" "$scope_did" \
    "$scope_candidate_digest" "$scope_run_id" "$scope_gate_started_at" <<'PY'
import hashlib
import sys
from datetime import UTC, datetime
from uuid import UUID

tenant_id = str(UUID(sys.argv[1]))
config_id = str(UUID(sys.argv[2]))
did = sys.argv[3]
candidate_digest = sys.argv[4]
run_id = str(UUID(sys.argv[5]))
started_at = datetime.strptime(sys.argv[6], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
canonical = ":".join(
    (tenant_id, config_id, did, candidate_digest, run_id, sys.argv[6])
)
print(hashlib.sha256(canonical.encode()).hexdigest(), int(started_at.timestamp()))
PY
)"
metrics_now_epoch="$(date +%s)"

cat >"$metrics_pass" <<'EOF'
talky_telephony_metrics_scrape_success 1
talky_telephony_calls_setup_attempts 100
talky_telephony_calls_setup_success_ratio 0.992
talky_telephony_calls_answer_latency_p95_seconds 1.2
talky_telephony_transfers_success_ratio 0.98
talky_telephony_transfers_attempts 30
talky_telephony_runtime_activation_success_ratio 1
talky_telephony_runtime_activation_attempts 20
talky_telephony_runtime_rollback_latency_p95_seconds 12
talky_telephony_runtime_rollback_attempts 2
EOF

cat >"$metrics_fail" <<'EOF'
talky_telephony_metrics_scrape_success 1
talky_telephony_calls_setup_attempts 100
talky_telephony_calls_setup_success_ratio 0.90
talky_telephony_calls_answer_latency_p95_seconds 2.1
talky_telephony_transfers_success_ratio 0.60
talky_telephony_transfers_attempts 30
talky_telephony_runtime_activation_success_ratio 0.90
talky_telephony_runtime_activation_attempts 20
talky_telephony_runtime_rollback_latency_p95_seconds 120
talky_telephony_runtime_rollback_attempts 3
EOF

for metrics_file in "$metrics_pass" "$metrics_fail"; do
  printf 'talky_telephony_metrics_scrape_timestamp_seconds %s\n' \
    "$metrics_now_epoch" >>"$metrics_file"
  printf 'talky_telephony_canary_scope_valid 1\n' >>"$metrics_file"
  printf 'talky_telephony_canary_scope_info{scope_hash="%s"} 1\n' \
    "$scope_hash" >>"$metrics_file"
  printf 'talky_telephony_canary_evidence_baseline_timestamp_seconds %s\n' \
    "$scope_gate_started_at_epoch" >>"$metrics_file"
  printf 'talky_telephony_canary_unique_call_ids 100\n' >>"$metrics_file"
  printf 'talky_telephony_canary_latest_call_timestamp_seconds %s\n' \
    "$metrics_now_epoch" >>"$metrics_file"
done

echo "[1/12] Running WS-K verifier (prerequisite)..."
"$SCRIPT_DIR/verify_ws_k.sh" "$ENV_FILE"

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[2/12] Running WS-E verifier (live canary prerequisite)..."
  "$SCRIPT_DIR/verify_ws_e.sh" "$ENV_FILE"
else
  echo "[2/12] Docker unavailable; skipping live WS-E prerequisite and live WS-L checks"
fi

echo "[3/12] Validating strict dedicated-DID canary configuration..."
sh "$SCRIPT_DIR/assert_canary_ingress.sh" all "$ENV_FILE"

if ! grep -Fq "opensips-cli -x mi ds_set_state i 2" "$ROLLBACK_SCRIPT"; then
  echo "[ERROR] Missing dispatcher runtime state transition command in rollback script"
  exit 1
fi

echo "[4/12] Proving dry-run baseline evaluation is non-mutating..."
dry_run_before="$RUN_DIR/dry_run_before.env"
cp "$ENV_FILE" "$dry_run_before"
"$STAGE_CONTROLLER_SCRIPT" set 0 "$ENV_FILE" \
  --reason "ws-l verifier reset baseline" \
  --dry-run \
  --evidence-dir "$RUN_DIR"
cmp -s "$dry_run_before" "$ENV_FILE" || {
  echo "[ERROR] Dry-run changed the durable canary env"
  exit 1
}

echo "[5/12] Validating freeze guard..."
if "$STAGE_CONTROLLER_SCRIPT" set 100 "$ENV_FILE" \
  --reason "ws-l verifier freeze check" \
  --dry-run \
  --evidence-dir "$RUN_DIR"; then
  echo "[ERROR] Frozen canary unexpectedly accepted activation"
  exit 1
fi

echo "[6/12] Validating gate rejection behavior..."
set_kv "OPENSIPS_CANARY_FREEZE" "0" "$ENV_FILE"
if "$STAGE_CONTROLLER_SCRIPT" set 100 "$ENV_FILE" \
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

echo "[7/12] Activating the dedicated-DID canary with passing gates (0->100)..."
"$STAGE_CONTROLLER_SCRIPT" set 100 "$ENV_FILE" \
  --reason "ws-l verifier dry-run dedicated-DID activation" \
  --dry-run \
  --metrics-url "file://$metrics_pass" \
  --evidence-dir "$RUN_DIR"

set_kv "OPENSIPS_CANARY_FREEZE" "1" "$ENV_FILE"
if "$STAGE_CONTROLLER_SCRIPT" set 100 "$ENV_FILE" \
  --reason "ws-l verifier freeze guard check" \
  --dry-run \
  --metrics-url "file://$metrics_pass" \
  --evidence-dir "$RUN_DIR"; then
  echo "[ERROR] Stage advance unexpectedly succeeded while frozen"
  exit 1
fi

if [[ "$(read_kv OPENSIPS_CANARY_PERCENT "$ENV_FILE")" != "0" ]]; then
  echo "[ERROR] Dry-run activation changed canary stage"
  exit 1
fi

echo "[8/12] Validating dry-run rollback is non-mutating..."
set_kv "OPENSIPS_CANARY_ENABLED" "1" "$ENV_FILE"
set_kv "OPENSIPS_CANARY_PERCENT" "100" "$ENV_FILE"
set_kv "OPENSIPS_CANARY_FREEZE" "0" "$ENV_FILE"
rollback_before="$RUN_DIR/rollback_before.env"
cp "$ENV_FILE" "$rollback_before"
"$STAGE_CONTROLLER_SCRIPT" rollback "$ENV_FILE" \
  --reason "ws-l verifier dry-run rollback" \
  --dry-run \
  --evidence-dir "$RUN_DIR"

if ! cmp -s "$rollback_before" "$ENV_FILE"; then
  echo "[ERROR] Dry-run rollback changed the durable canary env"
  exit 1
fi

echo "[9/12] Validating decision records..."
if [[ ! -f "$decision_file" ]]; then
  echo "[ERROR] Decision log not created: $decision_file"
  exit 1
fi

"$PYTHON_BIN" - "$decision_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
if len(rows) < 6:
    raise SystemExit("Decision log has insufficient entries")
results = {row.get("result") for row in rows}
if "simulated" not in results or "rejected" not in results:
    raise SystemExit("Decision log must contain both simulated and rejected entries")
print("Decision log validation passed")
PY

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[10/12] Running live WS-L controller smoke (0 -> 100 -> rollback)..."
  "$STAGE_CONTROLLER_SCRIPT" set 0 "$ENV_FILE" \
    --reason "ws-l verifier live baseline reset" \
    --evidence-dir "$RUN_DIR"
  set_kv "OPENSIPS_CANARY_FREEZE" "0" "$ENV_FILE"
  "$STAGE_CONTROLLER_SCRIPT" set 100 "$ENV_FILE" \
    --reason "ws-l verifier live dedicated-DID activation" \
    --metrics-url "file://$metrics_pass" \
    --evidence-dir "$RUN_DIR"

  start_epoch="$(date +%s)"
  "$STAGE_CONTROLLER_SCRIPT" rollback "$ENV_FILE" \
    --reason "ws-l verifier live rollback" \
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
