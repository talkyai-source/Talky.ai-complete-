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
  echo "[ERROR] Docker daemon is required for WS-N combined drill."
  exit 1
fi

mkdir -p "$EVIDENCE_DIR"

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
SIP_PORT="$(ws_n_read_env "OPENSIPS_SIP_PORT" "15060" "$ENV_FILE")"
RECOVERY_MAX_SECONDS="${WS_N_COMBINED_RECOVERY_MAX_SECONDS:-180}"

TIMELINE_FILE="$EVIDENCE_DIR/n4_combined_timeline.log"
PRE_METRICS="$EVIDENCE_DIR/n4_combined_pre.prom"
POST_METRICS="$EVIDENCE_DIR/n4_combined_post.prom"
RESULT_JSON="$EVIDENCE_DIR/n4_combined_result.json"
PROBE_AFTER_LOG="$EVIDENCE_DIR/n4_combined_probe_after.log"

rm -f "$TIMELINE_FILE" "$PRE_METRICS" "$POST_METRICS" "$RESULT_JSON" "$PROBE_AFTER_LOG"

restore_primary() {
  "${compose_cmd[@]}" up -d rtpengine asterisk opensips >/dev/null 2>&1 || true
}
trap restore_primary EXIT

ws_n_append_timeline "$TIMELINE_FILE" "drill_start" "N4 combined failure drill started"

echo "[N4] Ensuring primary services are up..."
"${compose_cmd[@]}" up -d asterisk rtpengine opensips >/dev/null
ws_n_wait_for_status "talky-asterisk" 120 healthy running >/dev/null
ws_n_wait_for_status "talky-rtpengine" 120 healthy running >/dev/null
ws_n_wait_for_status "talky-opensips" 120 healthy running >/dev/null

ws_n_capture_metrics "$PRE_METRICS"

start_epoch="$(ws_n_now_epoch)"
ws_n_append_timeline "$TIMELINE_FILE" "inject_multi_stop" "Stopping talky-opensips and talky-rtpengine"
docker stop talky-opensips talky-rtpengine >/dev/null

sleep 5

ws_n_append_timeline "$TIMELINE_FILE" "recover_start" "Starting rtpengine then opensips"
docker start talky-rtpengine >/dev/null
ws_n_wait_for_status "talky-rtpengine" 120 healthy running >/dev/null

docker start talky-opensips >/dev/null
ws_n_wait_for_status "talky-opensips" 120 healthy running >/dev/null
recovered_epoch="$(ws_n_now_epoch)"

if ! python3 "$SCRIPT_DIR/sip_options_probe.py" --host 127.0.0.1 --port "$SIP_PORT" --timeout 3.0 >"$PROBE_AFTER_LOG" 2>&1; then
  ws_n_append_timeline "$TIMELINE_FILE" "probe_after_failed" "SIP probe failed after combined recovery"
  ws_n_write_result_json "$RESULT_JSON" "N4" "failed" "$start_epoch" "$(ws_n_now_epoch)" "0" "0" "SIP probe failed after combined recovery."
  echo "[ERROR] SIP probe failed after combined recovery."
  exit 1
fi

ws_n_capture_metrics "$POST_METRICS"

recovery_seconds="$((recovered_epoch - start_epoch))"
if (( recovery_seconds > RECOVERY_MAX_SECONDS )); then
  ws_n_append_timeline "$TIMELINE_FILE" "threshold_failed" "Recovery ${recovery_seconds}s exceeded threshold ${RECOVERY_MAX_SECONDS}s"
  ws_n_write_result_json "$RESULT_JSON" "N4" "failed" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$recovery_seconds" "Recovery exceeded threshold (${RECOVERY_MAX_SECONDS}s)."
  echo "[ERROR] Combined recovery exceeded threshold: ${recovery_seconds}s > ${RECOVERY_MAX_SECONDS}s"
  exit 1
fi

ws_n_append_timeline "$TIMELINE_FILE" "drill_complete" "N4 passed (recovery=${recovery_seconds}s)"
ws_n_write_result_json "$RESULT_JSON" "N4" "passed" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$recovery_seconds" "Combined failure drill passed."
echo "[N4] Combined failure drill PASSED (recovery=${recovery_seconds}s)"

