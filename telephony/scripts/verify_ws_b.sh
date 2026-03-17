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
TLS_PORT="${OPENSIPS_TLS_PORT:-15061}"
ASTERISK_PORT="${ASTERISK_SIP_PORT:-5070}"

echo "[1/9] Ensuring OpenSIPS TLS certs exist..."
"$SCRIPT_DIR/generate_opensips_tls_certs.sh"

echo "[2/9] Running WS-A baseline verification..."
"$SCRIPT_DIR/verify_ws_a.sh" "$ENV_FILE"

echo "[3/9] Re-applying stack for WS-B config mounts..."
"${compose_cmd[@]}" up -d
sleep 5

echo "[4/9] OpenSIPS syntax check with WS-B modules..."
"${compose_cmd[@]}" exec -T opensips opensips -C -f /etc/opensips/opensips.cfg >/dev/null
echo "[OK] OpenSIPS syntax valid"

echo "[5/9] Verifying OpenSIPS security directives are present..."
for marker in \
  "loadmodule \"proto_tls.so\"" \
  "loadmodule \"tls_mgm.so\"" \
  "loadmodule \"tls_openssl.so\"" \
  "loadmodule \"sipmsgops.so\"" \
  "loadmodule \"pike.so\"" \
  "loadmodule \"ratelimit.so\"" \
  "modparam(\"tls_mgm\", \"server_domain\", \"default\")" \
  "modparam(\"tls_mgm\", \"match_ip_address\", \"[default]*\")" \
  "modparam(\"tls_mgm\", \"tls_method\", \"[default]TLSv1_2-TLSv1_3\")" \
  "modparam(\"tls_mgm\", \"certificate\", \"[default]/etc/opensips/certs/server.crt\")" \
  "modparam(\"tls_mgm\", \"private_key\", \"[default]/etc/opensips/certs/server.key\")" \
  '$si != "127.0.0.1"' \
  "pike_check_req()" \
  "rl_check("; do
  if ! grep -Fq "$marker" "$TELEPHONY_ROOT/opensips/conf/opensips.cfg"; then
    echo "[ERROR] Missing security directive in opensips.cfg: $marker"
    exit 1
  fi
done
echo "[OK] OpenSIPS security directives detected"

echo "[6/9] Verifying TLS listener is up..."
ss -ltn | grep -q ":${TLS_PORT} "
echo "[OK] TLS listener active on port ${TLS_PORT}"

echo "[7/9] Running TLS SIP OPTIONS probe..."
"$SCRIPT_DIR/sip_options_probe_tls.sh" "127.0.0.1" "${TLS_PORT}" "5"
echo "[OK] TLS SIP probe passed"

echo "[8/9] Verifying Asterisk PJSIP hardening baseline..."
if ! grep -Fq "noload => chan_sip.so" "$TELEPHONY_ROOT/asterisk/conf/modules.conf"; then
  echo "[ERROR] Missing Asterisk WS-B baseline marker: noload => chan_sip.so"
  exit 1
fi
for marker in \
  "direct_media=no" \
  "disallow=all" \
  "allow=ulaw" \
  "type=identify" \
  "match=127.0.0.1" \
  "outbound_proxy=sip:127.0.0.1:15060\\;lr"; do
  if ! grep -Fq "$marker" "$TELEPHONY_ROOT/asterisk/conf/pjsip.conf"; then
    echo "[ERROR] Missing Asterisk WS-B baseline marker in pjsip.conf: $marker"
    exit 1
  fi
done
ss -lun | grep -q ":${ASTERISK_PORT}"
echo "[OK] Asterisk PJSIP baseline and listener verified"

echo "[9/9] WS-B verification complete"
echo
echo "WS-B verification PASSED."
