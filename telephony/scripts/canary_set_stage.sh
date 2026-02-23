#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"

STAGE_PERCENT="${1:-}"
ENV_FILE="${2:-$TELEPHONY_ROOT/deploy/docker/.env.telephony}"
FORCE_FLAG="${3:-}"

if [[ -z "$STAGE_PERCENT" ]]; then
  echo "Usage: $0 <0|5|20|50|100> [env_file] [--force]"
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

case "$STAGE_PERCENT" in
  0|5|20|50|100) ;;
  *)
    echo "[ERROR] Invalid stage percent: $STAGE_PERCENT"
    echo "Allowed stages: 0, 5, 20, 50, 100"
    exit 1
    ;;
esac

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

current_freeze="$(grep -E '^KAMAILIO_CANARY_FREEZE=' "$ENV_FILE" | tail -n1 | cut -d= -f2 || true)"
if [[ -z "$current_freeze" ]]; then
  current_freeze="0"
fi

if [[ "$current_freeze" == "1" && "$STAGE_PERCENT" != "0" && "$FORCE_FLAG" != "--force" ]]; then
  echo "[ERROR] Canary is frozen (KAMAILIO_CANARY_FREEZE=1). Use --force to override."
  exit 1
fi

if [[ "$STAGE_PERCENT" == "0" ]]; then
  enabled="0"
else
  enabled="1"
fi

set_kv "KAMAILIO_CANARY_ENABLED" "$enabled" "$ENV_FILE"
set_kv "KAMAILIO_CANARY_PERCENT" "$STAGE_PERCENT" "$ENV_FILE"

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

echo "[INFO] Applying canary stage ${STAGE_PERCENT}% (enabled=${enabled})"
"${compose_cmd[@]}" up -d kamailio >/dev/null
sleep 2

running_services="$("${compose_cmd[@]}" ps --status running --services)"
if ! grep -qx "kamailio" <<<"$running_services"; then
  echo "[ERROR] Kamailio is not running after stage update"
  "${compose_cmd[@]}" ps
  exit 1
fi

"${compose_cmd[@]}" exec -T kamailio kamailio -c -f /etc/kamailio/kamailio.cfg >/dev/null
echo "[OK] Canary stage applied and Kamailio config validated"
