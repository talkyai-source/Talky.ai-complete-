#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony.example}"
EVIDENCE_DIR="${2:-$TELEPHONY_ROOT/docs/phase_3/evidence/ws_n}"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/ws_n_common.sh"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "[ERROR] Docker daemon is required for WS-N RTPengine drill."
  exit 1
fi

mkdir -p "$EVIDENCE_DIR"

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
SIP_PORT="$(ws_n_read_env "OPENSIPS_SIP_PORT" "15060" "$ENV_FILE")"
RECOVERY_MAX_SECONDS="${WS_N_RTPENGINE_RECOVERY_MAX_SECONDS:-45}"

TIMELINE_FILE="$EVIDENCE_DIR/n2_rtpengine_timeline.log"
PRE_METRICS="$EVIDENCE_DIR/n2_rtpengine_pre.prom"
POST_METRICS="$EVIDENCE_DIR/n2_rtpengine_post.prom"
RESULT_JSON="$EVIDENCE_DIR/n2_rtpengine_result.json"
PROBE_AFTER_LOG="$EVIDENCE_DIR/n2_rtpengine_probe_after.log"

rm -f "$TIMELINE_FILE" "$PRE_METRICS" "$POST_METRICS" "$RESULT_JSON" "$PROBE_AFTER_LOG"

ws_n_append_timeline "$TIMELINE_FILE" "drill_start" "N2 RTPengine degradation drill started"

echo "[N2] Ensuring primary services are up..."
"${compose_cmd[@]}" up -d asterisk rtpengine opensips >/dev/null
ws_n_wait_for_status "talky-asterisk" 120 healthy running >/dev/null
ws_n_wait_for_status "talky-rtpengine" 120 healthy running >/dev/null
ws_n_wait_for_status "talky-opensips" 120 healthy running >/dev/null

echo "[N2] Capturing baseline metrics..."
ws_n_capture_metrics "$PRE_METRICS"

start_epoch="$(ws_n_now_epoch)"
ws_n_append_timeline "$TIMELINE_FILE" "inject_restart_rtpengine" "Restarting talky-rtpengine"
docker restart talky-rtpengine >/dev/null

ws_n_wait_for_status "talky-rtpengine" 120 healthy running >/dev/null
recovered_epoch="$(ws_n_now_epoch)"

if ! docker exec talky-rtpengine sh -lc "ss -lun | grep -q ':2223'"; then
  ws_n_append_timeline "$TIMELINE_FILE" "ng_port_failed" "RTPengine NG port 2223 not available after restart"
  ws_n_write_result_json "$RESULT_JSON" "N2" "failed" "$start_epoch" "$(ws_n_now_epoch)" "0" "0" "RTPengine NG port check failed."
  echo "[ERROR] RTPengine NG port 2223 not available after restart."
  exit 1
fi
ws_n_append_timeline "$TIMELINE_FILE" "ng_port_ok" "RTPengine NG port 2223 confirmed"

if ! python3 "$SCRIPT_DIR/sip_options_probe.py" --host 127.0.0.1 --port "$SIP_PORT" --timeout 3.0 >"$PROBE_AFTER_LOG" 2>&1; then
  ws_n_append_timeline "$TIMELINE_FILE" "sip_probe_failed" "SIP probe failed after RTPengine recovery"
  ws_n_write_result_json "$RESULT_JSON" "N2" "failed" "$start_epoch" "$(ws_n_now_epoch)" "0" "0" "SIP probe failed after RTPengine recovery."
  echo "[ERROR] SIP probe failed after RTPengine restart."
  exit 1
fi
ws_n_append_timeline "$TIMELINE_FILE" "sip_probe_ok" "SIP probe succeeded after RTPengine recovery"

echo "[N2] Capturing recovery metrics..."
ws_n_capture_metrics "$POST_METRICS"

recovery_seconds="$((recovered_epoch - start_epoch))"
if (( recovery_seconds > RECOVERY_MAX_SECONDS )); then
  ws_n_append_timeline "$TIMELINE_FILE" "threshold_failed" "Recovery ${recovery_seconds}s exceeded threshold ${RECOVERY_MAX_SECONDS}s"
  ws_n_write_result_json "$RESULT_JSON" "N2" "failed" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$recovery_seconds" "Recovery exceeded threshold (${RECOVERY_MAX_SECONDS}s)."
  echo "[ERROR] RTPengine recovery exceeded threshold: ${recovery_seconds}s > ${RECOVERY_MAX_SECONDS}s"
  exit 1
fi

ws_n_append_timeline "$TIMELINE_FILE" "drill_complete" "N2 passed (recovery=${recovery_seconds}s)"
ws_n_write_result_json "$RESULT_JSON" "N2" "passed" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$recovery_seconds" "RTPengine degradation drill passed."

echo "[N2] RTPengine degradation drill PASSED (recovery=${recovery_seconds}s)"

