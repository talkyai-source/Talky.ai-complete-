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
  echo "[ERROR] Docker daemon is required for WS-N OpenSIPS drill."
  exit 1
fi

mkdir -p "$EVIDENCE_DIR"

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
SIP_PORT="$(ws_n_read_env "OPENSIPS_SIP_PORT" "15060" "$ENV_FILE")"
RECOVERY_MAX_SECONDS="${WS_N_OPENSIPS_RECOVERY_MAX_SECONDS:-90}"

TIMELINE_FILE="$EVIDENCE_DIR/n1_opensips_timeline.log"
PRE_METRICS="$EVIDENCE_DIR/n1_opensips_pre.prom"
POST_METRICS="$EVIDENCE_DIR/n1_opensips_post.prom"
RESULT_JSON="$EVIDENCE_DIR/n1_opensips_result.json"
PROBE_DURING_LOG="$EVIDENCE_DIR/n1_opensips_probe_during.log"
PROBE_AFTER_LOG="$EVIDENCE_DIR/n1_opensips_probe_after.log"

rm -f "$TIMELINE_FILE" "$PRE_METRICS" "$POST_METRICS" "$RESULT_JSON" "$PROBE_DURING_LOG" "$PROBE_AFTER_LOG"

restore_opensips() {
  local status
  status="$(ws_n_container_status "talky-opensips")"
  if [[ "$status" != "healthy" && "$status" != "running" ]]; then
    docker start talky-opensips >/dev/null 2>&1 || true
  fi
}
trap restore_opensips EXIT

ws_n_append_timeline "$TIMELINE_FILE" "drill_start" "N1 OpenSIPS outage drill started"

echo "[N1] Ensuring primary services are up..."
"${compose_cmd[@]}" up -d asterisk rtpengine opensips >/dev/null
ws_n_wait_for_status "talky-asterisk" 120 healthy running >/dev/null
ws_n_wait_for_status "talky-rtpengine" 120 healthy running >/dev/null
ws_n_wait_for_status "talky-opensips" 120 healthy running >/dev/null

echo "[N1] Capturing baseline metrics..."
ws_n_capture_metrics "$PRE_METRICS"

stop_epoch="$(ws_n_now_epoch)"
ws_n_append_timeline "$TIMELINE_FILE" "inject_stop_opensips" "Stopping talky-opensips"
docker stop talky-opensips >/dev/null
ws_n_wait_for_status "talky-opensips" 30 exited dead >/dev/null || true

echo "[N1] Probing SIP during outage (expected failure)..."
if python3 "$SCRIPT_DIR/sip_options_probe.py" --host 127.0.0.1 --port "$SIP_PORT" --timeout 2.0 >"$PROBE_DURING_LOG" 2>&1; then
  ws_n_append_timeline "$TIMELINE_FILE" "probe_during_unexpected_success" "SIP probe unexpectedly succeeded during outage"
  ws_n_write_result_json "$RESULT_JSON" "N1" "failed" "$stop_epoch" "$(ws_n_now_epoch)" "0" "0" "Probe unexpectedly succeeded while OpenSIPS was stopped."
  echo "[ERROR] OpenSIPS outage probe unexpectedly succeeded."
  exit 1
fi
failure_detected_epoch="$(ws_n_now_epoch)"
ws_n_append_timeline "$TIMELINE_FILE" "probe_during_expected_failure" "SIP probe failed as expected while OpenSIPS stopped"

echo "[N1] Restarting OpenSIPS..."
restart_epoch="$(ws_n_now_epoch)"
docker start talky-opensips >/dev/null
ws_n_wait_for_status "talky-opensips" 120 healthy running >/dev/null

if ! python3 "$SCRIPT_DIR/sip_options_probe.py" --host 127.0.0.1 --port "$SIP_PORT" --timeout 3.0 >"$PROBE_AFTER_LOG" 2>&1; then
  ws_n_append_timeline "$TIMELINE_FILE" "probe_after_failed" "SIP probe failed after OpenSIPS restart"
  ws_n_write_result_json "$RESULT_JSON" "N1" "failed" "$stop_epoch" "$(ws_n_now_epoch)" "0" "0" "OpenSIPS recovered health but SIP probe failed."
  echo "[ERROR] SIP probe failed after OpenSIPS restart."
  exit 1
fi
recovered_epoch="$(ws_n_now_epoch)"
ws_n_append_timeline "$TIMELINE_FILE" "probe_after_success" "SIP probe succeeded after OpenSIPS restart"

echo "[N1] Capturing recovery metrics..."
ws_n_capture_metrics "$POST_METRICS"

outage_seconds="$((failure_detected_epoch - stop_epoch))"
recovery_seconds="$((recovered_epoch - restart_epoch))"
total_seconds="$((recovered_epoch - stop_epoch))"

if (( recovery_seconds > RECOVERY_MAX_SECONDS )); then
  ws_n_append_timeline "$TIMELINE_FILE" "threshold_failed" "Recovery ${recovery_seconds}s exceeded threshold ${RECOVERY_MAX_SECONDS}s"
  ws_n_write_result_json "$RESULT_JSON" "N1" "failed" "$stop_epoch" "$recovered_epoch" "$outage_seconds" "$recovery_seconds" "Recovery exceeded threshold (${RECOVERY_MAX_SECONDS}s)."
  echo "[ERROR] OpenSIPS recovery exceeded threshold: ${recovery_seconds}s > ${RECOVERY_MAX_SECONDS}s"
  exit 1
fi

ws_n_append_timeline "$TIMELINE_FILE" "drill_complete" "N1 passed (outage_detect=${outage_seconds}s, recovery=${recovery_seconds}s, total=${total_seconds}s)"
ws_n_write_result_json "$RESULT_JSON" "N1" "passed" "$stop_epoch" "$recovered_epoch" "$outage_seconds" "$recovery_seconds" "OpenSIPS outage drill passed."

echo "[N1] OpenSIPS outage drill PASSED (recovery=${recovery_seconds}s, total=${total_seconds}s)"

