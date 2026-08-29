#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
LOCK_SCRIPT="$SCRIPT_DIR/canary_lock.sh"

ACTION="${1:-}"
ENV_FILE="${2:-$TELEPHONY_ROOT/deploy/docker/.env.telephony}"

if [[ "$ACTION" != "freeze" && "$ACTION" != "unfreeze" ]]; then
  echo "Usage: $0 <freeze|unfreeze> [env_file]"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
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

if [[ "$ACTION" == "freeze" ]]; then
  freeze_value="1"
else
  freeze_value="0"
fi

# Freeze/unfreeze touches the same durable gate as activation and rollback.
source "$LOCK_SCRIPT"
acquire_canary_state_lock "$ENV_FILE"
trap release_canary_state_lock EXIT

set_kv "OPENSIPS_CANARY_FREEZE" "$freeze_value" "$ENV_FILE"
# Freeze is an ingress kill switch, and unfreeze only releases the progression
# lock. Neither command activates calls: activation remains a separate,
# asserted `canary_set_stage.sh 100` operation.
set_kv "OPENSIPS_CANARY_ENABLED" "0" "$ENV_FILE"
set_kv "OPENSIPS_CANARY_PERCENT" "0" "$ENV_FILE"

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

echo "[INFO] Applying canary ${ACTION} state"
if [[ "$ACTION" == "freeze" ]]; then
  # Stop the old process before recreation so a failed apply cannot leave the
  # previously active, in-memory policy serving new calls.
  "${compose_cmd[@]}" stop opensips >/dev/null
fi
"${compose_cmd[@]}" up -d opensips >/dev/null
sleep 2
"${compose_cmd[@]}" exec -T opensips opensips -C -f /etc/opensips/opensips.cfg >/dev/null
echo "[OK] Canary ${ACTION} applied"
