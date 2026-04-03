#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony.example}"
DOC_ROOT="$TELEPHONY_ROOT/docs/phase_3"
EVIDENCE_DIR="$DOC_ROOT/evidence/day10"
mkdir -p "$EVIDENCE_DIR"

VERIFIER_OUT="$EVIDENCE_DIR/day10_verifier_output.txt"
exec > >(tee "$VERIFIER_OUT") 2>&1

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
COMPOSE_FORCE_RECREATE="${DAY10_COMPOSE_FORCE_RECREATE:-0}"

SIP_PORT="${OPENSIPS_SIP_PORT:-15060}"
EXTENSION="${DAY5_TEST_EXTENSION:-750}"
ARI_HOST="${ASTERISK_ARI_HOST:-127.0.0.1}"
ARI_PORT="${ASTERISK_ARI_PORT:-8088}"
ARI_USERNAME="${ASTERISK_ARI_USERNAME:-day5}"
ARI_PASSWORD="${ASTERISK_ARI_PASSWORD:-day5_local_only_change_me}"
ARI_APP="${ASTERISK_ARI_APP:-talky_day5}"

GATEWAY_DIR="$REPO_ROOT/services/voice-gateway-cpp"
GATEWAY_BUILD_DIR="$GATEWAY_DIR/build"
GATEWAY_HOST="${DAY10_GATEWAY_HOST:-127.0.0.1}"
GATEWAY_HTTP_PORT="${DAY10_GATEWAY_PORT:-18080}"
GATEWAY_BASE_URL="http://${GATEWAY_HOST}:${GATEWAY_HTTP_PORT}"
GATEWAY_PID_FILE="$EVIDENCE_DIR/day10_gateway.pid"

PYTHON_BIN="${DAY10_PYTHON_BIN:-$REPO_ROOT/backend/venv/bin/python}"
STAGE_CONCURRENCY="${DAY10_STAGE_CONCURRENCY:-10,20,30,40,50}"
STAGE_DURATION_SECONDS="${DAY10_STAGE_DURATION_SECONDS:-300}"
SOAK_DURATION_SECONDS="${DAY10_SOAK_DURATION_SECONDS:-7200}"
SMOKE_DURATION_SECONDS="${DAY10_SMOKE_DURATION_SECONDS:-20}"
SAMPLE_INTERVAL_SECONDS="${DAY10_SAMPLE_INTERVAL_SECONDS:-5}"
MIN_DISPATCH_INTERVAL_SECONDS="${DAY10_MIN_DISPATCH_INTERVAL_SECONDS:-0.02}"
HOLD_SECONDS="${DAY10_HOLD_SECONDS:-3.0}"

PROFILE_BASELINE_PERCENT="${DAY10_PROFILE_BASELINE_PERCENT:-50}"
PROFILE_BARGEIN_PERCENT="${DAY10_PROFILE_BARGEIN_PERCENT:-30}"
PROFILE_TRANSFER_PERCENT="${DAY10_PROFILE_TRANSFER_PERCENT:-20}"

SETUP_SUCCESS_MIN="${DAY10_SETUP_SUCCESS_MIN:-0.99}"
SRD_P95_MAX_MS="${DAY10_SRD_P95_MAX_MS:-2000}"
SDD_P95_MAX_MS="${DAY10_SDD_P95_MAX_MS:-1500}"
ISA_MAX_PERCENT="${DAY10_ISA_MAX_PERCENT:-1.0}"
TRANSFER_SUCCESS_MIN="${DAY10_TRANSFER_SUCCESS_MIN:-0.95}"
BARGEIN_REACTION_P95_MAX_MS="${DAY10_BARGEIN_REACTION_P95_MAX_MS:-250}"
REQUIRE_TRANSFER="${DAY10_REQUIRE_TRANSFER:-0}"
REQUIRE_BARGEIN_REACTION="${DAY10_REQUIRE_BARGEIN_REACTION:-0}"
REQUIRE_TENANT_FAIRNESS="${DAY10_REQUIRE_TENANT_FAIRNESS:-0}"

TENANT_ID="${DAY10_TENANT_ID:-day10-default}"
TENANT_IDS="${DAY10_TENANT_IDS:-$TENANT_ID}"
TRANSFER_DELAY_SECONDS="${DAY10_TRANSFER_DELAY_SECONDS:-0.6}"
TRANSFER_ENDPOINT="${DAY10_TRANSFER_ENDPOINT:-Local/blind_target@wsm-synthetic}"
CONTROLLER_IDLE_TIMEOUT_SECONDS="${DAY10_CONTROLLER_IDLE_TIMEOUT_SECONDS:-240}"

STAGE_MAX_CONCURRENCY=10
IFS=',' read -r -a __day10_levels <<<"$STAGE_CONCURRENCY"
for __day10_level in "${__day10_levels[@]}"; do
  __day10_level="${__day10_level// /}"
  if [[ "$__day10_level" =~ ^[0-9]+$ ]] && (( __day10_level > STAGE_MAX_CONCURRENCY )); then
    STAGE_MAX_CONCURRENCY="$__day10_level"
  fi
done
unset __day10_levels __day10_level
TENANT_MAX_ACTIVE_CALLS="${DAY10_TENANT_MAX_ACTIVE_CALLS:-$STAGE_MAX_CONCURRENCY}"
TENANT_MAX_TRANSFER_INFLIGHT="${DAY10_TENANT_MAX_TRANSFER_INFLIGHT:-$(( (STAGE_MAX_CONCURRENCY / 2) + 1 ))}"

HARN_SMOKE_JSON="$EVIDENCE_DIR/day10_harness_smoke.json"
RAMP_RESULTS_JSON="$EVIDENCE_DIR/day10_ramp_stage_results.json"
CAPACITY_REPORT_JSON="$EVIDENCE_DIR/day10_capacity_threshold_report.json"
SOAK_SUMMARY_JSON="$EVIDENCE_DIR/day10_soak_summary.json"
SOAK_TIMESERIES_CSV="$EVIDENCE_DIR/day10_soak_timeseries.csv"
TRANSFER_REPORT_JSON="$EVIDENCE_DIR/day10_transfer_load_report.json"
BARGEIN_REPORT_JSON="$EVIDENCE_DIR/day10_bargein_load_report.json"
TENANT_FAIRNESS_JSON="$EVIDENCE_DIR/day10_tenant_fairness_report.json"
CALL_RESULTS_JSON="$EVIDENCE_DIR/day10_call_results.json"
RECOVERY_SMOKE_JSON="$EVIDENCE_DIR/day10_recovery_smoke_results.json"

RECOVERY_OPENSIPS_JSON="$EVIDENCE_DIR/day10_recovery_opensips.json"
RECOVERY_ASTERISK_JSON="$EVIDENCE_DIR/day10_recovery_asterisk.json"
RECOVERY_RTPENGINE_JSON="$EVIDENCE_DIR/day10_recovery_rtpengine.json"
RECOVERY_GATEWAY_JSON="$EVIDENCE_DIR/day10_recovery_gateway.json"
RECOVERY_TIMELINE_LOG="$EVIDENCE_DIR/day10_recovery_timeline.log"

SESSION_TIMER_REPORT_JSON="$EVIDENCE_DIR/day10_session_timer_report.json"
LEAK_AUDIT_REPORT_JSON="$EVIDENCE_DIR/day10_leak_audit_report.json"
GO_NO_GO_JSON="$EVIDENCE_DIR/day10_go_no_go.json"
GO_NO_GO_CHECKLIST_MD="$EVIDENCE_DIR/day10_go_no_go_checklist.md"
EVIDENCE_DOC="$DOC_ROOT/day10_concurrency_soak_evidence.md"

ARI_TRACE="$EVIDENCE_DIR/day10_ari_event_trace.log"
GATEWAY_LOG="$EVIDENCE_DIR/day10_gateway_runtime.log"
GATEWAY_STATS_JSON="$EVIDENCE_DIR/day10_gateway_stats.json"
ARI_BASELINE_JSON="$EVIDENCE_DIR/day10_ari_baseline_state.json"
ARI_POST_JSON="$EVIDENCE_DIR/day10_ari_post_state.json"
RTPENGINE_CFG="${DAY10_RTPENGINE_CFG:-$TELEPHONY_ROOT/rtpengine/conf/rtpengine.userspace.conf}"
ASTERISK_RTP_CFG="$TELEPHONY_ROOT/asterisk/conf/rtp.conf"
RTP_MIN_PORT_SPAN="${DAY10_RTP_MIN_PORT_SPAN:-1000}"

GW_PID=""
ARI_PID=""

read_kv_cfg() {
  local cfg_file="$1"
  local cfg_key="$2"
  awk -F'=' -v key="$cfg_key" '
    BEGIN { found="" }
    /^[[:space:]]*#/ { next }
    {
      gsub(/[[:space:]]/, "", $1)
      gsub(/[[:space:]]/, "", $2)
      if ($1 == key) {
        found=$2
      }
    }
    END { print found }
  ' "$cfg_file"
}

start_controller() {
  local max_completed_calls="$1"
  local append_mode="${2:-0}"
  local start_lines=0
  if [[ "$append_mode" == "1" && -f "$ARI_TRACE" ]]; then
    start_lines="$(wc -l < "$ARI_TRACE")"
  fi

  if [[ "$append_mode" == "1" ]]; then
    "$PYTHON_BIN" "$SCRIPT_DIR/day5_ari_external_media_controller.py" \
      --ari-host "$ARI_HOST" \
      --ari-port "$ARI_PORT" \
      --ari-username "$ARI_USERNAME" \
      --ari-password "$ARI_PASSWORD" \
      --app-name "$ARI_APP" \
      --gateway-base-url "$GATEWAY_BASE_URL" \
      --gateway-rtp-ip "127.0.0.1" \
      --gateway-rtp-port-start 32000 \
      --gateway-rtp-port-end 32999 \
      --max-completed-calls "$max_completed_calls" \
      --idle-timeout-seconds "$CONTROLLER_IDLE_TIMEOUT_SECONDS" \
      --gateway-echo-enabled 0 \
      --blind-transfer-enabled 1 \
      --blind-transfer-endpoint "$TRANSFER_ENDPOINT" \
      --blind-transfer-use-continue 1 \
      --blind-transfer-delay-seconds "$TRANSFER_DELAY_SECONDS" \
      --tenant-id "$TENANT_ID" \
      --tenant-max-active-calls "$TENANT_MAX_ACTIVE_CALLS" \
      --tenant-max-transfer-inflight "$TENANT_MAX_TRANSFER_INFLIGHT" >>"$ARI_TRACE" 2>&1 &
  else
    "$PYTHON_BIN" "$SCRIPT_DIR/day5_ari_external_media_controller.py" \
      --ari-host "$ARI_HOST" \
      --ari-port "$ARI_PORT" \
      --ari-username "$ARI_USERNAME" \
      --ari-password "$ARI_PASSWORD" \
      --app-name "$ARI_APP" \
      --gateway-base-url "$GATEWAY_BASE_URL" \
      --gateway-rtp-ip "127.0.0.1" \
      --gateway-rtp-port-start 32000 \
      --gateway-rtp-port-end 32999 \
      --max-completed-calls "$max_completed_calls" \
      --idle-timeout-seconds "$CONTROLLER_IDLE_TIMEOUT_SECONDS" \
      --gateway-echo-enabled 0 \
      --blind-transfer-enabled 1 \
      --blind-transfer-endpoint "$TRANSFER_ENDPOINT" \
      --blind-transfer-use-continue 1 \
      --blind-transfer-delay-seconds "$TRANSFER_DELAY_SECONDS" \
      --tenant-id "$TENANT_ID" \
      --tenant-max-active-calls "$TENANT_MAX_ACTIVE_CALLS" \
      --tenant-max-transfer-inflight "$TENANT_MAX_TRANSFER_INFLIGHT" >"$ARI_TRACE" 2>&1 &
  fi
  ARI_PID=$!

  for _ in $(seq 1 150); do
    if ! ps -p "$ARI_PID" >/dev/null 2>&1; then
      echo "[ERROR] Controller process exited before startup marker."
      tail -n 120 "$ARI_TRACE" || true
      return 1
    fi
    if tail -n "+$((start_lines + 1))" "$ARI_TRACE" 2>/dev/null | grep -q '"event": "controller_started"'; then
      return 0
    fi
    sleep 0.1
  done

  echo "[ERROR] Controller did not emit controller_started marker."
  tail -n 120 "$ARI_TRACE" || true
  return 1
}

stop_controller() {
  if [[ -n "$ARI_PID" ]] && ps -p "$ARI_PID" >/dev/null 2>&1; then
    kill "$ARI_PID" >/dev/null 2>&1 || true
    for _ in $(seq 1 80); do
      if ! ps -p "$ARI_PID" >/dev/null 2>&1; then
        break
      fi
      sleep 0.1
    done
    if ps -p "$ARI_PID" >/dev/null 2>&1; then
      kill -9 "$ARI_PID" >/dev/null 2>&1 || true
    fi
    wait "$ARI_PID" >/dev/null 2>&1 || true
  fi
  ARI_PID=""
}

cleanup() {
  set +e
  stop_controller

  local runtime_gw_pid=""
  if [[ -f "$GATEWAY_PID_FILE" ]]; then
    runtime_gw_pid="$(cat "$GATEWAY_PID_FILE" 2>/dev/null || true)"
  fi
  if [[ -z "$runtime_gw_pid" ]]; then
    runtime_gw_pid="$GW_PID"
  fi
  if [[ -n "$runtime_gw_pid" ]] && ps -p "$runtime_gw_pid" >/dev/null 2>&1; then
    kill "$runtime_gw_pid" >/dev/null 2>&1 || true
    wait "$runtime_gw_pid" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT


echo "[1/20] Verifying Python runtime for Day 10 probe..."
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] Python interpreter not found: $PYTHON_BIN"
  echo "Set DAY10_PYTHON_BIN to a Python with requests installed."
  exit 1
fi
"$PYTHON_BIN" - <<'PY'
import importlib.util
mods = ["requests"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
    raise SystemExit(f"Missing required modules: {', '.join(missing)}")
print("python dependency check: ok")
PY

echo "    Checking RTP media pool span for Day 10..."
rtpe_min="$(read_kv_cfg "$RTPENGINE_CFG" "port-min")"
rtpe_max="$(read_kv_cfg "$RTPENGINE_CFG" "port-max")"
ast_min="$(read_kv_cfg "$ASTERISK_RTP_CFG" "rtpstart")"
ast_max="$(read_kv_cfg "$ASTERISK_RTP_CFG" "rtpend")"

if [[ -z "$rtpe_min" || -z "$rtpe_max" || -z "$ast_min" || -z "$ast_max" ]]; then
  echo "[ERROR] Unable to parse RTP ranges from config files."
  echo "        RTPengine: $RTPENGINE_CFG"
  echo "        Asterisk:  $ASTERISK_RTP_CFG"
  exit 1
fi

if ! [[ "$rtpe_min" =~ ^[0-9]+$ && "$rtpe_max" =~ ^[0-9]+$ && "$ast_min" =~ ^[0-9]+$ && "$ast_max" =~ ^[0-9]+$ ]]; then
  echo "[ERROR] RTP range values must be numeric."
  echo "        RTPengine: $rtpe_min-$rtpe_max"
  echo "        Asterisk:  $ast_min-$ast_max"
  exit 1
fi

rtpe_span=$((rtpe_max - rtpe_min + 1))
ast_span=$((ast_max - ast_min + 1))
if (( rtpe_span < RTP_MIN_PORT_SPAN || ast_span < RTP_MIN_PORT_SPAN )); then
  echo "[ERROR] RTP media range is too small for Day 10 concurrency gates."
  echo "        RTPengine: ${rtpe_min}-${rtpe_max} (span=${rtpe_span})"
  echo "        Asterisk:  ${ast_min}-${ast_max} (span=${ast_span})"
  echo "        Required span: ${RTP_MIN_PORT_SPAN}"
  exit 1
fi
if (( rtpe_min <= ast_max && ast_min <= rtpe_max )); then
  echo "[ERROR] RTPengine and Asterisk RTP ranges overlap."
  echo "        RTPengine: ${rtpe_min}-${rtpe_max}"
  echo "        Asterisk:  ${ast_min}-${ast_max}"
  echo "        Configure non-overlapping RTP pools before Day 10 load."
  exit 1
fi
echo "    RTP pool check: ok (rtpengine=${rtpe_min}-${rtpe_max}, asterisk=${ast_min}-${ast_max})"


echo "[2/20] Ensuring required telephony containers are running..."
if [[ "$COMPOSE_FORCE_RECREATE" == "1" ]]; then
  "${compose_cmd[@]}" up -d asterisk rtpengine opensips
else
  "${compose_cmd[@]}" up -d --no-recreate asterisk rtpengine opensips
fi


echo "[3/20] Reloading Asterisk ARI/http config..."
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "core show uptime seconds" >/dev/null
docker exec -i talky-asterisk sh -lc "cat > /etc/asterisk/http.conf" < "$TELEPHONY_ROOT/asterisk/conf/http.conf"
docker exec -i talky-asterisk sh -lc "cat > /etc/asterisk/ari.conf" < "$TELEPHONY_ROOT/asterisk/conf/ari.conf"
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "core reload" >/dev/null
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_http_websocket.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_ari.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_ari_channels.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "http show status" | grep -qi "Server Enabled and Bound to"


echo "[4/20] Verifying ARI API reachability..."
"$PYTHON_BIN" - <<PY
import requests
url = "http://${ARI_HOST}:${ARI_PORT}/ari/asterisk/info"
resp = requests.get(url, params={"api_key": "${ARI_USERNAME}:${ARI_PASSWORD}"}, timeout=5)
if resp.status_code != 200:
    raise SystemExit(f"ARI ping failed: {resp.status_code} {resp.text[:300]}")
print("ari ping: ok")
PY


echo "[5/20] Capturing ARI baseline state..."
"$PYTHON_BIN" - <<PY
import json
import requests
from pathlib import Path

api = {"api_key": "${ARI_USERNAME}:${ARI_PASSWORD}"}
base = "http://${ARI_HOST}:${ARI_PORT}/ari"
channels = requests.get(f"{base}/channels", params=api, timeout=5).json()
bridges = requests.get(f"{base}/bridges", params=api, timeout=5).json()
payload = {
    "external_channel_ids": sorted(str(ch.get("id") or "") for ch in channels if str(ch.get("name") or "").startswith("UnicastRTP/")),
    "bridge_ids": sorted(str(b.get("id") or "") for b in bridges),
}
Path("$ARI_BASELINE_JSON").write_text(json.dumps(payload, indent=2), encoding="utf-8")
print("ari baseline capture: ok")
PY


echo "[6/20] Building C++ voice gateway and running unit tests..."
cmake -S "$GATEWAY_DIR" -B "$GATEWAY_BUILD_DIR" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$GATEWAY_BUILD_DIR" -- -j"$(nproc)" >/dev/null
ctest --test-dir "$GATEWAY_BUILD_DIR" --output-on-failure >/dev/null


echo "[7/20] Starting C++ voice gateway runtime..."
"$GATEWAY_BUILD_DIR/voice_gateway" --host "$GATEWAY_HOST" --port "$GATEWAY_HTTP_PORT" >"$GATEWAY_LOG" 2>&1 &
GW_PID=$!
echo "$GW_PID" >"$GATEWAY_PID_FILE"
for _ in $(seq 1 100); do
  if curl -fsS "$GATEWAY_BASE_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
curl -fsS "$GATEWAY_BASE_URL/health" >/dev/null


echo "[8/20] Starting ARI controller for Day 10 load window..."
start_controller 0 0


echo "[9/20] Running Day 10 concurrency + soak probe..."
"$PYTHON_BIN" "$SCRIPT_DIR/day10_concurrency_soak_probe.py" \
  --host 127.0.0.1 \
  --port "$SIP_PORT" \
  --extension "$EXTENSION" \
  --bind-ip 127.0.0.1 \
  --stage-concurrency "$STAGE_CONCURRENCY" \
  --stage-duration-seconds "$STAGE_DURATION_SECONDS" \
  --soak-duration-seconds "$SOAK_DURATION_SECONDS" \
  --smoke-duration-seconds "$SMOKE_DURATION_SECONDS" \
  --sample-interval-seconds "$SAMPLE_INTERVAL_SECONDS" \
  --min-dispatch-interval-seconds "$MIN_DISPATCH_INTERVAL_SECONDS" \
  --hold-seconds "$HOLD_SECONDS" \
  --profile-baseline-percent "$PROFILE_BASELINE_PERCENT" \
  --profile-bargein-percent "$PROFILE_BARGEIN_PERCENT" \
  --profile-transfer-percent "$PROFILE_TRANSFER_PERCENT" \
  --tenant-ids "$TENANT_IDS" \
  --setup-success-min "$SETUP_SUCCESS_MIN" \
  --srd-p95-max-ms "$SRD_P95_MAX_MS" \
  --sdd-p95-max-ms "$SDD_P95_MAX_MS" \
  --isa-max-percent "$ISA_MAX_PERCENT" \
  --transfer-success-min "$TRANSFER_SUCCESS_MIN" \
  --bargein-reaction-p95-max-ms "$BARGEIN_REACTION_P95_MAX_MS" \
  --require-transfer "$REQUIRE_TRANSFER" \
  --require-bargein-reaction "$REQUIRE_BARGEIN_REACTION" \
  --require-tenant-fairness "$REQUIRE_TENANT_FAIRNESS" \
  --gateway-base-url "$GATEWAY_BASE_URL" \
  --output-harness-smoke "$HARN_SMOKE_JSON" \
  --output-ramp "$RAMP_RESULTS_JSON" \
  --output-capacity "$CAPACITY_REPORT_JSON" \
  --output-soak-summary "$SOAK_SUMMARY_JSON" \
  --output-soak-timeseries "$SOAK_TIMESERIES_CSV" \
  --output-transfer "$TRANSFER_REPORT_JSON" \
  --output-bargein "$BARGEIN_REPORT_JSON" \
  --output-tenant-fairness "$TENANT_FAIRNESS_JSON" \
  --output-call-results "$CALL_RESULTS_JSON"


echo "[10/20] Stopping ARI controller after primary load run..."
stop_controller


echo "[11/20] Running Day 10 restart recovery drills..."
DAY10_GATEWAY_HOST="$GATEWAY_HOST" \
DAY10_GATEWAY_PORT="$GATEWAY_HTTP_PORT" \
DAY10_GATEWAY_LOG="$GATEWAY_LOG" \
DAY10_GATEWAY_PID_FILE="$GATEWAY_PID_FILE" \
"$SCRIPT_DIR/day10_restart_recovery_drill.sh" "$ENV_FILE" "$EVIDENCE_DIR" "$GATEWAY_BUILD_DIR/voice_gateway"



echo "[12/20] Re-starting ARI controller for post-recovery smoke..."
start_controller 20 1

"$PYTHON_BIN" "$SCRIPT_DIR/day10_concurrency_soak_probe.py" \
  --host 127.0.0.1 \
  --port "$SIP_PORT" \
  --extension "$EXTENSION" \
  --bind-ip 127.0.0.1 \
  --stage-concurrency "2" \
  --stage-duration-seconds "20" \
  --soak-duration-seconds "0" \
  --smoke-duration-seconds "8" \
  --sample-interval-seconds "2" \
  --min-dispatch-interval-seconds "$MIN_DISPATCH_INTERVAL_SECONDS" \
  --hold-seconds "$HOLD_SECONDS" \
  --profile-baseline-percent 100 \
  --profile-bargein-percent 0 \
  --profile-transfer-percent 0 \
  --tenant-ids "$TENANT_ID" \
  --setup-success-min "$SETUP_SUCCESS_MIN" \
  --srd-p95-max-ms "$SRD_P95_MAX_MS" \
  --sdd-p95-max-ms "$SDD_P95_MAX_MS" \
  --isa-max-percent "$ISA_MAX_PERCENT" \
  --transfer-success-min "$TRANSFER_SUCCESS_MIN" \
  --bargein-reaction-p95-max-ms "$BARGEIN_REACTION_P95_MAX_MS" \
  --require-transfer 0 \
  --require-bargein-reaction 0 \
  --require-tenant-fairness 0 \
  --gateway-base-url "$GATEWAY_BASE_URL" \
  --output-harness-smoke "$RECOVERY_SMOKE_JSON" \
  --output-ramp "$EVIDENCE_DIR/day10_recovery_smoke_ramp.json" \
  --output-capacity "$EVIDENCE_DIR/day10_recovery_smoke_capacity.json" \
  --output-soak-summary "$EVIDENCE_DIR/day10_recovery_smoke_soak.json" \
  --output-soak-timeseries "$EVIDENCE_DIR/day10_recovery_smoke_timeseries.csv" \
  --output-transfer "$EVIDENCE_DIR/day10_recovery_smoke_transfer.json" \
  --output-bargein "$EVIDENCE_DIR/day10_recovery_smoke_bargein.json" \
  --output-tenant-fairness "$EVIDENCE_DIR/day10_recovery_smoke_fairness.json" \
  --enforce-gates 1

stop_controller


echo "[13/20] Validating transfer and concurrency signals from ARI trace..."
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path

trace_path = Path("$ARI_TRACE")
events = []
for line in trace_path.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        events.append(json.loads(line))
    except json.JSONDecodeError:
        continue

counts = {
    "transfer_redirect_dispatched": 0,
    "transfer_continue_dispatched": 0,
    "transfer_failed": 0,
    "call_rejected_concurrency": 0,
    "transfer_rejected_concurrency": 0,
}
for event in events:
    et = str(event.get("event") or "")
    if et in counts:
        counts[et] += 1

if int("$REQUIRE_TRANSFER") == 1:
    transfer_total = counts["transfer_redirect_dispatched"] + counts["transfer_continue_dispatched"]
    if transfer_total <= 0:
        raise SystemExit("transfer required but no transfer dispatch events were recorded")
if counts["transfer_failed"] > 0:
    raise SystemExit(f"transfer_failed events detected: {counts['transfer_failed']}")
print("day10 ari trace validation: ok")
PY


echo "[14/20] Capturing gateway stats and active-session cleanup evidence..."
curl -fsS "$GATEWAY_BASE_URL/stats" >"$GATEWAY_STATS_JSON"


echo "[15/20] Capturing ARI post state and leak audit..."
"$PYTHON_BIN" - <<PY
import json
import requests
from pathlib import Path

api = {"api_key": "${ARI_USERNAME}:${ARI_PASSWORD}"}
base = "http://${ARI_HOST}:${ARI_PORT}/ari"
baseline = json.loads(Path("$ARI_BASELINE_JSON").read_text(encoding="utf-8"))
channels = requests.get(f"{base}/channels", params=api, timeout=5).json()
bridges = requests.get(f"{base}/bridges", params=api, timeout=5).json()
post = {
    "external_channel_ids": sorted(str(ch.get("id") or "") for ch in channels if str(ch.get("name") or "").startswith("UnicastRTP/")),
    "bridge_ids": sorted(str(b.get("id") or "") for b in bridges),
}
Path("$ARI_POST_JSON").write_text(json.dumps(post, indent=2), encoding="utf-8")

sessions = requests.get("${GATEWAY_BASE_URL}/v1/sessions", timeout=5).json().get("sessions", [])
active_sessions = [s for s in sessions if str(s.get("state") or "") not in {"stopped", "failed"}]

baseline_external = set(baseline.get("external_channel_ids", []))
baseline_bridges = set(baseline.get("bridge_ids", []))
post_external = set(post.get("external_channel_ids", []))
post_bridges = set(post.get("bridge_ids", []))

leaked_external = sorted(post_external - baseline_external)
leaked_bridges = sorted(post_bridges - baseline_bridges)

report = {
    "baseline": baseline,
    "post": post,
    "leaked_external_channel_ids": leaked_external,
    "leaked_bridge_ids": leaked_bridges,
    "gateway_active_session_ids": [str(s.get("session_id") or "") for s in active_sessions],
    "gateway_active_session_count": len(active_sessions),
    "pass": not leaked_external and not leaked_bridges and not active_sessions,
}
Path("$LEAK_AUDIT_REPORT_JSON").write_text(json.dumps(report, indent=2), encoding="utf-8")
if not report["pass"]:
    raise SystemExit(f"Leak audit failed: {report}")
print("day10 leak audit: ok")
PY


echo "[16/20] Writing session timer report..."
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path

pjsip_conf = Path("$TELEPHONY_ROOT/asterisk/conf/pjsip.conf").read_text(encoding="utf-8", errors="ignore")
markers = {
    "timers": "timers=" in pjsip_conf,
    "timers_min_se": "timers_min_se=" in pjsip_conf,
    "timers_sess_expires": "timers_sess_expires=" in pjsip_conf,
}
payload = {
    "status": "observed" if any(markers.values()) else "not_configured",
    "markers": markers,
    "note": "Session-timer integrity for Day10 should be validated with long-call profile if enabled.",
}
Path("$SESSION_TIMER_REPORT_JSON").write_text(json.dumps(payload, indent=2), encoding="utf-8")
print("session timer report: ok")
PY


echo "[17/20] Computing final Go/No-Go decision..."
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path
from datetime import UTC, datetime

capacity = json.loads(Path("$CAPACITY_REPORT_JSON").read_text(encoding="utf-8"))
soak = json.loads(Path("$SOAK_SUMMARY_JSON").read_text(encoding="utf-8"))
transfer = json.loads(Path("$TRANSFER_REPORT_JSON").read_text(encoding="utf-8"))
bargein = json.loads(Path("$BARGEIN_REPORT_JSON").read_text(encoding="utf-8"))
fairness = json.loads(Path("$TENANT_FAIRNESS_JSON").read_text(encoding="utf-8"))
leak = json.loads(Path("$LEAK_AUDIT_REPORT_JSON").read_text(encoding="utf-8"))

recovery_files = [
    "$RECOVERY_OPENSIPS_JSON",
    "$RECOVERY_ASTERISK_JSON",
    "$RECOVERY_RTPENGINE_JSON",
    "$RECOVERY_GATEWAY_JSON",
]
recovery = [json.loads(Path(path).read_text(encoding="utf-8")) for path in recovery_files]
recovery_pass = all(r.get("status") == "passed" for r in recovery)

checks = {
    "ramp_pass": bool(capacity.get("ramp_gate_passed", False)),
    "soak_pass": bool(soak.get("pass", False)),
    "transfer_pass": bool(transfer.get("pass", False)),
    "bargein_pass": bool(bargein.get("pass", False)),
    "tenant_fairness_pass": bool(fairness.get("pass", False)),
    "recovery_pass": recovery_pass,
    "leak_pass": bool(leak.get("pass", False)),
}

failed = [name for name, ok in checks.items() if not ok]
go = len(failed) == 0

payload = {
    "generated_at_utc": datetime.now(UTC).isoformat(),
    "decision": "go" if go else "no-go",
    "checks": checks,
    "failed_checks": failed,
    "safe_concurrency_threshold": capacity.get("safe_concurrency_threshold"),
    "recommended_concurrency": capacity.get("recommended_concurrency"),
    "recovery_results": recovery,
}
Path("$GO_NO_GO_JSON").write_text(json.dumps(payload, indent=2), encoding="utf-8")

lines = [
    "# Day 10 Go/No-Go Checklist",
    "",
    f"Generated (UTC): {payload['generated_at_utc']}",
    f"Decision: {payload['decision'].upper()}",
    "",
    "## Gate Checks",
]
for key, ok in checks.items():
    lines.append(f"- {'PASS' if ok else 'FAIL'}: {key}")
if failed:
    lines.append("")
    lines.append("## Blocking Failures")
    for item in failed:
        lines.append(f"- {item}")

Path("$GO_NO_GO_CHECKLIST_MD").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("go/no-go decision computed")
if not go:
    raise SystemExit("Day10 Go/No-Go decision is NO-GO")
PY


echo "[18/20] Writing Day 10 evidence markdown..."
DAY10_CAPACITY_JSON="$CAPACITY_REPORT_JSON" \
DAY10_SOAK_JSON="$SOAK_SUMMARY_JSON" \
DAY10_GO_NO_GO_JSON="$GO_NO_GO_JSON" \
DAY10_EVIDENCE_DOC="$EVIDENCE_DOC" \
"$PYTHON_BIN" - <<'PY'
import json
import os
from datetime import UTC, datetime
from pathlib import Path

capacity = json.loads(Path(os.environ["DAY10_CAPACITY_JSON"]).read_text(encoding="utf-8"))
soak = json.loads(Path(os.environ["DAY10_SOAK_JSON"]).read_text(encoding="utf-8"))
decision = json.loads(Path(os.environ["DAY10_GO_NO_GO_JSON"]).read_text(encoding="utf-8"))

doc = f"""# Day 10 Concurrency + Soak Evidence

Date: {datetime.now(UTC).strftime('%Y-%m-%d')}  
Verifier: `telephony/scripts/verify_day10_concurrency_soak.sh`

## Summary

1. Safe concurrency threshold: {capacity.get('safe_concurrency_threshold')}
2. Recommended production concurrency: {capacity.get('recommended_concurrency')}
3. Soak pass: {soak.get('pass')}
4. Final decision: {decision.get('decision')}

## Core Evidence Files

1. `telephony/docs/phase_3/evidence/day10/day10_verifier_output.txt`
2. `telephony/docs/phase_3/evidence/day10/day10_ramp_stage_results.json`
3. `telephony/docs/phase_3/evidence/day10/day10_capacity_threshold_report.json`
4. `telephony/docs/phase_3/evidence/day10/day10_soak_summary.json`
5. `telephony/docs/phase_3/evidence/day10/day10_recovery_timeline.log`
6. `telephony/docs/phase_3/evidence/day10/day10_go_no_go.json`
"""
Path(os.environ["DAY10_EVIDENCE_DOC"]).write_text(doc, encoding="utf-8")
print("day10 evidence markdown: ok")
PY


echo "[19/20] Final Day 10 contract checks..."
for path in \
  "$HARN_SMOKE_JSON" \
  "$RAMP_RESULTS_JSON" \
  "$CAPACITY_REPORT_JSON" \
  "$SOAK_SUMMARY_JSON" \
  "$SOAK_TIMESERIES_CSV" \
  "$TRANSFER_REPORT_JSON" \
  "$BARGEIN_REPORT_JSON" \
  "$TENANT_FAIRNESS_JSON" \
  "$RECOVERY_OPENSIPS_JSON" \
  "$RECOVERY_ASTERISK_JSON" \
  "$RECOVERY_RTPENGINE_JSON" \
  "$RECOVERY_GATEWAY_JSON" \
  "$SESSION_TIMER_REPORT_JSON" \
  "$LEAK_AUDIT_REPORT_JSON" \
  "$GO_NO_GO_JSON" \
  "$GO_NO_GO_CHECKLIST_MD" \
  "$RECOVERY_TIMELINE_LOG"; do
  if [[ ! -f "$path" ]]; then
    echo "[ERROR] Missing Day 10 artifact: $path"
    exit 1
  fi
done


echo "[20/20] Day 10 verifier complete."
echo "Day 10 verification PASSED."
