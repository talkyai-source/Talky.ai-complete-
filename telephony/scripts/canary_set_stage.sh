#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"

STAGE_PERCENT="${1:-}"
ENV_FILE="${2:-$TELEPHONY_ROOT/deploy/docker/.env.telephony}"
FORCE_FLAG=0
NO_DOCKER=0

if [[ -z "$STAGE_PERCENT" ]]; then
  echo "Usage: $0 <0|5|20|25|50|100> [env_file] [--force] [--no-docker]"
  exit 1
fi

for flag in "${@:3}"; do
  case "$flag" in
    --force)
      FORCE_FLAG=1
      ;;
    --no-docker)
      NO_DOCKER=1
      ;;
    *)
      echo "[ERROR] Unknown flag: $flag"
      echo "Usage: $0 <0|5|20|25|50|100> [env_file] [--force] [--no-docker]"
      exit 1
      ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

case "$STAGE_PERCENT" in
  0|5|20|25|50|100) ;;
  *)
    echo "[ERROR] Invalid stage percent: $STAGE_PERCENT"
    echo "Allowed stages: 0, 5, 20, 25, 50, 100"
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

current_freeze="$(grep -E '^OPENSIPS_CANARY_FREEZE=' "$ENV_FILE" | tail -n1 | cut -d= -f2 || true)"
if [[ -z "$current_freeze" ]]; then
  current_freeze="0"
fi

if [[ "$current_freeze" == "1" && "$STAGE_PERCENT" != "0" && "$FORCE_FLAG" -ne 1 ]]; then
  echo "[ERROR] Canary is frozen (OPENSIPS_CANARY_FREEZE=1). Use --force to override."
  exit 1
fi

if [[ "$STAGE_PERCENT" == "0" ]]; then
  enabled="0"
else
  enabled="1"
fi

set_kv "OPENSIPS_CANARY_ENABLED" "$enabled" "$ENV_FILE"
set_kv "OPENSIPS_CANARY_PERCENT" "$STAGE_PERCENT" "$ENV_FILE"

if [[ "$NO_DOCKER" -eq 1 ]]; then
  echo "[OK] Canary stage persisted (no docker apply): ${STAGE_PERCENT}%"
  exit 0
fi

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

echo "[INFO] Applying canary stage ${STAGE_PERCENT}% (enabled=${enabled})"
"${compose_cmd[@]}" up -d opensips >/dev/null
sleep 2

running_services="$("${compose_cmd[@]}" ps --status running --services)"
if ! grep -qx "opensips" <<<"$running_services"; then
  echo "[ERROR] OpenSIPS is not running after stage update"
  "${compose_cmd[@]}" ps
  exit 1
fi

"${compose_cmd[@]}" exec -T opensips opensips -C -f /etc/opensips/opensips.cfg >/dev/null
echo "[OK] Canary stage applied and OpenSIPS config validated"
