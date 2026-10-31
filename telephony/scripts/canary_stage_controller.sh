#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CANARY_SET_SCRIPT="$SCRIPT_DIR/canary_set_stage.sh"
CANARY_ROLLBACK_SCRIPT="$SCRIPT_DIR/canary_rollback.sh"
ASSERT_SCRIPT="$SCRIPT_DIR/assert_canary_ingress.sh"

DEFAULT_ENV_FILE="$TELEPHONY_ROOT/deploy/docker/.env.telephony"
DEFAULT_EVIDENCE_DIR="$TELEPHONY_ROOT/docs/phase_3/evidence"
DEFAULT_DECISION_FILE="$DEFAULT_EVIDENCE_DIR/ws_l_stage_decisions.jsonl"
PYTHON_BIN="${TELEPHONY_PYTHON_BIN:-python3}"

STAGE_SEQUENCE=(0 100)

usage() {
  cat <<'USAGE'
WS-L Canary Stage Controller

Usage:
  canary_stage_controller.sh status [env_file]
  canary_stage_controller.sh advance [env_file] [options]
  canary_stage_controller.sh set <0|100> [env_file] [options]
  canary_stage_controller.sh rollback [env_file] [options]

Options:
  --reason <text>           Reason for stage action (required for non-status)
  --operator <name>         Operator identifier (default: $USER)
  --force                   Override progression order only (never freeze)
  --dry-run                 Evaluate and record only; never change env/runtime
  --metrics-url <url>       Metrics endpoint (default: TELEPHONY_METRICS_URL or http://127.0.0.1:8000/metrics)
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
dry_run=0
metrics_url="${TELEPHONY_METRICS_URL:-http://127.0.0.1:8000/metrics}"
metrics_token="${TELEPHONY_METRICS_TOKEN:-}"
evidence_dir="$DEFAULT_EVIDENCE_DIR"
candidate_env=""
metrics_header_file=""
controller_decision_lock_dir=""
controller_decision_lock_owned=0
scope_candidate_digest=""
scope_run_id=""
scope_gate_started_at=""
scope_gate_started_at_epoch=""
expected_scope_hash=""

cleanup_controller_temp_files() {
  if [[ -n "${candidate_env:-}" ]]; then
    rm -f "$candidate_env"
  fi
  if [[ -n "${metrics_header_file:-}" ]]; then
    rm -f "$metrics_header_file"
  fi
  if [[ "${controller_decision_lock_owned:-0}" -eq 1 && -n "${controller_decision_lock_dir:-}" ]]; then
    rmdir "$controller_decision_lock_dir" 2>/dev/null || true
  fi
}
trap cleanup_controller_temp_files EXIT

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
    --dry-run)
      dry_run=1
      shift
      ;;
    --metrics-url)
      metrics_url="${2:-}"
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

cmp_eq() {
  local left="$1"
  local right="$2"
  awk -v l="$left" -v r="$right" 'BEGIN {exit ((l+0) == (r+0) ? 0 : 1)}'
}

metric_value_from_payload() {
  local payload="$1"
  local metric="$2"
  awk -v m="$metric" '$1==m {print $2; found=1; exit} END {if (!found) print ""}' <<<"$payload"
}

if [[ "$command" != "status" ]]; then
  if ! resolved_env_file="$(
    "$PYTHON_BIN" - "$env_file" <<'PY'
import sys
from pathlib import Path

print(Path(sys.argv[1]).resolve(strict=True))
PY
  )"; then
    echo "[ERROR] Unable to resolve canary env file for controller locking: $env_file"
    exit 1
  fi
  controller_decision_lock_dir="${resolved_env_file}.controller.lock.d"
  if ! mkdir "$controller_decision_lock_dir" 2>/dev/null; then
    echo "[ERROR] Another controller decision is already evaluating this canary env"
    exit 1
  fi
  controller_decision_lock_owned=1
fi

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
  current_freeze="1"
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

if [[ "$current_freeze" == "1" && "$target_stage" != "0" ]]; then
  echo "[ERROR] Canary is frozen. Explicitly unfreeze before activation; --force cannot bypass this safety gate."
  exit 1
fi

if [[ "$target_stage" == "100" ]]; then
  candidate_env="$(mktemp)"
  cp "$env_file" "$candidate_env"
  set_kv "OPENSIPS_CANARY_ENABLED" "1" "$candidate_env"
  set_kv "OPENSIPS_CANARY_PERCENT" "100" "$candidate_env"
  sh "$ASSERT_SCRIPT" env "$candidate_env"
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
  DECISION_GATES_JSON="$gates_json" \
  DECISION_METRICS_SNAPSHOT="$metrics_snapshot_file" \
  DECISION_SCOPE_HASH="$expected_scope_hash" \
  DECISION_CANDIDATE_DIGEST="$scope_candidate_digest" \
  DECISION_RUN_ID="$scope_run_id" \
  DECISION_GATE_STARTED_AT="$scope_gate_started_at" \
  "$PYTHON_BIN" - "$decision_file" <<'PY'
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
    "skip_gates": False,
    "metrics_snapshot_file": os.environ.get("DECISION_METRICS_SNAPSHOT") or None,
    "canary_scope_hash": os.environ.get("DECISION_SCOPE_HASH") or None,
    "candidate_digest": os.environ.get("DECISION_CANDIDATE_DIGEST") or None,
    "run_id": os.environ.get("DECISION_RUN_ID") or None,
    "gate_started_at": os.environ.get("DECISION_GATE_STARTED_AT") or None,
    "gates": json.loads(os.environ.get("DECISION_GATES_JSON", "{}")),
}
with open(sys.argv[1], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(entry, sort_keys=True) + "\n")
PY
}

if [[ "$target_stage" != "0" && "$target_stage" -gt "$current_stage" ]]; then
  setup_success_min="${TELEPHONY_CANARY_GATE_SETUP_SUCCESS_MIN:-0.985}"
  setup_min_attempts="${TELEPHONY_CANARY_GATE_SETUP_MIN_ATTEMPTS:-30}"
  answer_p95_max="${TELEPHONY_CANARY_GATE_ANSWER_P95_MAX_SECONDS:-1.5}"
  transfer_success_min="${TELEPHONY_CANARY_GATE_TRANSFER_SUCCESS_MIN:-0.95}"
  transfer_min_attempts="${TELEPHONY_CANARY_GATE_TRANSFER_MIN_ATTEMPTS:-10}"
  require_transfer_evidence="${TELEPHONY_CANARY_GATE_REQUIRE_TRANSFER_EVIDENCE:-0}"
  runtime_activation_success_min="${TELEPHONY_CANARY_GATE_RUNTIME_ACTIVATION_SUCCESS_MIN:-0.999}"
  runtime_activation_min_attempts="${TELEPHONY_CANARY_GATE_RUNTIME_ACTIVATION_MIN_ATTEMPTS:-3}"
  rollback_p95_max="${TELEPHONY_CANARY_GATE_ROLLBACK_P95_MAX_SECONDS:-60}"
  rollback_min_attempts="${TELEPHONY_CANARY_GATE_ROLLBACK_MIN_ATTEMPTS:-1}"
  scrape_max_age="${TELEPHONY_CANARY_GATE_SCRAPE_MAX_AGE_SECONDS:-60}"
  evidence_max_age="${TELEPHONY_CANARY_GATE_EVIDENCE_MAX_AGE_SECONDS:-900}"
  scope_tenant_id="${TELEPHONY_CANARY_TENANT_ID:-}"
  scope_config_id="${TELEPHONY_CANARY_CONFIG_ID:-}"
  scope_did="${TELEPHONY_CANARY_DID:-}"
  scope_candidate_digest="${TELEPHONY_CANARY_CANDIDATE_DIGEST:-}"
  scope_run_id="${TELEPHONY_CANARY_RUN_ID:-}"
  scope_gate_started_at="${TELEPHONY_CANARY_GATE_STARTED_AT:-}"
  ingress_did="$(read_kv "OPENSIPS_CANARY_DID" "$env_file")"

  if [[ "$require_transfer_evidence" != "0" && "$require_transfer_evidence" != "1" ]]; then
    gate_result="rejected"
    gates_json='{"error":"invalid_transfer_evidence_requirement"}'
    record_decision "$gate_result"
    echo "[ERROR] TELEPHONY_CANARY_GATE_REQUIRE_TRANSFER_EVIDENCE must be 0 or 1"
    exit 1
  fi
  if [[ ! "$scrape_max_age" =~ ^[1-9][0-9]*$ || ! "$evidence_max_age" =~ ^[1-9][0-9]*$ ]]; then
    gate_result="rejected"
    gates_json='{"error":"invalid_evidence_freshness_threshold"}'
    record_decision "$gate_result"
    echo "[ERROR] Canary scrape/evidence max ages must be positive integer seconds"
    exit 1
  fi

  if ! scope_identity="$(
    "$PYTHON_BIN" - \
      "$scope_tenant_id" "$scope_config_id" "$scope_did" "$ingress_did" \
      "$scope_candidate_digest" "$scope_run_id" "$scope_gate_started_at" <<'PY'
import hashlib
import re
import sys
from datetime import UTC, datetime
from uuid import UUID

try:
    tenant_id = str(UUID(sys.argv[1]))
    config_id = str(UUID(sys.argv[2]))
    parsed_run_id = UUID(sys.argv[6])
except (ValueError, AttributeError):
    raise SystemExit(1)
did = sys.argv[3].strip()
ingress_did = sys.argv[4].strip()
candidate_digest = sys.argv[5].strip()
gate_started_at_text = sys.argv[7].strip()
if parsed_run_id.version != 4:
    raise SystemExit(1)
run_id = str(parsed_run_id)
if not re.fullmatch(r"[1-9][0-9]{6,14}", did) or did != ingress_did:
    raise SystemExit(1)
if not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_digest):
    raise SystemExit(1)
if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", gate_started_at_text):
    raise SystemExit(1)
try:
    gate_started_at = datetime.strptime(
        gate_started_at_text, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=UTC)
except ValueError:
    raise SystemExit(1)
age_seconds = (datetime.now(UTC) - gate_started_at).total_seconds()
if age_seconds < -30 or age_seconds > 6 * 60 * 60:
    raise SystemExit(1)
canonical = ":".join(
    (tenant_id, config_id, did, candidate_digest, run_id, gate_started_at_text)
)
print(
    hashlib.sha256(canonical.encode()).hexdigest(),
    int(gate_started_at.timestamp()),
    run_id,
    sep="\t",
)
PY
  )"; then
    gate_result="rejected"
    gates_json='{"error":"invalid_canary_evidence_scope"}'
    record_decision "$gate_result"
    echo "[ERROR] Exact tenant/config/DID, sha256 candidate digest, UUIDv4 run ID, and fresh canonical UTC gate start are required; DID must match ingress"
    exit 1
  fi
  IFS=$'\t' read -r expected_scope_hash scope_gate_started_at_epoch canonical_run_id <<<"$scope_identity"
  scope_run_id="$canonical_run_id"

  curl_cmd=(curl -fsS --connect-timeout 3 --max-time 15)
  if [[ -n "$metrics_token" ]]; then
    case "$metrics_token" in
      *$'\r'*|*$'\n'*)
        gate_result="rejected"
        gates_json='{"error":"invalid_metrics_token"}'
        record_decision "$gate_result"
        echo "[ERROR] TELEPHONY_METRICS_TOKEN contains a forbidden line break"
        exit 1
        ;;
    esac
    metrics_header_file="$(mktemp)"
    chmod 600 "$metrics_header_file"
    printf 'X-Metrics-Token: %s\n' "$metrics_token" >"$metrics_header_file"
    curl_cmd+=("--header" "@${metrics_header_file}")
  fi
  curl_cmd+=("$metrics_url")

  metrics_payload="$("${curl_cmd[@]}")" || {
    gate_result="rejected"
    gates_json='{"error":"metrics_fetch_failed"}'
    record_decision "$gate_result"
    echo "[ERROR] Failed to fetch metrics from $metrics_url"
    exit 1
  }
  if [[ -n "$metrics_header_file" ]]; then
    rm -f "$metrics_header_file"
    metrics_header_file=""
  fi

  metrics_snapshot_file="$(
    mktemp "${evidence_dir%/}/ws_l_metrics_${scope_run_id}_$(date -u +%Y%m%dT%H%M%SZ).XXXXXX.prom"
  )"
  printf "%s\n" "$metrics_payload" > "$metrics_snapshot_file"

  m_scrape="$(metric_value_from_payload "$metrics_payload" "talky_telephony_metrics_scrape_success")"
  m_scrape_timestamp="$(metric_value_from_payload "$metrics_payload" "talky_telephony_metrics_scrape_timestamp_seconds")"
  m_scope_valid="$(metric_value_from_payload "$metrics_payload" "talky_telephony_canary_scope_valid")"
  m_baseline_timestamp="$(metric_value_from_payload "$metrics_payload" "talky_telephony_canary_evidence_baseline_timestamp_seconds")"
  m_scope_match="$(
    awk -v expected="$expected_scope_hash" '
      $1 == "talky_telephony_canary_scope_info{scope_hash=\"" expected "\"}" && ($2+0) == 1 {print 1; exit}
    ' <<<"$metrics_payload"
  )"
  m_setup_attempts="$(metric_value_from_payload "$metrics_payload" "talky_telephony_calls_setup_attempts")"
  m_unique_call_ids="$(metric_value_from_payload "$metrics_payload" "talky_telephony_canary_unique_call_ids")"
  m_latest_call_timestamp="$(metric_value_from_payload "$metrics_payload" "talky_telephony_canary_latest_call_timestamp_seconds")"
  m_setup_ratio="$(metric_value_from_payload "$metrics_payload" "talky_telephony_calls_setup_success_ratio")"
  m_answer_p95="$(metric_value_from_payload "$metrics_payload" "talky_telephony_calls_answer_latency_p95_seconds")"
  m_transfer_ratio="$(metric_value_from_payload "$metrics_payload" "talky_telephony_transfers_success_ratio")"
  m_transfer_attempts="$(metric_value_from_payload "$metrics_payload" "talky_telephony_transfers_attempts")"
  m_runtime_activation_ratio="$(metric_value_from_payload "$metrics_payload" "talky_telephony_runtime_activation_success_ratio")"
  m_runtime_activation_attempts="$(metric_value_from_payload "$metrics_payload" "talky_telephony_runtime_activation_attempts")"
  m_rollback_p95="$(metric_value_from_payload "$metrics_payload" "talky_telephony_runtime_rollback_latency_p95_seconds")"
  m_rollback_attempts="$(metric_value_from_payload "$metrics_payload" "talky_telephony_runtime_rollback_attempts")"

  for value_name in \
    m_scrape m_scrape_timestamp m_scope_valid m_baseline_timestamp m_setup_attempts m_unique_call_ids m_latest_call_timestamp m_setup_ratio m_answer_p95 m_transfer_ratio m_transfer_attempts \
    m_runtime_activation_ratio m_runtime_activation_attempts m_rollback_p95 m_rollback_attempts; do
    if [[ -z "${!value_name}" ]]; then
      gate_result="rejected"
      gates_json='{"error":"metrics_missing_required_signal"}'
      record_decision "$gate_result"
      echo "[ERROR] Missing required metric signal: $value_name"
      exit 1
    fi
  done

  if ! "$PYTHON_BIN" - \
    "$m_scrape" "$m_scrape_timestamp" "$m_scope_valid" "$m_baseline_timestamp" \
    "$m_setup_attempts" "$m_unique_call_ids" "$m_latest_call_timestamp" \
    "$m_setup_ratio" "$m_answer_p95" "$m_transfer_ratio" "$m_transfer_attempts" \
    "$m_runtime_activation_ratio" "$m_runtime_activation_attempts" \
    "$m_rollback_p95" "$m_rollback_attempts" "$scrape_max_age" "$evidence_max_age" <<'PY'
import math
import sys

try:
    values = [float(value) for value in sys.argv[1:]]
except ValueError:
    raise SystemExit(1)
if not all(math.isfinite(value) and value >= 0 for value in values):
    raise SystemExit(1)
PY
  then
    gate_result="rejected"
    gates_json='{"error":"metrics_invalid_numeric_signal"}'
    record_decision "$gate_result"
    echo "[ERROR] Metrics and freshness thresholds must be finite non-negative numbers"
    exit 1
  fi

  scrape_pass=0
  scope_pass=0
  setup_samples_pass=0
  setup_pass=0
  answer_pass=0
  transfer_pass=0
  runtime_activation_pass=0
  rollback_pass=0

  now_epoch="$(date +%s)"
  future_limit="$((now_epoch + 30))"
  scrape_oldest="$((now_epoch - scrape_max_age))"
  evidence_oldest="$((now_epoch - evidence_max_age))"

  if cmp_ge "$m_scrape" "1" && \
     cmp_ge "$m_scrape_timestamp" "$scope_gate_started_at_epoch" && \
     cmp_ge "$m_scrape_timestamp" "$scrape_oldest" && \
     cmp_le "$m_scrape_timestamp" "$future_limit"; then
    scrape_pass=1
  fi
  if cmp_ge "$m_scope_valid" "1" && [[ "$m_scope_match" == "1" ]] && \
     cmp_eq "$m_baseline_timestamp" "$scope_gate_started_at_epoch"; then
    scope_pass=1
  fi
  if cmp_eq "$m_setup_attempts" "$m_unique_call_ids" && \
     cmp_ge "$m_unique_call_ids" "$setup_min_attempts" && \
     cmp_ge "$m_latest_call_timestamp" "$scope_gate_started_at_epoch" && \
     cmp_ge "$m_latest_call_timestamp" "$evidence_oldest" && \
     cmp_le "$m_latest_call_timestamp" "$future_limit"; then
    setup_samples_pass=1
  fi
  cmp_ge "$m_setup_ratio" "$setup_success_min" && setup_pass=1 || true
  cmp_le "$m_answer_p95" "$answer_p95_max" && answer_pass=1 || true

  if [[ "$require_transfer_evidence" == "0" ]]; then
    transfer_pass=1
  elif cmp_ge "$m_transfer_attempts" "$transfer_min_attempts" && \
       cmp_ge "$m_transfer_ratio" "$transfer_success_min"; then
    transfer_pass=1
  fi

  if cmp_ge "$m_runtime_activation_attempts" "$runtime_activation_min_attempts" && \
     cmp_ge "$m_runtime_activation_ratio" "$runtime_activation_success_min"; then
    runtime_activation_pass=1
  fi

  if cmp_ge "$m_rollback_attempts" "$rollback_min_attempts" && \
     cmp_le "$m_rollback_p95" "$rollback_p95_max"; then
    rollback_pass=1
  fi

  gates_json="$(
    "$PYTHON_BIN" - \
      "$scrape_pass" "$scope_pass" "$setup_samples_pass" "$setup_pass" "$answer_pass" "$transfer_pass" "$runtime_activation_pass" "$rollback_pass" \
      "$m_scrape" "$m_scrape_timestamp" "$scrape_max_age" "$expected_scope_hash" "$m_baseline_timestamp" "$scope_gate_started_at_epoch" \
      "$m_setup_attempts" "$m_unique_call_ids" "$m_latest_call_timestamp" "$evidence_max_age" "$setup_min_attempts" "$m_setup_ratio" "$setup_success_min" "$m_answer_p95" "$answer_p95_max" \
      "$require_transfer_evidence" "$m_transfer_ratio" "$transfer_success_min" "$m_transfer_attempts" "$transfer_min_attempts" \
      "$m_runtime_activation_ratio" "$runtime_activation_success_min" "$m_runtime_activation_attempts" "$runtime_activation_min_attempts" \
      "$m_rollback_p95" "$rollback_p95_max" "$m_rollback_attempts" "$rollback_min_attempts" <<'PY'
import json
import sys

(
    scrape_pass,
    scope_pass,
    setup_samples_pass,
    setup_pass,
    answer_pass,
    transfer_pass,
    runtime_pass,
    rollback_pass,
    scrape_actual,
    scrape_timestamp,
    scrape_max_age,
    scope_hash,
    baseline_timestamp,
    expected_baseline_timestamp,
    setup_attempts,
    unique_call_ids,
    latest_call_timestamp,
    evidence_max_age,
    setup_min_attempts,
    setup_actual,
    setup_threshold,
    answer_actual,
    answer_threshold,
    transfer_required,
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
    rollback_min_attempts,
) = sys.argv[1:]

def _bool(v: str) -> bool:
    return str(v) == "1"

payload = {
    "scrape_success": {
        "pass": _bool(scrape_pass),
        "actual": float(scrape_actual),
        "threshold": 1.0,
        "timestamp_seconds": float(scrape_timestamp),
        "max_age_seconds": float(scrape_max_age),
    },
    "canary_evidence_scope": {
        "pass": _bool(scope_pass),
        "scope_hash": scope_hash,
        "baseline_timestamp_seconds": float(baseline_timestamp),
        "expected_baseline_timestamp_seconds": float(expected_baseline_timestamp),
    },
    "call_setup_samples": {
        "pass": _bool(setup_samples_pass),
        "attempts": float(setup_attempts),
        "unique_call_ids": float(unique_call_ids),
        "latest_call_timestamp_seconds": float(latest_call_timestamp),
        "max_age_seconds": float(evidence_max_age),
        "min_attempts": float(setup_min_attempts),
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
        "required": _bool(transfer_required),
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
        "min_attempts": float(rollback_min_attempts),
    },
}

print(json.dumps(payload, sort_keys=True))
PY
  )"

  if [[ "$scrape_pass" -ne 1 || "$scope_pass" -ne 1 || "$setup_samples_pass" -ne 1 || "$setup_pass" -ne 1 || "$answer_pass" -ne 1 || "$transfer_pass" -ne 1 || "$runtime_activation_pass" -ne 1 || "$rollback_pass" -ne 1 ]]; then
    gate_result="rejected"
    record_decision "$gate_result"
    echo "[ERROR] SLO gate check failed or has insufficient evidence; stage advance denied."
    exit 1
  fi
fi

if [[ "$dry_run" -eq 1 ]]; then
  record_decision "simulated"
  echo "[OK] WS-L stage action simulated: ${current_stage}% -> ${target_stage}% (command=${command}); env/runtime unchanged"
  if [[ -n "$metrics_snapshot_file" ]]; then
    echo "[OK] Metrics snapshot: $metrics_snapshot_file"
  fi
  echo "[OK] Decision log: $decision_file"
  exit 0
fi

if [[ "$command" == "rollback" ]]; then
  "$CANARY_ROLLBACK_SCRIPT" full "$env_file"
else
  set_cmd=("$CANARY_SET_SCRIPT" "$target_stage" "$env_file")
  if [[ "$force" -eq 1 ]]; then
    set_cmd+=("--force")
  fi
  "${set_cmd[@]}"
fi

record_decision "applied"
echo "[OK] WS-L stage action applied: ${current_stage}% -> ${target_stage}% (command=${command})"
if [[ -n "$metrics_snapshot_file" ]]; then
  echo "[OK] Metrics snapshot: $metrics_snapshot_file"
fi
echo "[OK] Decision log: $decision_file"
