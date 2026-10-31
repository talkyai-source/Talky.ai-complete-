#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
DISPATCHER_FILE="$TELEPHONY_ROOT/opensips/conf/dispatcher.list"
LOCK_SCRIPT="$SCRIPT_DIR/canary_lock.sh"

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

# Serialize the entire durable/runtime rollback with activation and freeze.
source "$LOCK_SCRIPT"
acquire_canary_state_lock "$ENV_FILE"
trap release_canary_state_lock EXIT

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
  if ! "${compose_cmd[@]}" ps --status running --services | grep -qx opensips; then
    echo "[ERROR] OpenSIPS is not running; runtime-only rollback cannot be verified"
    return 1
  fi
  if ! "${compose_cmd[@]}" exec -T opensips sh -lc "opensips-cli -x mi ds_list >/dev/null 2>&1"; then
    echo "[ERROR] Dispatcher MI commands are unavailable; runtime-only rollback failed"
    return 1
  fi
  if ! "${compose_cmd[@]}" exec -T opensips sh -lc "opensips-cli -x mi ds_set_state i 2 '$canary_uri' >/dev/null"; then
    echo "[WARN] opensips-cli path failed, attempting opensipsctl fifo fallback"
    "${compose_cmd[@]}" exec -T opensips sh -lc "opensipsctl fifo ds_set_state i 2 '$canary_uri' >/dev/null" || {
      echo "[ERROR] Runtime dispatcher state transition unavailable"
      return 1
    }
  fi
  echo "[OK] Runtime rollback command applied for set=2 uri=$canary_uri"
}

durable_rollback() {
  echo "[INFO] Durable rollback: forcing canary percent to 0 and disabling canary"
  set_kv "OPENSIPS_CANARY_ENABLED" "0" "$ENV_FILE"
  set_kv "OPENSIPS_CANARY_PERCENT" "0" "$ENV_FILE"
  # Rollback is both disabled and frozen so an unrelated follow-up command
  # cannot accidentally re-enable inbound traffic.
  set_kv "OPENSIPS_CANARY_FREEZE" "1" "$ENV_FILE"
  # Stop the old process before recreation. If the new config cannot start,
  # ingress remains closed instead of continuing with stale active settings.
  "${compose_cmd[@]}" stop opensips >/dev/null
  "${compose_cmd[@]}" up -d opensips >/dev/null
  sleep 2
  "${compose_cmd[@]}" exec -T opensips opensips -C -f /etc/opensips/opensips.cfg >/dev/null
  echo "[OK] Durable rollback applied and config validated"
}

case "$MODE" in
  runtime)
    echo "[WARN] Runtime-only rollback is unsafe with dispatcher health probing; enforcing durable freeze first"
    durable_rollback
    if ! runtime_rollback; then
      echo "[WARN] Runtime dispatcher state could not be changed, but durable ingress gate is disabled and frozen"
    fi
    ;;
  durable)
    durable_rollback
    ;;
  full)
    durable_rollback
    if ! runtime_rollback; then
      echo "[WARN] Runtime dispatcher state could not be changed, but durable ingress gate is disabled and frozen"
    fi
    ;;
esac
