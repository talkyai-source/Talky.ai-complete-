#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony.example}"
DOC_ROOT="$TELEPHONY_ROOT/docs/phase_3"
EVIDENCE_DIR="$DOC_ROOT/evidence/day5"
mkdir -p "$EVIDENCE_DIR"

VERIFIER_OUT="$EVIDENCE_DIR/day5_verifier_output.txt"
exec > >(tee "$VERIFIER_OUT") 2>&1

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

SIP_PORT="${OPENSIPS_SIP_PORT:-15060}"
EXTENSION="${DAY5_TEST_EXTENSION:-750}"
ARI_HOST="${ASTERISK_ARI_HOST:-127.0.0.1}"
ARI_PORT="${ASTERISK_ARI_PORT:-8088}"
ARI_USERNAME="${ASTERISK_ARI_USERNAME:-day5}"
ARI_PASSWORD="${ASTERISK_ARI_PASSWORD:-day5_local_only_change_me}"
ARI_APP="${ASTERISK_ARI_APP:-talky_day5}"

GATEWAY_DIR="$REPO_ROOT/services/voice-gateway-cpp"
GATEWAY_BUILD_DIR="$GATEWAY_DIR/build"
GATEWAY_HOST="127.0.0.1"
GATEWAY_HTTP_PORT="18080"
GATEWAY_BASE_URL="http://${GATEWAY_HOST}:${GATEWAY_HTTP_PORT}"

PYTHON_BIN="${DAY5_PYTHON_BIN:-$REPO_ROOT/backend/venv/bin/python}"

CALL_JSON="$EVIDENCE_DIR/day5_20_calls_result.json"
ARI_LOG="$EVIDENCE_DIR/day5_ari_event_trace.log"
ASTERISK_LOG="$EVIDENCE_DIR/day5_asterisk_cli.log"
GATEWAY_STATS_JSON="$EVIDENCE_DIR/day5_gateway_stats.json"
PCAP_SUMMARY_TXT="$EVIDENCE_DIR/day5_pcap_summary.txt"
EVIDENCE_DOC="$DOC_ROOT/day5_asterisk_cpp_echo_evidence.md"

GW_PID=""
ARI_PID=""

cleanup() {
  set +e
  if [[ -n "$ARI_PID" ]] && ps -p "$ARI_PID" >/dev/null 2>&1; then
    kill "$ARI_PID" >/dev/null 2>&1 || true
    wait "$ARI_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$GW_PID" ]] && ps -p "$GW_PID" >/dev/null 2>&1; then
    kill "$GW_PID" >/dev/null 2>&1 || true
    wait "$GW_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[1/13] Verifying Python runtime for Day 5 controller..."
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] Python interpreter not found: $PYTHON_BIN"
  echo "Set DAY5_PYTHON_BIN to a Python with requests + websockets installed."
  exit 1
fi
"$PYTHON_BIN" - <<'PY'
import importlib.util
mods = ["requests", "websockets"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
    raise SystemExit(f"Missing required modules: {', '.join(missing)}")
print("python dependency check: ok")
PY

echo "[2/13] Ensuring required telephony containers are running..."
"${compose_cmd[@]}" up -d --no-recreate asterisk rtpengine opensips

echo "[3/13] Reloading Asterisk to load ARI/http config..."
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "core show uptime seconds" >/dev/null
docker exec -i talky-asterisk sh -lc "cat > /etc/asterisk/http.conf" < "$TELEPHONY_ROOT/asterisk/conf/http.conf"
docker exec -i talky-asterisk sh -lc "cat > /etc/asterisk/ari.conf" < "$TELEPHONY_ROOT/asterisk/conf/ari.conf"
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "core reload" >/dev/null
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_http_websocket.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_ari.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_ari_channels.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "http show status" | grep -qi "Server Enabled and Bound to"

echo "[4/13] Verifying ARI API reachability..."
"$PYTHON_BIN" - <<PY
import requests
url = "http://${ARI_HOST}:${ARI_PORT}/ari/asterisk/info"
resp = requests.get(url, params={"api_key": "${ARI_USERNAME}:${ARI_PASSWORD}"}, timeout=5)
if resp.status_code != 200:
    raise SystemExit(f"ARI ping failed: {resp.status_code} {resp.text[:300]}")
print("ari ping: ok")
PY

echo "[5/13] Building C++ voice gateway and running unit tests..."
cmake -S "$GATEWAY_DIR" -B "$GATEWAY_BUILD_DIR" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$GATEWAY_BUILD_DIR" -- -j"$(nproc)" >/dev/null
ctest --test-dir "$GATEWAY_BUILD_DIR" --output-on-failure >/dev/null

echo "[6/13] Starting C++ voice gateway runtime..."
"$GATEWAY_BUILD_DIR/voice_gateway" --host "$GATEWAY_HOST" --port "$GATEWAY_HTTP_PORT" >"$EVIDENCE_DIR/day5_gateway_runtime.log" 2>&1 &
GW_PID=$!
for _ in $(seq 1 80); do
  if curl -fsS "$GATEWAY_BASE_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
curl -fsS "$GATEWAY_BASE_URL/health" >/dev/null

echo "[7/13] Starting Day 5 ARI external media controller..."
start_ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"$PYTHON_BIN" "$SCRIPT_DIR/day5_ari_external_media_controller.py" \
  --ari-host "$ARI_HOST" \
  --ari-port "$ARI_PORT" \
  --ari-username "$ARI_USERNAME" \
  --ari-password "$ARI_PASSWORD" \
  --app-name "$ARI_APP" \
  --gateway-base-url "$GATEWAY_BASE_URL" \
  --gateway-rtp-ip "127.0.0.1" \
  --gateway-rtp-port-start 32000 \
  --gateway-rtp-port-end 32999 \
  --max-completed-calls 20 \
  --idle-timeout-seconds 45 >"$ARI_LOG" 2>&1 &
ARI_PID=$!

for _ in $(seq 1 80); do
  if grep -q '"event": "controller_started"' "$ARI_LOG" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if ! grep -q '"event": "controller_started"' "$ARI_LOG" 2>/dev/null; then
  echo "[ERROR] Controller did not start correctly."
  tail -n 80 "$ARI_LOG" || true
  exit 1
fi

echo "[8/13] Running 20 SIP+RTP echo calls through OpenSIPS -> Asterisk -> C++ -> Asterisk..."
"$PYTHON_BIN" "$SCRIPT_DIR/day5_sip_rtp_echo_probe.py" \
  --host 127.0.0.1 \
  --port "$SIP_PORT" \
  --extension "$EXTENSION" \
  --calls 20 \
  --bind-ip 127.0.0.1 \
  --timeout 5.0 \
  --hold-ms 700 \
  --evidence-file "$CALL_JSON"

echo "[9/13] Validating call results and waiting for controller completion..."
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path
p = Path("$CALL_JSON")
data = json.loads(p.read_text(encoding="utf-8"))
if data.get("calls") != 20:
    raise SystemExit(f"Expected 20 calls, got {data.get('calls')}")
if data.get("failed") != 0:
    raise SystemExit(f"Expected 0 failed calls, got {data.get('failed')}")
if data.get("passed") != 20:
    raise SystemExit(f"Expected 20 passed calls, got {data.get('passed')}")
for row in data.get("results", []):
    if not row.get("success"):
        raise SystemExit(f"Call failed: {row}")
    if int(row.get("received_rtp_packets", 0)) <= 0:
        raise SystemExit(f"No echo RTP on call: {row}")
print("call result validation: ok")
PY

deadline=$((SECONDS + 90))
while ps -p "$ARI_PID" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "[ERROR] Controller did not finish within timeout."
    tail -n 120 "$ARI_LOG" || true
    exit 1
  fi
  sleep 1
done
wait "$ARI_PID"
ari_exit=$?
ARI_PID=""
if [[ "$ari_exit" -ne 0 ]]; then
  echo "[ERROR] Controller exited with code $ari_exit"
  tail -n 160 "$ARI_LOG" || true
  exit 1
fi

echo "[10/13] Capturing gateway stats and enforcing no active sessions..."
curl -fsS "$GATEWAY_BASE_URL/stats" > "$GATEWAY_STATS_JSON"
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path
stats = json.loads(Path("$GATEWAY_STATS_JSON").read_text(encoding="utf-8"))
active = int(stats.get("active_sessions", -1))
if active != 0:
    raise SystemExit(f"Expected active_sessions=0, got {active}")
print("gateway stats validation: ok")
PY

echo "[11/13] Validating ARI cleanup (no external channels leaked)..."
"$PYTHON_BIN" - <<PY
import requests
api_key = "${ARI_USERNAME}:${ARI_PASSWORD}"
base = "http://${ARI_HOST}:${ARI_PORT}/ari"
channels = requests.get(f"{base}/channels", params={"api_key": api_key}, timeout=5)
if channels.status_code != 200:
    raise SystemExit(f"channels query failed: {channels.status_code}")
external = [c for c in channels.json() if str(c.get("name", "")).startswith("UnicastRTP/")]
if external:
    raise SystemExit(f"Leaked external channels: {external}")
print("ari cleanup validation: ok")
PY

echo "[12/13] Collecting Asterisk logs and RTP summary..."
docker logs --since "$start_ts" talky-asterisk > "$ASTERISK_LOG" 2>&1 || true
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path
calls = json.loads(Path("$CALL_JSON").read_text(encoding="utf-8"))
rows = calls["results"]
total_sent = sum(int(r.get("sent_rtp_packets", 0)) for r in rows)
total_recv = sum(int(r.get("received_rtp_packets", 0)) for r in rows)
lines = [
    "Day 5 RTP Capture Summary (probe-based)",
    "====================================",
    f"calls: {calls.get('calls')}",
    f"passed: {calls.get('passed')}",
    f"failed: {calls.get('failed')}",
    f"total_rtp_packets_sent: {total_sent}",
    f"total_rtp_packets_received: {total_recv}",
    "echo_verdict: PASS" if int(calls.get("failed", 1)) == 0 else "echo_verdict: FAIL",
]
Path("$PCAP_SUMMARY_TXT").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("rtp summary generated")
PY

echo "[13/13] Writing Day 5 evidence report..."
cat > "$EVIDENCE_DOC" <<EOF
# Day 5 Asterisk <-> C++ End-to-End Echo Evidence

Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Verifier: \
\`telephony/scripts/verify_day5_asterisk_cpp_echo.sh\`

## Acceptance Status

1. 20/20 consecutive calls with RTP echo: PASS
2. Silent calls: 0 (PASS)
3. Stuck sessions after hangup: 0 (PASS)
4. External ARI channels leaked: 0 (PASS)

## Evidence Files

1. \`telephony/docs/phase_3/evidence/day5/day5_20_calls_result.json\`
2. \`telephony/docs/phase_3/evidence/day5/day5_ari_event_trace.log\`
3. \`telephony/docs/phase_3/evidence/day5/day5_gateway_stats.json\`
4. \`telephony/docs/phase_3/evidence/day5/day5_asterisk_cli.log\`
5. \`telephony/docs/phase_3/evidence/day5/day5_pcap_summary.txt\`
6. \`telephony/docs/phase_3/evidence/day5/day5_verifier_output.txt\`

## Gate Verdict

Day 5 gate is **COMPLETE**.
EOF

echo "Day 5 verification PASSED."
echo "Evidence directory: $EVIDENCE_DIR"
