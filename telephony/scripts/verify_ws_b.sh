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

echo "[1/9] Ensuring Kamailio TLS certs exist..."
"$SCRIPT_DIR/generate_kamailio_tls_certs.sh"

echo "[2/9] Running WS-A baseline verification..."
"$SCRIPT_DIR/verify_ws_a.sh" "$ENV_FILE"

echo "[3/9] Re-applying stack for WS-B config mounts..."
"${compose_cmd[@]}" up -d
sleep 5

echo "[4/9] Kamailio syntax check with WS-B modules..."
"${compose_cmd[@]}" exec -T kamailio kamailio -c -f /etc/kamailio/kamailio.cfg >/dev/null
echo "[OK] Kamailio syntax valid"

echo "[5/9] Verifying Kamailio security directives are present..."
for marker in "loadmodule \"permissions.so\"" "loadmodule \"pike.so\"" "loadmodule \"ratelimit.so\"" "loadmodule \"tls.so\"" "allow_source_address(\"1\")" "pike_check_req()" "rl_check()"; do
  if ! grep -Fq "$marker" "$TELEPHONY_ROOT/kamailio/conf/kamailio.cfg"; then
    echo "[ERROR] Missing security directive in kamailio.cfg: $marker"
    exit 1
  fi
done
echo "[OK] Kamailio security directives detected"

echo "[6/9] Verifying TLS listener is up..."
ss -ltn | grep -q ":${KAMAILIO_TLS_PORT:-15061} "
echo "[OK] TLS listener active on port ${KAMAILIO_TLS_PORT:-15061}"

echo "[7/9] Running TLS SIP OPTIONS probe..."
"$SCRIPT_DIR/sip_options_probe_tls.sh" "127.0.0.1" "${KAMAILIO_TLS_PORT:-15061}" "5"
echo "[OK] TLS SIP probe passed"

echo "[8/9] Verifying FreeSWITCH ESL bind and ACL..."
ss -ltn | grep ':8021' | grep -q '127.0.0.1:8021'
if ! grep -Fq 'apply-inbound-acl" value="loopback.auto"' "$TELEPHONY_ROOT/freeswitch/conf/autoload_configs/event_socket.conf.xml"; then
  echo "[ERROR] FreeSWITCH ESL ACL is not loopback.auto"
  exit 1
fi
echo "[OK] FreeSWITCH ESL bound to loopback with loopback ACL"

echo "[9/9] WS-B verification complete"
echo
echo "WS-B verification PASSED."
