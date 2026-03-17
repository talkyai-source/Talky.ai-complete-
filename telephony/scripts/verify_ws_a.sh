#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  echo "Copy telephony/deploy/docker/.env.telephony.example -> $ENV_FILE and adjust values."
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
SIP_PORT="${OPENSIPS_SIP_PORT:-15060}"
ASTERISK_PORT="${ASTERISK_SIP_PORT:-5070}"

echo "[1/7] Validating docker compose syntax..."
"${compose_cmd[@]}" config -q
echo "[OK] compose config is valid"

echo "[2/7] Starting WS-A services..."
"${compose_cmd[@]}" down --remove-orphans >/dev/null 2>&1 || true
docker rm -f talky-asterisk talky-freeswitch talky-rtpengine talky-opensips >/dev/null 2>&1 || true
"${compose_cmd[@]}" up -d

echo "[3/7] Waiting for services to stabilize..."
sleep 12

echo "[4/7] Checking required services are running..."
running_services="$("${compose_cmd[@]}" ps --status running --services)"
for svc in asterisk rtpengine opensips; do
  if ! grep -qx "$svc" <<<"$running_services"; then
    echo "[ERROR] Service is not running: $svc"
    "${compose_cmd[@]}" ps
    exit 1
  fi
done
echo "[OK] asterisk, rtpengine, opensips running"

echo "[5/7] OpenSIPS config syntax check..."
"${compose_cmd[@]}" exec -T opensips opensips -C -f /etc/opensips/opensips.cfg >/dev/null
echo "[OK] opensips config syntax valid"

echo "[6/7] Asterisk control and RTPengine checks..."
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "core show uptime seconds" >/dev/null
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "pjsip show transports" | grep -q "${ASTERISK_PORT}"
"${compose_cmd[@]}" exec -T rtpengine sh -lc "ss -lun | grep -q ':2223'"
if [[ ! -f "$TELEPHONY_ROOT/freeswitch/conf/autoload_configs/event_socket.conf.xml" ]]; then
  echo "[ERROR] FreeSWITCH backup config missing"
  exit 1
fi
echo "[OK] Asterisk primary control reachable; FreeSWITCH backup configs present; rtpengine reachable"

echo "[7/7] SIP OPTIONS synthetic probe..."
python3 "$SCRIPT_DIR/sip_options_probe.py" \
  --host 127.0.0.1 \
  --port "$SIP_PORT" \
  --timeout 3.0
echo "[OK] SIP OPTIONS probe succeeded"

echo
echo "WS-A verification PASSED."
