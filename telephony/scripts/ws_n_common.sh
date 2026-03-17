#!/usr/bin/env bash
# Shared helpers for WS-N failure injection drills.

ws_n_now_iso() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

ws_n_now_epoch() {
  date +%s
}

ws_n_read_env() {
  local key="$1"
  local default_value="$2"
  local env_file="$3"
  local value
  value="$(grep -E "^${key}=" "$env_file" | tail -n1 | cut -d= -f2- || true)"
  if [[ -n "$value" ]]; then
    printf "%s" "$value"
  else
    printf "%s" "$default_value"
  fi
}

ws_n_container_status() {
  local container="$1"
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || echo "missing"
}

ws_n_wait_for_status() {
  local container="$1"
  local timeout_seconds="$2"
  shift 2
  local expected_statuses=("$@")

  local start_epoch
  start_epoch="$(ws_n_now_epoch)"

  while true; do
    local status
    status="$(ws_n_container_status "$container")"
    for expected in "${expected_statuses[@]}"; do
      if [[ "$status" == "$expected" ]]; then
        return 0
      fi
    done

    local now_epoch
    now_epoch="$(ws_n_now_epoch)"
    if (( now_epoch - start_epoch >= timeout_seconds )); then
      echo "[ERROR] Timeout waiting for $container status in [${expected_statuses[*]}], current=$status"
      return 1
    fi
    sleep 1
  done
}

ws_n_capture_metrics() {
  local output_file="$1"
  local tmp_file="${output_file}.tmp"

  if curl -fsS --max-time 3 "http://127.0.0.1:8000/metrics" >"$tmp_file" 2>/dev/null; then
    printf "\ntelephony_ws_n_metrics_available 1\n" >>"$tmp_file"
    mv "$tmp_file" "$output_file"
    return 0
  fi

  cat >"$output_file" <<'EOF'
# Backend metrics endpoint unavailable during WS-N drill snapshot.
telephony_ws_n_metrics_available 0
EOF
  rm -f "$tmp_file"
  return 0
}

ws_n_append_timeline() {
  local output_file="$1"
  local event="$2"
  local detail="$3"
  printf "%s | %s | %s\n" "$(ws_n_now_iso)" "$event" "$detail" >>"$output_file"
}

ws_n_write_result_json() {
  local output_file="$1"
  local drill_id="$2"
  local status="$3"
  local start_epoch="$4"
  local end_epoch="$5"
  local outage_seconds="$6"
  local recovery_seconds="$7"
  local notes="$8"

  python3 - "$output_file" "$drill_id" "$status" "$start_epoch" "$end_epoch" "$outage_seconds" "$recovery_seconds" "$notes" <<'PY'
import json
import sys
from pathlib import Path

output_file = Path(sys.argv[1])
payload = {
    "drill_id": sys.argv[2],
    "status": sys.argv[3],
    "start_epoch": int(float(sys.argv[4])),
    "end_epoch": int(float(sys.argv[5])),
    "outage_seconds": int(float(sys.argv[6])),
    "recovery_seconds": int(float(sys.argv[7])),
    "notes": sys.argv[8],
}
output_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

