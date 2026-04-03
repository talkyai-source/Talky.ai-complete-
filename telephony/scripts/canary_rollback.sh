#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
DISPATCHER_FILE="$TELEPHONY_ROOT/opensips/conf/dispatcher.list"

MODE="${1:-full}"
ENV_FILE="${2:-$TELEPHONY_ROOT/deploy/docker/.env.telephony}"

if [[ "$MODE" != "runtime" && "$MODE" != "durable" && "$MODE" != "full" ]]; then
  echo "Usage: $0 [runtime|durable|full] [env_file]"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

if [[ ! -f "$DISPATCHER_FILE" ]]; then
  echo "[ERROR] Missing dispatcher file: $DISPATCHER_FILE"
  exit 1
fi

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

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
canary_uri="$(awk '$1=="2"{print $2; exit}' "$DISPATCHER_FILE")"

if [[ -z "$canary_uri" ]]; then
  echo "[ERROR] No canary URI (dispatcher set 2) found in $DISPATCHER_FILE"
  exit 1
fi

runtime_rollback() {
  echo "[INFO] Runtime rollback: disabling canary destination state in dispatcher"
  "${compose_cmd[@]}" up -d opensips >/dev/null
  if ! "${compose_cmd[@]}" exec -T opensips sh -lc "opensips-cli -x mi ds_list >/dev/null 2>&1"; then
    echo "[WARN] Dispatcher MI commands are unavailable in active runtime; skipping ds_set_state rollback step"
    return 0
  fi
  if ! "${compose_cmd[@]}" exec -T opensips sh -lc "opensips-cli -x mi ds_set_state i 2 '$canary_uri' >/dev/null"; then
    echo "[WARN] opensips-cli path failed, attempting opensipsctl fifo fallback"
    "${compose_cmd[@]}" exec -T opensips sh -lc "opensipsctl fifo ds_set_state i 2 '$canary_uri' >/dev/null" || {
      echo "[WARN] Runtime dispatcher state transition unavailable; continuing with durable rollback controls"
      return 0
    }
  fi
  echo "[OK] Runtime rollback command applied for set=2 uri=$canary_uri"
}

durable_rollback() {
  echo "[INFO] Durable rollback: forcing canary percent to 0 and disabling canary"
  set_kv "OPENSIPS_CANARY_ENABLED" "0" "$ENV_FILE"
  set_kv "OPENSIPS_CANARY_PERCENT" "0" "$ENV_FILE"
  set_kv "OPENSIPS_CANARY_FREEZE" "0" "$ENV_FILE"
  "${compose_cmd[@]}" up -d opensips >/dev/null
  sleep 2
  "${compose_cmd[@]}" exec -T opensips opensips -C -f /etc/opensips/opensips.cfg >/dev/null
  echo "[OK] Durable rollback applied and config validated"
}

case "$MODE" in
  runtime)
    runtime_rollback
    ;;
  durable)
    durable_rollback
    ;;
  full)
    runtime_rollback
    durable_rollback
    ;;
esac
