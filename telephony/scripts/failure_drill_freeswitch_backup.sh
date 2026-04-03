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
  echo "[ERROR] Docker daemon is required for WS-N FreeSWITCH backup drill."
  exit 1
fi

mkdir -p "$EVIDENCE_DIR"

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
compose_backup_cmd=(docker compose --profile backup --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
SIP_PORT="$(ws_n_read_env "OPENSIPS_SIP_PORT" "15060" "$ENV_FILE")"
FREESWITCH_IMAGE="$(ws_n_read_env "FREESWITCH_IMAGE" "safarov/freeswitch:latest" "$ENV_FILE")"
RECOVERY_MAX_SECONDS="${WS_N_FREESWITCH_RECOVERY_MAX_SECONDS:-120}"

TIMELINE_FILE="$EVIDENCE_DIR/n3_freeswitch_backup_timeline.log"
PRE_METRICS="$EVIDENCE_DIR/n3_freeswitch_backup_pre.prom"
POST_METRICS="$EVIDENCE_DIR/n3_freeswitch_backup_post.prom"
RESULT_JSON="$EVIDENCE_DIR/n3_freeswitch_backup_result.json"
PROBE_PRIMARY_LOG="$EVIDENCE_DIR/n3_freeswitch_backup_primary_probe.log"

rm -f "$TIMELINE_FILE" "$PRE_METRICS" "$POST_METRICS" "$RESULT_JSON" "$PROBE_PRIMARY_LOG"

if ! docker image inspect "$FREESWITCH_IMAGE" >/dev/null 2>&1; then
  ws_n_write_result_json "$RESULT_JSON" "N3" "failed" "$(ws_n_now_epoch)" "$(ws_n_now_epoch)" "0" "0" "FreeSWITCH image unavailable: $FREESWITCH_IMAGE"
  echo "[ERROR] FreeSWITCH image is not available locally: $FREESWITCH_IMAGE"
  exit 1
fi

initial_exists=0
initial_running=0
if docker inspect talky-freeswitch >/dev/null 2>&1; then
  initial_exists=1
  initial_state="$(docker inspect --format '{{.State.Status}}' talky-freeswitch 2>/dev/null || echo unknown)"
  if [[ "$initial_state" == "running" ]]; then
    initial_running=1
  fi
else
  initial_state="missing"
fi

restore_freeswitch_baseline() {
  if [[ "$initial_exists" -eq 0 ]]; then
    docker rm -f talky-freeswitch >/dev/null 2>&1 || true
    return 0
  fi

  if [[ "$initial_running" -eq 1 ]]; then
    docker start talky-freeswitch >/dev/null 2>&1 || true
  else
    docker stop talky-freeswitch >/dev/null 2>&1 || true
  fi
}
trap restore_freeswitch_baseline EXIT

wait_for_freeswitch_cli() {
  local timeout_seconds="$1"
  local start_epoch
  start_epoch="$(ws_n_now_epoch)"

  while true; do
    if docker exec talky-freeswitch fs_cli -x status >/dev/null 2>&1; then
      return 0
    fi
    local now_epoch
    now_epoch="$(ws_n_now_epoch)"
    if (( now_epoch - start_epoch >= timeout_seconds )); then
      return 1
    fi
    sleep 1
  done
}

ws_n_append_timeline "$TIMELINE_FILE" "drill_start" "N3 FreeSWITCH backup disruption drill started (initial_state=${initial_state})"

echo "[N3] Ensuring primary services are up..."
"${compose_cmd[@]}" up -d asterisk rtpengine opensips >/dev/null
ws_n_wait_for_status "talky-asterisk" 120 healthy running >/dev/null
ws_n_wait_for_status "talky-opensips" 120 healthy running >/dev/null
ws_n_wait_for_status "talky-rtpengine" 120 healthy running >/dev/null

echo "[N3] Starting backup FreeSWITCH profile..."
"${compose_backup_cmd[@]}" up -d freeswitch >/dev/null
ws_n_wait_for_status "talky-freeswitch" 180 healthy running >/dev/null
if ! wait_for_freeswitch_cli 60; then
  ws_n_write_result_json "$RESULT_JSON" "N3" "failed" "$(ws_n_now_epoch)" "$(ws_n_now_epoch)" "0" "0" "FreeSWITCH CLI did not become ready."
  echo "[ERROR] FreeSWITCH fs_cli did not become ready."
  exit 1
fi
ws_n_append_timeline "$TIMELINE_FILE" "backup_ready" "FreeSWITCH backup profile running and CLI ready"

echo "[N3] Capturing baseline metrics..."
ws_n_capture_metrics "$PRE_METRICS"

stop_epoch="$(ws_n_now_epoch)"
ws_n_append_timeline "$TIMELINE_FILE" "inject_stop_freeswitch" "Stopping talky-freeswitch"
docker stop talky-freeswitch >/dev/null
ws_n_wait_for_status "talky-freeswitch" 30 exited dead >/dev/null || true
failure_detected_epoch="$(ws_n_now_epoch)"

echo "[N3] Verifying primary path remains healthy..."
if ! python3 "$SCRIPT_DIR/sip_options_probe.py" --host 127.0.0.1 --port "$SIP_PORT" --timeout 3.0 >"$PROBE_PRIMARY_LOG" 2>&1; then
  ws_n_append_timeline "$TIMELINE_FILE" "primary_probe_failed" "Primary SIP probe failed during backup disruption"
  ws_n_write_result_json "$RESULT_JSON" "N3" "failed" "$stop_epoch" "$(ws_n_now_epoch)" "0" "0" "Primary SIP probe failed while disrupting backup FreeSWITCH."
  echo "[ERROR] Primary SIP probe failed during FreeSWITCH backup disruption."
  exit 1
fi
if ! docker exec talky-asterisk asterisk -rx "core show uptime seconds" >/dev/null 2>&1; then
  ws_n_append_timeline "$TIMELINE_FILE" "asterisk_health_failed" "Asterisk health check failed during backup disruption"
  ws_n_write_result_json "$RESULT_JSON" "N3" "failed" "$stop_epoch" "$(ws_n_now_epoch)" "0" "0" "Asterisk health check failed during backup disruption."
  echo "[ERROR] Asterisk health check failed during FreeSWITCH backup disruption."
  exit 1
fi
ws_n_append_timeline "$TIMELINE_FILE" "primary_healthy" "Primary stack remained healthy during backup disruption"

echo "[N3] Restarting backup FreeSWITCH..."
restart_epoch="$(ws_n_now_epoch)"
docker start talky-freeswitch >/dev/null
ws_n_wait_for_status "talky-freeswitch" 180 healthy running >/dev/null
if ! wait_for_freeswitch_cli 60; then
  ws_n_append_timeline "$TIMELINE_FILE" "backup_recovery_failed" "FreeSWITCH CLI did not recover after restart"
  ws_n_write_result_json "$RESULT_JSON" "N3" "failed" "$stop_epoch" "$(ws_n_now_epoch)" "0" "0" "FreeSWITCH CLI did not recover after restart."
  echo "[ERROR] FreeSWITCH CLI did not recover after restart."
  exit 1
fi
recovered_epoch="$(ws_n_now_epoch)"

echo "[N3] Capturing recovery metrics..."
ws_n_capture_metrics "$POST_METRICS"

outage_seconds="$((failure_detected_epoch - stop_epoch))"
recovery_seconds="$((recovered_epoch - restart_epoch))"
if (( recovery_seconds > RECOVERY_MAX_SECONDS )); then
  ws_n_append_timeline "$TIMELINE_FILE" "threshold_failed" "Recovery ${recovery_seconds}s exceeded threshold ${RECOVERY_MAX_SECONDS}s"
  ws_n_write_result_json "$RESULT_JSON" "N3" "failed" "$stop_epoch" "$recovered_epoch" "$outage_seconds" "$recovery_seconds" "Recovery exceeded threshold (${RECOVERY_MAX_SECONDS}s)."
  echo "[ERROR] FreeSWITCH backup recovery exceeded threshold: ${recovery_seconds}s > ${RECOVERY_MAX_SECONDS}s"
  exit 1
fi

ws_n_append_timeline "$TIMELINE_FILE" "drill_complete" "N3 passed (outage_detect=${outage_seconds}s, recovery=${recovery_seconds}s)"
ws_n_write_result_json "$RESULT_JSON" "N3" "passed" "$stop_epoch" "$recovered_epoch" "$outage_seconds" "$recovery_seconds" "FreeSWITCH backup disruption drill passed."

echo "[N3] FreeSWITCH backup disruption drill PASSED (recovery=${recovery_seconds}s)"

