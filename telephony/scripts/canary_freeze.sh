#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"

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

set_kv "OPENSIPS_CANARY_FREEZE" "$freeze_value" "$ENV_FILE"

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

echo "[INFO] Applying canary ${ACTION} state"
"${compose_cmd[@]}" up -d opensips >/dev/null
sleep 2
"${compose_cmd[@]}" exec -T opensips opensips -C -f /etc/opensips/opensips.cfg >/dev/null
echo "[OK] Canary ${ACTION} applied"
