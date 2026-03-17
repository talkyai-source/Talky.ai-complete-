#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony.example}"
DOC_ROOT="$TELEPHONY_ROOT/docs/phase_3"
EVIDENCE_DIR="$DOC_ROOT/evidence/day7"
mkdir -p "$EVIDENCE_DIR"

VERIFIER_OUT="$EVIDENCE_DIR/day7_verifier_output.txt"
exec > >(tee "$VERIFIER_OUT") 2>&1

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${DEEPGRAM_API_KEY:-}" ]]; then
  if [[ -f "$REPO_ROOT/backend/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$REPO_ROOT/backend/.env"
    set +a
  fi
fi

if [[ -z "${DEEPGRAM_API_KEY:-}" ]]; then
  if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$REPO_ROOT/.env"
    set +a
  fi
fi

if [[ -z "${DEEPGRAM_API_KEY:-}" ]]; then
  echo "[ERROR] DEEPGRAM_API_KEY is required for Day 7 verifier."
  echo "Set it in shell env, $ENV_FILE, $REPO_ROOT/.env, or $REPO_ROOT/backend/.env"
  exit 1
fi

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

PYTHON_BIN="${DAY7_PYTHON_BIN:-$REPO_ROOT/backend/venv/bin/python}"
AUDIO_FILE="${DAY7_AUDIO_FILE:-$REPO_ROOT/backend/tests/fixtures/test_greeting.wav}"
BATCHES="${DAY7_BATCHES:-3}"
CALLS_PER_BATCH="${DAY7_CALLS_PER_BATCH:-2}"

TOTAL_CALLS=$((BATCHES * CALLS_PER_BATCH))

BATCH_RESULTS="$EVIDENCE_DIR/day7_batch_call_results.json"
INTEGRITY_REPORT="$EVIDENCE_DIR/day7_transcript_integrity_report.json"
LATENCY_SUMMARY="$EVIDENCE_DIR/day7_stt_latency_summary.json"
STREAM_TRACE="$EVIDENCE_DIR/day7_deepgram_stream_trace.log"
GATEWAY_LOG="$EVIDENCE_DIR/day7_gateway_runtime.log"
ARI_TRACE="$EVIDENCE_DIR/day7_ari_event_trace.log"
EVIDENCE_DOC="$DOC_ROOT/day7_stt_streaming_evidence.md"

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

echo "[1/13] Verifying Python runtime for Day 7 probe..."
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] Python interpreter not found: $PYTHON_BIN"
  echo "Set DAY7_PYTHON_BIN to a Python with requests + websockets + numpy installed."
  exit 1
fi
"$PYTHON_BIN" - <<'PY'
import importlib.util
mods = ["requests", "websockets", "numpy"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
    raise SystemExit(f"Missing required modules: {', '.join(missing)}")
print("python dependency check: ok")
PY

echo "[2/13] Ensuring required telephony containers are running..."
"${compose_cmd[@]}" up -d --no-recreate asterisk rtpengine opensips

echo "[3/13] Reloading Asterisk ARI/http config..."
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
"$GATEWAY_BUILD_DIR/voice_gateway" --host "$GATEWAY_HOST" --port "$GATEWAY_HTTP_PORT" >"$GATEWAY_LOG" 2>&1 &
GW_PID=$!
for _ in $(seq 1 80); do
  if curl -fsS "$GATEWAY_BASE_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
curl -fsS "$GATEWAY_BASE_URL/health" >/dev/null

echo "[7/13] Starting ARI external media controller for Day 7 call batches..."
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
  --max-completed-calls "$TOTAL_CALLS" \
  --idle-timeout-seconds 90 >"$ARI_TRACE" 2>&1 &
ARI_PID=$!

for _ in $(seq 1 100); do
  if grep -q '"event": "controller_started"' "$ARI_TRACE" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if ! grep -q '"event": "controller_started"' "$ARI_TRACE" 2>/dev/null; then
  echo "[ERROR] Controller did not start correctly."
  tail -n 80 "$ARI_TRACE" || true
  exit 1
fi

echo "[8/13] Running Day 7 SIP+RTP+STT probe (batches=${BATCHES}, calls_per_batch=${CALLS_PER_BATCH})..."
"$PYTHON_BIN" "$SCRIPT_DIR/day7_stt_stream_probe.py" \
  --host 127.0.0.1 \
  --port "$SIP_PORT" \
  --extension "$EXTENSION" \
  --bind-ip 127.0.0.1 \
  --timeout 6.0 \
  --audio-file "$AUDIO_FILE" \
  --batches "$BATCHES" \
  --calls-per-batch "$CALLS_PER_BATCH" \
  --deepgram-api-key "$DEEPGRAM_API_KEY" \
  --output-results "$BATCH_RESULTS" \
  --output-integrity "$INTEGRITY_REPORT" \
  --output-latency "$LATENCY_SUMMARY" \
  --output-trace "$STREAM_TRACE"

echo "[9/13] Waiting for ARI controller completion..."
deadline=$((SECONDS + 120))
while ps -p "$ARI_PID" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "[ERROR] Controller did not finish within timeout."
    tail -n 120 "$ARI_TRACE" || true
    exit 1
  fi
  sleep 1
done
wait "$ARI_PID"
ari_exit=$?
ARI_PID=""
if [[ "$ari_exit" -ne 0 ]]; then
  echo "[ERROR] Controller exited with code $ari_exit"
  tail -n 160 "$ARI_TRACE" || true
  exit 1
fi

echo "[10/13] Validating Day 7 outputs..."
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path

batch = json.loads(Path("$BATCH_RESULTS").read_text(encoding="utf-8"))
integrity = json.loads(Path("$INTEGRITY_REPORT").read_text(encoding="utf-8"))
latency = json.loads(Path("$LATENCY_SUMMARY").read_text(encoding="utf-8"))

if int(batch.get("calls", -1)) != $TOTAL_CALLS:
    raise SystemExit(f"Expected calls=$TOTAL_CALLS, got {batch.get('calls')}")
if int(batch.get("failed", 1)) != 0:
    raise SystemExit(f"Expected failed=0, got {batch.get('failed')}")
if int(integrity.get("invalid_calls", 1)) != 0:
    raise SystemExit(f"Expected invalid_calls=0, got {integrity.get('invalid_calls')}")
if not bool(latency.get("stable", False)):
    raise SystemExit("Latency stability gate failed")
if latency.get("stt_first_transcript_ms", {}).get("p95") is None:
    raise SystemExit("Missing p95 STT latency")
print("day7 output validation: ok")
PY

echo "[11/13] Enforcing no active gateway sessions..."
GATEWAY_STATS_JSON="$EVIDENCE_DIR/day7_gateway_stats.json"
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

echo "[12/13] Optional Day 5/Day 6 regression checks..."
if [[ "${DAY7_RUN_DAY5_REGRESSION:-0}" == "1" ]]; then
  bash "$SCRIPT_DIR/verify_day5_asterisk_cpp_echo.sh" "$ENV_FILE"
fi
if [[ "${DAY7_RUN_DAY6_REGRESSION:-0}" == "1" ]]; then
  bash "$SCRIPT_DIR/verify_day6_media_resilience.sh"
fi

echo "[13/13] Writing Day 7 evidence report..."
cat > "$EVIDENCE_DOC" <<EOF
# Day 7 STT Streaming Evidence

Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Verifier: \
\`telephony/scripts/verify_day7_stt_streaming.sh\`

## Acceptance Status

1. Transcript generated per call in batch runs: PASS
2. Transcript integrity (\`call_id\` + \`talklee_call_id\`): PASS
3. p95 STT latency captured: PASS
4. p95 STT latency stability gate: PASS
5. No active sessions leaked after verification: PASS

## Evidence Files

1. \`telephony/docs/phase_3/evidence/day7/day7_batch_call_results.json\`
2. \`telephony/docs/phase_3/evidence/day7/day7_transcript_integrity_report.json\`
3. \`telephony/docs/phase_3/evidence/day7/day7_stt_latency_summary.json\`
4. \`telephony/docs/phase_3/evidence/day7/day7_deepgram_stream_trace.log\`
5. \`telephony/docs/phase_3/evidence/day7/day7_gateway_runtime.log\`
6. \`telephony/docs/phase_3/evidence/day7/day7_ari_event_trace.log\`
7. \`telephony/docs/phase_3/evidence/day7/day7_verifier_output.txt\`

## Gate Verdict

Day 7 gate is **COMPLETE** when all outputs are PASS and latency stability remains true.
EOF

echo "Day 7 verification PASSED."
echo "Evidence directory: $EVIDENCE_DIR"
