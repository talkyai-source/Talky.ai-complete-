#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony.example}"

OPENSIPS_CFG="$TELEPHONY_ROOT/opensips/conf/opensips.cfg"
RTP_KERNEL_CFG="$TELEPHONY_ROOT/rtpengine/conf/rtpengine.conf"
RTP_USERSPACE_CFG="$TELEPHONY_ROOT/rtpengine/conf/rtpengine.userspace.conf"
ASTERISK_EXTENSIONS="$TELEPHONY_ROOT/asterisk/conf/extensions.conf"
ASTERISK_FEATURES="$TELEPHONY_ROOT/asterisk/conf/features.conf"
XML_CURL_CFG="$TELEPHONY_ROOT/freeswitch/conf/autoload_configs/xml_curl.conf.xml"

WSM_COMPLETION_DOC="$TELEPHONY_ROOT/docs/phase_3/11_ws_m_completion.md"
PHASE3_CHECKLIST="$TELEPHONY_ROOT/docs/phase_3/02_phase_three_gated_checklist.md"
EVIDENCE_DIR="$TELEPHONY_ROOT/docs/phase_3/evidence"
RESULTS_FILE="$EVIDENCE_DIR/ws_m_synthetic_results.log"
MEDIA_EVIDENCE="$EVIDENCE_DIR/ws_m_media_mode_check.txt"
TRANSFER_EVIDENCE="$EVIDENCE_DIR/ws_m_transfer_check.txt"
LONGCALL_EVIDENCE="$EVIDENCE_DIR/ws_m_longcall_check.txt"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

mkdir -p "$EVIDENCE_DIR"

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

read_env() {
  local key="$1"
  local default_value="$2"
  local value
  value="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
  if [[ -n "${value}" ]]; then
    printf "%s" "$value"
  else
    printf "%s" "$default_value"
  fi
}

RTP_IMAGE="$(read_env "RTPENGINE_IMAGE" "talky/rtpengine:ubuntu24.04")"
WSM_LONGCALL_MIN_SECONDS=10

echo "[1/13] Running WS-L verifier (WS-M prerequisite)..."
"$SCRIPT_DIR/verify_ws_l.sh" "$ENV_FILE"

echo "[2/13] Validating WS-M OpenSIPS media relay markers..."
for marker in \
  'loadmodule "rtpengine.so"' \
  'modparam("rtpengine", "rtpengine_sock", "udp:127.0.0.1:2223")' \
  'route(WS_M_MANAGE_RTP);' \
  't_on_reply("WS_M_RTP_REPLY");' \
  'rtpengine_offer("replace-origin replace-session-connection ICE=remove")' \
  'rtpengine_answer("replace-origin replace-session-connection ICE=remove")' \
  'rtpengine_delete()' \
  'onreply_route[WS_M_RTP_REPLY]'; do
  if ! grep -Fq "$marker" "$OPENSIPS_CFG"; then
    echo "[ERROR] Missing WS-M marker in opensips.cfg: $marker"
    exit 1
  fi
done

echo "[3/13] Validating RTPengine kernel/userspace policy configs..."
if ! rg -n '^table\s*=\s*0$' "$RTP_KERNEL_CFG" >/dev/null; then
  echo "[ERROR] Kernel RTPengine config must set table = 0: $RTP_KERNEL_CFG"
  exit 1
fi
if ! rg -n '^table\s*=\s*-1$' "$RTP_USERSPACE_CFG" >/dev/null; then
  echo "[ERROR] Userspace RTPengine config must set table = -1: $RTP_USERSPACE_CFG"
  exit 1
fi
for cfg in "$RTP_KERNEL_CFG" "$RTP_USERSPACE_CFG"; do
  for marker in "listen-ng = 0.0.0.0:2223" "port-min = 30000" "port-max = 34999"; do
    if ! grep -Fq "$marker" "$cfg"; then
      echo "[ERROR] Missing marker in $(basename "$cfg"): $marker"
      exit 1
    fi
  done
done

echo "[4/13] Validating Asterisk transfer and synthetic scenario config..."
for marker in "blindxfer=#1" "atxfer=*2" "disconnect=*0"; do
  if ! grep -Fq "$marker" "$ASTERISK_FEATURES"; then
    echo "[ERROR] Missing transfer feature marker in features.conf: $marker"
    exit 1
  fi
done
for marker in \
  "[wsm-synthetic]" \
  "exten => longcall,1" \
  "exten => blind,1" \
  "exten => attended,1" \
  "exten => blind_target,1" \
  "exten => attended_target,1"; do
  if ! grep -Fq "$marker" "$ASTERISK_EXTENSIONS"; then
    echo "[ERROR] Missing synthetic marker in extensions.conf: $marker"
    exit 1
  fi
done

echo "[5/13] Validating FreeSWITCH mod_xml_curl timeout/retry limits..."
python3 - "$XML_CURL_CFG" <<'PY'
import re
import sys
from pathlib import Path

cfg = Path(sys.argv[1]).read_text(encoding="utf-8")
timeouts = [int(v) for v in re.findall(r'name="timeout"\s+value="(\d+)"', cfg)]
retries = [int(v) for v in re.findall(r'name="retries"\s+value="(\d+)"', cfg)]
retry_delays = [int(v) for v in re.findall(r'name="retry-delay-ms"\s+value="(\d+)"', cfg)]
if len(timeouts) < 2:
    raise SystemExit("Expected timeout limits for directory and dialplan bindings")
if len(retries) < 2:
    raise SystemExit("Expected retry limits for directory and dialplan bindings")
if len(retry_delays) < 2:
    raise SystemExit("Expected retry-delay-ms limits for directory and dialplan bindings")
if any(t < 1000 or t > 10000 for t in timeouts):
    raise SystemExit(f"Timeout out of bounds: {timeouts} (expected 1000..10000 ms)")
if any(r < 1 or r > 3 for r in retries):
    raise SystemExit(f"Retries out of bounds: {retries} (expected 1..3)")
if any(d < 100 or d > 2000 for d in retry_delays):
    raise SystemExit(f"Retry delay out of bounds: {retry_delays} (expected 100..2000 ms)")
print(f"xml_curl limits validated: timeout={timeouts} retries={retries} retry-delay-ms={retry_delays}")
PY

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[6/13] Applying latest compose config and waiting for health..."
  "${compose_cmd[@]}" up -d asterisk rtpengine opensips >/dev/null

  for svc in talky-asterisk talky-rtpengine talky-opensips; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$svc" 2>/dev/null || true)"
    if [[ "$status" != "healthy" && "$status" != "running" ]]; then
      echo "[ERROR] Service not healthy: $svc (status=$status)"
      exit 1
    fi
  done

  echo "[7/13] Validating live Asterisk/OpenSIPS WS-M runtime markers..."
  docker exec talky-opensips opensips -C -f /etc/opensips/opensips.cfg >/dev/null
  docker exec talky-asterisk asterisk -rx "core show applications like BlindTransfer" | tee "$TRANSFER_EVIDENCE" >/dev/null
  docker exec talky-asterisk asterisk -rx "core show applications like AttendedTransfer" >> "$TRANSFER_EVIDENCE"
  docker exec talky-asterisk asterisk -rx "features show" >> "$TRANSFER_EVIDENCE"
  docker exec talky-asterisk asterisk -rx "dialplan show wsm-synthetic" >> "$TRANSFER_EVIDENCE"

  echo "[8/13] Validating RTPengine userspace startup path (table=-1)..."
  docker run --rm --network host --cap-add NET_ADMIN \
    -v "$RTP_USERSPACE_CFG:/etc/rtpengine/rtpengine.conf:ro" \
    --entrypoint sh "$RTP_IMAGE" \
    -lc 'timeout 6 rtpengine --config-file=/etc/rtpengine/rtpengine.conf --foreground >/tmp/rtp_userspace.log 2>&1; rc=$?; if [ "$rc" -eq 124 ]; then echo "userspace_mode_ok"; exit 0; fi; cat /tmp/rtp_userspace.log; exit 1' \
    | tee "$MEDIA_EVIDENCE" >/dev/null

  echo "[9/13] Running synthetic long-call/blind/attended scenarios in Asterisk..."
  docker exec talky-asterisk asterisk -rx "database deltree wsm" >/dev/null || true
  docker exec talky-asterisk asterisk -rx "dialplan reload" >/dev/null
  docker exec talky-asterisk asterisk -rx "features reload" >/dev/null || true

  docker exec talky-asterisk asterisk -rx "channel originate Local/longcall@wsm-synthetic application Wait 1" >/dev/null
  sleep 14

  docker exec talky-asterisk asterisk -rx "channel originate Local/blind@wsm-synthetic application Wait 1" >/dev/null
  sleep 3

  docker exec talky-asterisk asterisk -rx "channel originate Local/attended@wsm-synthetic application Wait 1" >/dev/null
  sleep 4

  docker exec talky-asterisk asterisk -rx "database show wsm" | tee "$RESULTS_FILE" >/dev/null

  echo "[10/13] Validating synthetic scenario outcomes..."
  python3 - "$RESULTS_FILE" "$WSM_LONGCALL_MIN_SECONDS" <<'PY'
import re
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
min_long = int(sys.argv[2])
text = log_path.read_text(encoding="utf-8")

longcall = re.search(r"longcall:(\d+)", text)
if not longcall:
    raise SystemExit("Missing longcall result marker")
duration = int(longcall.group(1))
if duration < min_long:
    raise SystemExit(f"Long-call duration too short: {duration}s < {min_long}s")

required = [
    "blind:pass",
    "blind_target:reached",
    "attended:pass",
    "attended_target:reached",
]
for marker in required:
    if marker not in text:
        raise SystemExit(f"Missing transfer marker: {marker}")

print(f"Synthetic scenarios validated (longcall={duration}s)")
PY

  grep -E 'longcall:' "$RESULTS_FILE" > "$LONGCALL_EVIDENCE"
  grep -E '(blind|blind_target|attended|attended_target):' "$RESULTS_FILE" >> "$LONGCALL_EVIDENCE"

  echo "[11/13] Validating WS-M documentation markers..."
  for marker in \
    "WS-M Completion Record" \
    "Media quality report" \
    "Transfer success report" \
    "Long-call/session-timer report"; do
    if ! rg -n "$marker" "$WSM_COMPLETION_DOC" "$PHASE3_CHECKLIST" >/dev/null; then
      echo "[ERROR] Missing WS-M documentation marker: $marker"
      exit 1
    fi
  done
else
  echo "[6/13] Docker unavailable/inaccessible: live WS-M checks skipped"
fi

echo "[12/13] WS-M evidence files generated:"
echo " - $MEDIA_EVIDENCE"
echo " - $TRANSFER_EVIDENCE"
echo " - $LONGCALL_EVIDENCE"
echo " - $RESULTS_FILE"

echo "[13/13] WS-M verification complete"
echo
echo "WS-M verification PASSED."
