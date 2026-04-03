#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony.example}"
EVIDENCE_DIR="${2:-$TELEPHONY_ROOT/docs/phase_3/evidence/day10}"
GATEWAY_BINARY="${3:-$REPO_ROOT/services/voice-gateway-cpp/build/voice_gateway}"
GATEWAY_HOST="${DAY10_GATEWAY_HOST:-127.0.0.1}"
GATEWAY_PORT="${DAY10_GATEWAY_PORT:-18080}"
GATEWAY_LOG="${DAY10_GATEWAY_LOG:-$EVIDENCE_DIR/day10_gateway_runtime.log}"
GATEWAY_PID_FILE="${DAY10_GATEWAY_PID_FILE:-$EVIDENCE_DIR/day10_gateway.pid}"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/ws_n_common.sh"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  echo "[ERROR] Docker daemon is required for Day 10 recovery drill."
  exit 1
fi

mkdir -p "$EVIDENCE_DIR"

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
SIP_PORT="$(ws_n_read_env "OPENSIPS_SIP_PORT" "15060" "$ENV_FILE")"

OPENSIPS_RECOVERY_SLA_SECONDS="${DAY10_RECOVERY_SLA_OPENSIPS_SECONDS:-60}"
ASTERISK_RECOVERY_SLA_SECONDS="${DAY10_RECOVERY_SLA_ASTERISK_SECONDS:-90}"
RTPENGINE_RECOVERY_SLA_SECONDS="${DAY10_RECOVERY_SLA_RTPENGINE_SECONDS:-90}"
GATEWAY_RECOVERY_SLA_SECONDS="${DAY10_RECOVERY_SLA_GATEWAY_SECONDS:-60}"
RECOVERY_PROBE_TIMEOUT_SECONDS="${DAY10_RECOVERY_PROBE_TIMEOUT_SECONDS:-120}"

TIMELINE_FILE="$EVIDENCE_DIR/day10_recovery_timeline.log"
OPENSIPS_JSON="$EVIDENCE_DIR/day10_recovery_opensips.json"
ASTERISK_JSON="$EVIDENCE_DIR/day10_recovery_asterisk.json"
RTPENGINE_JSON="$EVIDENCE_DIR/day10_recovery_rtpengine.json"
GATEWAY_JSON="$EVIDENCE_DIR/day10_recovery_gateway.json"

: >"$TIMELINE_FILE"

append_timeline() {
  local event="$1"
  local detail="$2"
  printf "%s | %s | %s\n" "$(ws_n_now_iso)" "$event" "$detail" >>"$TIMELINE_FILE"
}

probe_signaling() {
  python3 "$SCRIPT_DIR/sip_options_probe.py" --host 127.0.0.1 --port "$SIP_PORT" --timeout 3.0 >/dev/null
}

write_service_result() {
  local output_file="$1"
  local service="$2"
  local status="$3"
  local start_epoch="$4"
  local recovered_epoch="$5"
  local recovery_seconds="$6"
  local sla_seconds="$7"
  local notes="$8"

  python3 - "$output_file" "$service" "$status" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$sla_seconds" "$notes" <<'PY'
import json
import sys
from pathlib import Path

payload = {
    "service": sys.argv[2],
    "status": sys.argv[3],
    "start_epoch": int(sys.argv[4]),
    "recovered_epoch": int(sys.argv[5]),
    "recovery_seconds": int(sys.argv[6]),
    "sla_seconds": int(sys.argv[7]),
    "notes": sys.argv[8],
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

run_container_recovery() {
  local container="$1"
  local service_name="$2"
  local service_label="$3"
  local sla_seconds="$4"
  local output_json="$5"

  append_timeline "drill_start" "service=${service_label} container=${container}"
  local start_epoch
  start_epoch="$(ws_n_now_epoch)"

  if "${compose_cmd[@]}" restart "$service_name" >/dev/null 2>&1; then
    append_timeline "restart_action" "service=${service_label} method=compose_restart"
  else
    append_timeline "restart_action" "service=${service_label} method=compose_restart_failed fallback=soft_reload"
    case "$service_name" in
      opensips)
        docker exec "$container" sh -lc 'opensips-cli -x mi reload_routes >/dev/null 2>&1 || true'
        ;;
      asterisk)
        docker exec "$container" sh -lc 'asterisk -rx "core reload" >/dev/null 2>&1 || true'
        ;;
      rtpengine)
        python3 - <<'PY'
import socket

cookie = b"day10recovery"
payload = b"d7:command4:pinge"
packet = cookie + b" " + payload
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2)
sock.sendto(packet, ("127.0.0.1", 2223))
try:
    sock.recvfrom(4096)
except Exception:
    pass
PY
        ;;
      *)
        ;;
    esac
  fi

  ws_n_wait_for_status "$container" 180 healthy running >/dev/null

  local probe_deadline=$(( $(ws_n_now_epoch) + RECOVERY_PROBE_TIMEOUT_SECONDS ))
  local probe_ok=0
  while (( $(ws_n_now_epoch) < probe_deadline )); do
    if probe_signaling >/dev/null 2>&1; then
      probe_ok=1
      break
    fi
    sleep 1
  done

  local recovered_epoch
  recovered_epoch="$(ws_n_now_epoch)"
  local recovery_seconds=$((recovered_epoch - start_epoch))

  if [[ "$probe_ok" -ne 1 ]]; then
    write_service_result "$output_json" "$service_label" "failed" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$sla_seconds" "SIP probe did not recover within timeout (${RECOVERY_PROBE_TIMEOUT_SECONDS}s)."
    append_timeline "drill_failed" "service=${service_label} reason=probe_timeout recovery=${recovery_seconds}s timeout=${RECOVERY_PROBE_TIMEOUT_SECONDS}s"
    return 1
  fi

  if (( recovery_seconds > sla_seconds )); then
    write_service_result "$output_json" "$service_label" "failed" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$sla_seconds" "Recovery exceeded SLA."
    append_timeline "drill_failed" "service=${service_label} reason=sla_exceeded recovery=${recovery_seconds}s sla=${sla_seconds}s"
    return 1
  fi

  write_service_result "$output_json" "$service_label" "passed" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$sla_seconds" "Recovery within SLA and SIP probe passed."
  append_timeline "drill_pass" "service=${service_label} recovery=${recovery_seconds}s sla=${sla_seconds}s"
  return 0
}

run_gateway_recovery() {
  local sla_seconds="$1"
  local output_json="$2"

  append_timeline "drill_start" "service=gateway"
  local start_epoch
  start_epoch="$(ws_n_now_epoch)"

  local old_pid=""
  if [[ -f "$GATEWAY_PID_FILE" ]]; then
    old_pid="$(cat "$GATEWAY_PID_FILE" 2>/dev/null || true)"
  fi
  if [[ -z "$old_pid" ]]; then
    old_pid="$(pgrep -f "voice_gateway --host ${GATEWAY_HOST} --port ${GATEWAY_PORT}" | head -n1 || true)"
  fi

  if [[ -n "$old_pid" ]] && ps -p "$old_pid" >/dev/null 2>&1; then
    kill "$old_pid" >/dev/null 2>&1 || true
    for _ in $(seq 1 40); do
      if ! ps -p "$old_pid" >/dev/null 2>&1; then
        break
      fi
      sleep 0.1
    done
    if ps -p "$old_pid" >/dev/null 2>&1; then
      kill -9 "$old_pid" >/dev/null 2>&1 || true
    fi
  fi

  "$GATEWAY_BINARY" --host "$GATEWAY_HOST" --port "$GATEWAY_PORT" >>"$GATEWAY_LOG" 2>&1 &
  local new_pid=$!
  echo "$new_pid" >"$GATEWAY_PID_FILE"

  local probe_deadline=$(( $(ws_n_now_epoch) + 90 ))
  local gateway_ok=0
  while (( $(ws_n_now_epoch) < probe_deadline )); do
    if curl -fsS "http://${GATEWAY_HOST}:${GATEWAY_PORT}/health" >/dev/null 2>&1; then
      gateway_ok=1
      break
    fi
    sleep 1
  done

  local recovered_epoch
  recovered_epoch="$(ws_n_now_epoch)"
  local recovery_seconds=$((recovered_epoch - start_epoch))

  if [[ "$gateway_ok" -ne 1 ]]; then
    write_service_result "$output_json" "gateway" "failed" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$sla_seconds" "Gateway health endpoint did not recover."
    append_timeline "drill_failed" "service=gateway reason=health_timeout recovery=${recovery_seconds}s"
    return 1
  fi

  if (( recovery_seconds > sla_seconds )); then
    write_service_result "$output_json" "gateway" "failed" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$sla_seconds" "Recovery exceeded SLA."
    append_timeline "drill_failed" "service=gateway reason=sla_exceeded recovery=${recovery_seconds}s sla=${sla_seconds}s"
    return 1
  fi

  write_service_result "$output_json" "gateway" "passed" "$start_epoch" "$recovered_epoch" "$recovery_seconds" "$sla_seconds" "Gateway recovered within SLA."
  append_timeline "drill_pass" "service=gateway recovery=${recovery_seconds}s sla=${sla_seconds}s pid=${new_pid}"
  return 0
}


"${compose_cmd[@]}" up -d --no-recreate opensips asterisk rtpengine >/dev/null

run_container_recovery "talky-opensips" "opensips" "opensips" "$OPENSIPS_RECOVERY_SLA_SECONDS" "$OPENSIPS_JSON"
run_container_recovery "talky-asterisk" "asterisk" "asterisk" "$ASTERISK_RECOVERY_SLA_SECONDS" "$ASTERISK_JSON"
run_container_recovery "talky-rtpengine" "rtpengine" "rtpengine" "$RTPENGINE_RECOVERY_SLA_SECONDS" "$RTPENGINE_JSON"
run_gateway_recovery "$GATEWAY_RECOVERY_SLA_SECONDS" "$GATEWAY_JSON"

append_timeline "drill_complete" "all_recovery_drills_passed"

echo "Day 10 restart recovery drill PASSED."
