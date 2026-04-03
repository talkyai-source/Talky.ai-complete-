#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CANARY_SET_SCRIPT="$SCRIPT_DIR/canary_set_stage.sh"
CANARY_ROLLBACK_SCRIPT="$SCRIPT_DIR/canary_rollback.sh"

DEFAULT_ENV_FILE="$TELEPHONY_ROOT/deploy/docker/.env.telephony"
DEFAULT_EVIDENCE_DIR="$TELEPHONY_ROOT/docs/phase_3/evidence"
DEFAULT_DECISION_FILE="$DEFAULT_EVIDENCE_DIR/ws_l_stage_decisions.jsonl"

STAGE_SEQUENCE=(0 5 25 50 100)

usage() {
  cat <<'USAGE'
WS-L Canary Stage Controller

Usage:
  canary_stage_controller.sh status [env_file]
  canary_stage_controller.sh advance [env_file] [options]
  canary_stage_controller.sh set <0|5|25|50|100> [env_file] [options]
  canary_stage_controller.sh rollback [env_file] [options]

Options:
  --reason <text>           Reason for stage action (required for non-status)
  --operator <name>         Operator identifier (default: $USER)
  --force                   Override freeze/progression guards
  --skip-gates              Skip metric gate checks for stage increase
  --dry-run                 Do not call docker/OpenSIPS; mutate env only
  --metrics-url <url>       Metrics endpoint (default: TELEPHONY_METRICS_URL or http://127.0.0.1:8000/metrics)
  --metrics-token <token>   Token for X-Metrics-Token header
  --evidence-dir <path>     Where decision logs/snapshots are stored
USAGE
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

command="$1"
shift

target_stage=""
if [[ "$command" == "set" ]]; then
  if [[ $# -lt 1 ]]; then
    echo "[ERROR] set requires target stage"
    usage
    exit 1
  fi
  target_stage="$1"
  shift
fi

env_file="$DEFAULT_ENV_FILE"
if [[ $# -gt 0 && ! "$1" =~ ^-- ]]; then
  env_file="$1"
  shift
fi

reason=""
operator="${USER:-unknown}"
force=0
skip_gates=0
dry_run=0
metrics_url="${TELEPHONY_METRICS_URL:-http://127.0.0.1:8000/metrics}"
metrics_token="${TELEPHONY_METRICS_TOKEN:-}"
evidence_dir="$DEFAULT_EVIDENCE_DIR"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reason)
      reason="${2:-}"
      shift 2
      ;;
    --operator)
      operator="${2:-}"
      shift 2
      ;;
    --force)
      force=1
      shift
      ;;
    --skip-gates)
      skip_gates=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --metrics-url)
      metrics_url="${2:-}"
      shift 2
      ;;
    --metrics-token)
      metrics_token="${2:-}"
      shift 2
      ;;
    --evidence-dir)
      evidence_dir="${2:-}"
      shift 2
      ;;
    *)
      echo "[ERROR] Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ ! -f "$env_file" ]]; then
  echo "[ERROR] Missing env file: $env_file"
  exit 1
fi

case "$command" in
  status|advance|set|rollback) ;;
  *)
    echo "[ERROR] Unsupported command: $command"
    usage
    exit 1
    ;;
esac

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
  local value
  value="$(grep -E "^${key}=" "$file" | tail -n1 | cut -d= -f2- || true)"
  echo "${value}"
}

is_valid_stage() {
  local stage="$1"
  local allowed
  for allowed in "${STAGE_SEQUENCE[@]}"; do
    if [[ "$allowed" == "$stage" ]]; then
      return 0
    fi
  done
  return 1
}

next_stage() {
  local current="$1"
  local i
  for i in "${!STAGE_SEQUENCE[@]}"; do
    if [[ "${STAGE_SEQUENCE[$i]}" == "$current" ]]; then
      if [[ "$i" -ge "$((${#STAGE_SEQUENCE[@]} - 1))" ]]; then
        echo ""
      else
        echo "${STAGE_SEQUENCE[$((i + 1))]}"
      fi
      return
    fi
  done
  echo ""
}

is_direct_progression() {
  local from="$1"
  local to="$2"
  local next
  next="$(next_stage "$from")"
  [[ -n "$next" && "$next" == "$to" ]]
}

cmp_ge() {
  local left="$1"
  local right="$2"
  awk -v l="$left" -v r="$right" 'BEGIN {exit ((l+0) >= (r+0) ? 0 : 1)}'
}

cmp_le() {
  local left="$1"
  local right="$2"
  awk -v l="$left" -v r="$right" 'BEGIN {exit ((l+0) <= (r+0) ? 0 : 1)}'
}

metric_value_from_payload() {
  local payload="$1"
  local metric="$2"
  awk -v m="$metric" '$1==m {print $2; found=1; exit} END {if (!found) print ""}' <<<"$payload"
}

current_stage="$(read_kv "OPENSIPS_CANARY_PERCENT" "$env_file")"
if [[ -z "$current_stage" ]]; then
  current_stage="0"
fi
if ! is_valid_stage "$current_stage"; then
  echo "[ERROR] Current canary stage in env is invalid: $current_stage"
  exit 1
fi

current_enabled="$(read_kv "OPENSIPS_CANARY_ENABLED" "$env_file")"
if [[ -z "$current_enabled" ]]; then
  current_enabled="0"
fi
current_freeze="$(read_kv "OPENSIPS_CANARY_FREEZE" "$env_file")"
if [[ -z "$current_freeze" ]]; then
  current_freeze="0"
fi

if [[ "$command" == "status" ]]; then
  echo "canary_enabled=${current_enabled}"
  echo "canary_percent=${current_stage}"
  echo "canary_freeze=${current_freeze}"
  next="$(next_stage "$current_stage")"
  if [[ -n "$next" ]]; then
    echo "next_stage=${next}"
  else
    echo "next_stage=none"
  fi
  exit 0
fi

if [[ -z "$reason" ]]; then
  echo "[ERROR] --reason is required for $command"
  exit 1
fi

case "$command" in
  advance)
    target_stage="$(next_stage "$current_stage")"
    if [[ -z "$target_stage" ]]; then
      echo "[ERROR] Canary already at terminal stage: $current_stage"
      exit 1
    fi
    ;;
  set)
    if ! is_valid_stage "$target_stage"; then
      echo "[ERROR] Invalid target stage: $target_stage"
      echo "Allowed: ${STAGE_SEQUENCE[*]}"
      exit 1
    fi
    ;;
  rollback)
    target_stage="0"
    ;;
esac

if [[ "$current_freeze" == "1" && "$target_stage" != "0" && "$force" -ne 1 ]]; then
  echo "[ERROR] Canary is frozen. Use --force to override."
  exit 1
fi

if [[ "$command" == "set" ]]; then
  if [[ "$target_stage" == "$current_stage" ]]; then
    echo "[INFO] Requested stage equals current stage ($current_stage); no-op."
  elif [[ "$target_stage" != "0" ]]; then
    if ! is_direct_progression "$current_stage" "$target_stage" && [[ "$force" -ne 1 ]]; then
      echo "[ERROR] Non-sequential stage change ${current_stage} -> ${target_stage} blocked. Use --force if intentional."
      exit 1
    fi
  fi
fi

mkdir -p "$evidence_dir"
decision_file="$evidence_dir/ws_l_stage_decisions.jsonl"
timestamp_utc="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
metrics_snapshot_file=""
gates_json="{}"
gate_result="passed"

record_decision() {
  local result="$1"
  DECISION_TS="$timestamp_utc" \
  DECISION_CMD="$command" \
  DECISION_OPERATOR="$operator" \
  DECISION_REASON="$reason" \
  DECISION_FROM="$current_stage" \
  DECISION_TO="$target_stage" \
  DECISION_RESULT="$result" \
  DECISION_DRY_RUN="$dry_run" \
  DECISION_FORCE="$force" \
  DECISION_SKIP_GATES="$skip_gates" \
  DECISION_GATES_JSON="$gates_json" \
  DECISION_METRICS_SNAPSHOT="$metrics_snapshot_file" \
  python3 - "$decision_file" <<'PY'
import json
import os
import sys

def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

entry = {
    "timestamp_utc": os.environ["DECISION_TS"],
    "command": os.environ["DECISION_CMD"],
    "operator": os.environ["DECISION_OPERATOR"],
    "reason": os.environ["DECISION_REASON"],
    "from_stage_percent": int(os.environ["DECISION_FROM"]),
    "to_stage_percent": int(os.environ["DECISION_TO"]),
    "result": os.environ["DECISION_RESULT"],
    "dry_run": _to_bool(os.environ["DECISION_DRY_RUN"]),
    "force": _to_bool(os.environ["DECISION_FORCE"]),
    "skip_gates": _to_bool(os.environ["DECISION_SKIP_GATES"]),
    "metrics_snapshot_file": os.environ.get("DECISION_METRICS_SNAPSHOT") or None,
    "gates": json.loads(os.environ.get("DECISION_GATES_JSON", "{}")),
}
with open(sys.argv[1], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(entry, sort_keys=True) + "\n")
PY
}

if [[ "$target_stage" != "0" && "$target_stage" -gt "$current_stage" && "$skip_gates" -ne 1 ]]; then
  setup_success_min="${TELEPHONY_CANARY_GATE_SETUP_SUCCESS_MIN:-0.985}"
  answer_p95_max="${TELEPHONY_CANARY_GATE_ANSWER_P95_MAX_SECONDS:-1.5}"
  transfer_success_min="${TELEPHONY_CANARY_GATE_TRANSFER_SUCCESS_MIN:-0.95}"
  transfer_min_attempts="${TELEPHONY_CANARY_GATE_TRANSFER_MIN_ATTEMPTS:-10}"
  runtime_activation_success_min="${TELEPHONY_CANARY_GATE_RUNTIME_ACTIVATION_SUCCESS_MIN:-0.999}"
  runtime_activation_min_attempts="${TELEPHONY_CANARY_GATE_RUNTIME_ACTIVATION_MIN_ATTEMPTS:-3}"
  rollback_p95_max="${TELEPHONY_CANARY_GATE_ROLLBACK_P95_MAX_SECONDS:-60}"

  curl_cmd=(curl -fsS "$metrics_url")
  if [[ -n "$metrics_token" ]]; then
    curl_cmd=(-H "X-Metrics-Token: ${metrics_token}" "${curl_cmd[@]}")
  fi

  metrics_payload="$("${curl_cmd[@]}")" || {
    gate_result="rejected"
    gates_json='{"error":"metrics_fetch_failed"}'
    record_decision "$gate_result"
    echo "[ERROR] Failed to fetch metrics from $metrics_url"
    exit 1
  }

  metrics_snapshot_file="$evidence_dir/ws_l_metrics_$(date -u +%Y%m%dT%H%M%SZ).prom"
  printf "%s\n" "$metrics_payload" > "$metrics_snapshot_file"

  m_scrape="$(metric_value_from_payload "$metrics_payload" "talky_telephony_metrics_scrape_success")"
  m_setup_ratio="$(metric_value_from_payload "$metrics_payload" "talky_telephony_calls_setup_success_ratio")"
  m_answer_p95="$(metric_value_from_payload "$metrics_payload" "talky_telephony_calls_answer_latency_p95_seconds")"
  m_transfer_ratio="$(metric_value_from_payload "$metrics_payload" "talky_telephony_transfers_success_ratio")"
  m_transfer_attempts="$(metric_value_from_payload "$metrics_payload" "talky_telephony_transfers_attempts")"
  m_runtime_activation_ratio="$(metric_value_from_payload "$metrics_payload" "talky_telephony_runtime_activation_success_ratio")"
  m_runtime_activation_attempts="$(metric_value_from_payload "$metrics_payload" "talky_telephony_runtime_activation_attempts")"
  m_rollback_p95="$(metric_value_from_payload "$metrics_payload" "talky_telephony_runtime_rollback_latency_p95_seconds")"
  m_rollback_attempts="$(metric_value_from_payload "$metrics_payload" "talky_telephony_runtime_rollback_attempts")"

  for value_name in \
    m_scrape m_setup_ratio m_answer_p95 m_transfer_ratio m_transfer_attempts \
    m_runtime_activation_ratio m_runtime_activation_attempts m_rollback_p95 m_rollback_attempts; do
    if [[ -z "${!value_name}" ]]; then
      gate_result="rejected"
      gates_json='{"error":"metrics_missing_required_signal"}'
      record_decision "$gate_result"
      echo "[ERROR] Missing required metric signal: $value_name"
      exit 1
    fi
  done

  scrape_pass=0
  setup_pass=0
  answer_pass=0
  transfer_pass=0
  runtime_activation_pass=0
  rollback_pass=0

  cmp_ge "$m_scrape" "1" && scrape_pass=1 || true
  cmp_ge "$m_setup_ratio" "$setup_success_min" && setup_pass=1 || true
  cmp_le "$m_answer_p95" "$answer_p95_max" && answer_pass=1 || true

  if cmp_ge "$m_transfer_attempts" "$transfer_min_attempts"; then
    cmp_ge "$m_transfer_ratio" "$transfer_success_min" && transfer_pass=1 || true
  else
    transfer_pass=1
  fi

  if cmp_ge "$m_runtime_activation_attempts" "$runtime_activation_min_attempts"; then
    cmp_ge "$m_runtime_activation_ratio" "$runtime_activation_success_min" && runtime_activation_pass=1 || true
  else
    runtime_activation_pass=1
  fi

  if cmp_ge "$m_rollback_attempts" "1"; then
    cmp_le "$m_rollback_p95" "$rollback_p95_max" && rollback_pass=1 || true
  else
    rollback_pass=1
  fi

  gates_json="$(
    python3 - \
      "$scrape_pass" "$setup_pass" "$answer_pass" "$transfer_pass" "$runtime_activation_pass" "$rollback_pass" \
      "$m_scrape" "$m_setup_ratio" "$setup_success_min" "$m_answer_p95" "$answer_p95_max" \
      "$m_transfer_ratio" "$transfer_success_min" "$m_transfer_attempts" "$transfer_min_attempts" \
      "$m_runtime_activation_ratio" "$runtime_activation_success_min" "$m_runtime_activation_attempts" "$runtime_activation_min_attempts" \
      "$m_rollback_p95" "$rollback_p95_max" "$m_rollback_attempts" <<'PY'
import json
import sys

(
    scrape_pass,
    setup_pass,
    answer_pass,
    transfer_pass,
    runtime_pass,
    rollback_pass,
    scrape_actual,
    setup_actual,
    setup_threshold,
    answer_actual,
    answer_threshold,
    transfer_actual,
    transfer_threshold,
    transfer_attempts,
    transfer_min_attempts,
    runtime_actual,
    runtime_threshold,
    runtime_attempts,
    runtime_min_attempts,
    rollback_actual,
    rollback_threshold,
    rollback_attempts,
) = sys.argv[1:]

def _bool(v: str) -> bool:
    return str(v) == "1"

payload = {
    "scrape_success": {
        "pass": _bool(scrape_pass),
        "actual": float(scrape_actual),
        "threshold": 1.0,
    },
    "call_setup_success_ratio": {
        "pass": _bool(setup_pass),
        "actual": float(setup_actual),
        "threshold": float(setup_threshold),
    },
    "answer_latency_p95_seconds": {
        "pass": _bool(answer_pass),
        "actual": float(answer_actual),
        "threshold_max": float(answer_threshold),
    },
    "transfer_success_ratio": {
        "pass": _bool(transfer_pass),
        "actual": float(transfer_actual),
        "threshold": float(transfer_threshold),
        "attempts": float(transfer_attempts),
        "min_attempts": float(transfer_min_attempts),
    },
    "runtime_activation_success_ratio": {
        "pass": _bool(runtime_pass),
        "actual": float(runtime_actual),
        "threshold": float(runtime_threshold),
        "attempts": float(runtime_attempts),
        "min_attempts": float(runtime_min_attempts),
    },
    "rollback_latency_p95_seconds": {
        "pass": _bool(rollback_pass),
        "actual": float(rollback_actual),
        "threshold_max": float(rollback_threshold),
        "attempts": float(rollback_attempts),
    },
}
print(json.dumps(payload, sort_keys=True))
PY
  )"

  if [[ "$scrape_pass" -ne 1 || "$setup_pass" -ne 1 || "$answer_pass" -ne 1 || "$transfer_pass" -ne 1 || "$runtime_activation_pass" -ne 1 || "$rollback_pass" -ne 1 ]]; then
    gate_result="rejected"
    record_decision "$gate_result"
    echo "[ERROR] SLO gate check failed; stage advance denied."
    exit 1
  fi
elif [[ "$skip_gates" -eq 1 ]]; then
  gates_json='{"skipped":true}'
fi

  if [[ "$dry_run" -eq 1 ]]; then
  if [[ "$command" == "rollback" ]]; then
    set_kv "OPENSIPS_CANARY_ENABLED" "0" "$env_file"
    set_kv "OPENSIPS_CANARY_PERCENT" "0" "$env_file"
    set_kv "OPENSIPS_CANARY_FREEZE" "0" "$env_file"
  else
    enabled="1"
    if [[ "$target_stage" == "0" ]]; then
      enabled="0"
    fi
    set_kv "OPENSIPS_CANARY_ENABLED" "$enabled" "$env_file"
    set_kv "OPENSIPS_CANARY_PERCENT" "$target_stage" "$env_file"
  fi
else
  if [[ "$command" == "rollback" ]]; then
    "$CANARY_ROLLBACK_SCRIPT" full "$env_file"
  else
    set_cmd=("$CANARY_SET_SCRIPT" "$target_stage" "$env_file")
    if [[ "$force" -eq 1 ]]; then
      set_cmd+=("--force")
    fi
    "${set_cmd[@]}"
  fi
fi

record_decision "applied"
echo "[OK] WS-L stage action applied: ${current_stage}% -> ${target_stage}% (command=${command})"
if [[ -n "$metrics_snapshot_file" ]]; then
  echo "[OK] Metrics snapshot: $metrics_snapshot_file"
fi
echo "[OK] Decision log: $decision_file"
