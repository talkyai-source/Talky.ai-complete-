#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony.example}"
DOC_ROOT="$TELEPHONY_ROOT/docs/phase_3"
EVIDENCE_DIR="$DOC_ROOT/evidence/day8"
mkdir -p "$EVIDENCE_DIR"

VERIFIER_OUT="$EVIDENCE_DIR/day8_verifier_output.txt"
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
  echo "[ERROR] DEEPGRAM_API_KEY is required for Day 8 verifier."
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

PYTHON_BIN="${DAY8_PYTHON_BIN:-$REPO_ROOT/backend/venv/bin/python}"
AUDIO_FILE="${DAY8_AUDIO_FILE:-$REPO_ROOT/backend/tests/fixtures/test_greeting.wav}"
BATCHES="${DAY8_BATCHES:-3}"
CALLS_PER_BATCH="${DAY8_CALLS_PER_BATCH:-2}"
MAX_BARGE_REACTION_MS="${DAY8_MAX_BARGE_REACTION_MS:-250}"

TOTAL_CALLS=$((BATCHES * CALLS_PER_BATCH))

BATCH_RESULTS="$EVIDENCE_DIR/day8_batch_tts_results.json"
REACTION_SUMMARY="$EVIDENCE_DIR/day8_barge_in_reaction_summary.json"
STOP_REASON_SUMMARY="$EVIDENCE_DIR/day8_tts_stop_reason_summary.json"
PLAYBACK_TRACE="$EVIDENCE_DIR/day8_tts_playback_trace.log"
GATEWAY_LOG="$EVIDENCE_DIR/day8_gateway_runtime.log"
ARI_TRACE="$EVIDENCE_DIR/day8_ari_event_trace.log"
GATEWAY_STATS_JSON="$EVIDENCE_DIR/day8_gateway_stats.json"
ARI_BASELINE_JSON="$EVIDENCE_DIR/day8_ari_baseline_state.json"
ARI_POST_JSON="$EVIDENCE_DIR/day8_ari_post_state.json"
ARI_LEAK_REPORT="$EVIDENCE_DIR/day8_ari_leak_report.json"
EVIDENCE_DOC="$DOC_ROOT/day8_tts_bargein_evidence.md"

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

echo "[1/15] Verifying Python runtime for Day 8 probe..."
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] Python interpreter not found: $PYTHON_BIN"
  echo "Set DAY8_PYTHON_BIN to a Python with requests + websockets + numpy + aiohttp installed."
  exit 1
fi
"$PYTHON_BIN" - <<'PY'
import importlib.util
mods = ["requests", "websockets", "numpy", "aiohttp"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
    raise SystemExit(f"Missing required modules: {', '.join(missing)}")
print("python dependency check: ok")
PY

echo "[2/15] Ensuring required telephony containers are running..."
"${compose_cmd[@]}" up -d --no-recreate asterisk rtpengine opensips

echo "[3/15] Reloading Asterisk ARI/http config..."
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "core show uptime seconds" >/dev/null
docker exec -i talky-asterisk sh -lc "cat > /etc/asterisk/http.conf" < "$TELEPHONY_ROOT/asterisk/conf/http.conf"
docker exec -i talky-asterisk sh -lc "cat > /etc/asterisk/ari.conf" < "$TELEPHONY_ROOT/asterisk/conf/ari.conf"
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "core reload" >/dev/null
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_http_websocket.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_ari.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_ari_channels.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "http show status" | grep -qi "Server Enabled and Bound to"

echo "[4/15] Verifying ARI API reachability..."
"$PYTHON_BIN" - <<PY
import requests
url = "http://${ARI_HOST}:${ARI_PORT}/ari/asterisk/info"
resp = requests.get(url, params={"api_key": "${ARI_USERNAME}:${ARI_PASSWORD}"}, timeout=5)
if resp.status_code != 200:
    raise SystemExit(f"ARI ping failed: {resp.status_code} {resp.text[:300]}")
print("ari ping: ok")
PY

echo "[5/15] Capturing ARI baseline state..."
"$PYTHON_BIN" - <<PY
import json
import requests
from pathlib import Path

api = {"api_key": "${ARI_USERNAME}:${ARI_PASSWORD}"}
base = "http://${ARI_HOST}:${ARI_PORT}/ari"
channels = requests.get(f"{base}/channels", params=api, timeout=5).json()
bridges = requests.get(f"{base}/bridges", params=api, timeout=5).json()
external = sorted(str(ch.get("id") or "") for ch in channels if str(ch.get("name") or "").startswith("UnicastRTP/"))
bridge_ids = sorted(str(b.get("id") or "") for b in bridges)
payload = {"external_channel_ids": external, "bridge_ids": bridge_ids}
Path("$ARI_BASELINE_JSON").write_text(json.dumps(payload, indent=2), encoding="utf-8")
print("ari baseline capture: ok")
PY

echo "[6/15] Building C++ voice gateway and running unit tests..."
cmake -S "$GATEWAY_DIR" -B "$GATEWAY_BUILD_DIR" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$GATEWAY_BUILD_DIR" -- -j"$(nproc)" >/dev/null
ctest --test-dir "$GATEWAY_BUILD_DIR" --output-on-failure >/dev/null

echo "[7/15] Starting C++ voice gateway runtime..."
"$GATEWAY_BUILD_DIR/voice_gateway" --host "$GATEWAY_HOST" --port "$GATEWAY_HTTP_PORT" >"$GATEWAY_LOG" 2>&1 &
GW_PID=$!
for _ in $(seq 1 80); do
  if curl -fsS "$GATEWAY_BASE_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
curl -fsS "$GATEWAY_BASE_URL/health" >/dev/null

echo "[8/15] Starting ARI external media controller (Day 8 mode: echo disabled)..."
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
  --idle-timeout-seconds 90 \
  --gateway-echo-enabled 0 >"$ARI_TRACE" 2>&1 &
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

echo "[9/15] Running Day 8 TTS + barge-in probe (batches=${BATCHES}, calls_per_batch=${CALLS_PER_BATCH})..."
"$PYTHON_BIN" "$SCRIPT_DIR/day8_tts_bargein_probe.py" \
  --host 127.0.0.1 \
  --port "$SIP_PORT" \
  --extension "$EXTENSION" \
  --bind-ip 127.0.0.1 \
  --timeout 6.0 \
  --audio-file "$AUDIO_FILE" \
  --gateway-base-url "$GATEWAY_BASE_URL" \
  --batches "$BATCHES" \
  --calls-per-batch "$CALLS_PER_BATCH" \
  --max-barge-reaction-ms "$MAX_BARGE_REACTION_MS" \
  --deepgram-api-key "$DEEPGRAM_API_KEY" \
  --output-results "$BATCH_RESULTS" \
  --output-reaction "$REACTION_SUMMARY" \
  --output-stop-reasons "$STOP_REASON_SUMMARY" \
  --output-trace "$PLAYBACK_TRACE"

echo "[10/15] Waiting for ARI controller completion..."
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

echo "[11/15] Validating Day 8 probe outputs..."
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path

batch = json.loads(Path("$BATCH_RESULTS").read_text(encoding="utf-8"))
reaction = json.loads(Path("$REACTION_SUMMARY").read_text(encoding="utf-8"))
stop_reasons = json.loads(Path("$STOP_REASON_SUMMARY").read_text(encoding="utf-8"))

if int(batch.get("calls", -1)) != $TOTAL_CALLS:
    raise SystemExit(f"Expected calls=$TOTAL_CALLS, got {batch.get('calls')}")
if int(batch.get("failed", 1)) != 0:
    raise SystemExit(f"Expected failed=0, got {batch.get('failed')}")
if not bool(batch.get("start_of_turn_detected", False)):
    raise SystemExit("Expected start_of_turn_detected=true")
if not bool(reaction.get("pass", False)):
    raise SystemExit("Barge-in reaction gate failed")

p95 = reaction.get("barge_in_reaction_ms", {}).get("p95")
if p95 is None:
    raise SystemExit("Missing p95 barge_in_reaction_ms")
if float(p95) > float($MAX_BARGE_REACTION_MS):
    raise SystemExit(f"p95 barge reaction too high: {p95}ms")

results = batch.get("results", [])
barge_results = [r for r in results if r.get("scenario") == "barge_in"]
if not barge_results:
    raise SystemExit("No barge_in scenarios found in results")
for row in barge_results:
    if not bool(row.get("barge_in_success", False)):
        raise SystemExit(f"barge_in_success=false for call_index={row.get('call_index')}")
    if str(row.get("gateway_tts_stop_reason") or "") != "barge_in_start_of_turn":
        raise SystemExit(
            "Unexpected gateway_tts_stop_reason for call_index="
            f"{row.get('call_index')}: {row.get('gateway_tts_stop_reason')}"
        )

reason_counts = stop_reasons.get("stop_reasons", {})
if int(reason_counts.get("barge_in_start_of_turn", 0)) < len(barge_results):
    raise SystemExit("Missing barge_in_start_of_turn stop reasons")

print("day8 output validation: ok")
PY

echo "[12/15] Enforcing no active gateway sessions and drained TTS queue..."
curl -fsS "$GATEWAY_BASE_URL/stats" > "$GATEWAY_STATS_JSON"
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path

stats = json.loads(Path("$GATEWAY_STATS_JSON").read_text(encoding="utf-8"))
active = int(stats.get("active_sessions", -1))
queue_depth = int(stats.get("tts_queue_depth_frames", -1))

if active != 0:
    raise SystemExit(f"Expected active_sessions=0, got {active}")
if queue_depth != 0:
    raise SystemExit(f"Expected tts_queue_depth_frames=0, got {queue_depth}")
print("gateway cleanup validation: ok")
PY

echo "[13/15] Enforcing ARI cleanup (no leaked external channels or bridges)..."
"$PYTHON_BIN" - <<PY
import json
import requests
from pathlib import Path

api = {"api_key": "${ARI_USERNAME}:${ARI_PASSWORD}"}
base = "http://${ARI_HOST}:${ARI_PORT}/ari"
baseline = json.loads(Path("$ARI_BASELINE_JSON").read_text(encoding="utf-8"))

channels = requests.get(f"{base}/channels", params=api, timeout=5).json()
bridges = requests.get(f"{base}/bridges", params=api, timeout=5).json()
post = {
    "external_channel_ids": sorted(
        str(ch.get("id") or "")
        for ch in channels
        if str(ch.get("name") or "").startswith("UnicastRTP/")
    ),
    "bridge_ids": sorted(str(b.get("id") or "") for b in bridges),
}
Path("$ARI_POST_JSON").write_text(json.dumps(post, indent=2), encoding="utf-8")

baseline_external = set(baseline.get("external_channel_ids", []))
baseline_bridges = set(baseline.get("bridge_ids", []))
post_external = set(post.get("external_channel_ids", []))
post_bridges = set(post.get("bridge_ids", []))

leaked_external = sorted(post_external - baseline_external)
leaked_bridges = sorted(post_bridges - baseline_bridges)
report = {
    "leaked_external_channel_ids": leaked_external,
    "leaked_bridge_ids": leaked_bridges,
}
Path("$ARI_LEAK_REPORT").write_text(json.dumps(report, indent=2), encoding="utf-8")

if leaked_external or leaked_bridges:
    raise SystemExit(
        "ARI leak detected: "
        f"external={leaked_external} bridges={leaked_bridges}"
    )
print("ari cleanup validation: ok")
PY

echo "[14/15] Optional Day 6/Day 7 regression checks..."
if [[ "${DAY8_RUN_DAY6_REGRESSION:-0}" == "1" ]]; then
  bash "$SCRIPT_DIR/verify_day6_media_resilience.sh"
fi
if [[ "${DAY8_RUN_DAY7_REGRESSION:-0}" == "1" ]]; then
  bash "$SCRIPT_DIR/verify_day7_stt_streaming.sh" "$ENV_FILE"
fi

echo "[15/15] Writing Day 8 evidence report..."
cat > "$EVIDENCE_DOC" <<EOF
# Day 8 TTS + Barge-In Evidence

Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Verifier: \`telephony/scripts/verify_day8_tts_bargein.sh\`

## Acceptance Status

1. Controlled batch calls produced audible TTS playback on frozen path: PASS
2. Barge-in stop reason (\`barge_in_start_of_turn\`) deterministic: PASS
3. p95 barge-in reaction time <= ${MAX_BARGE_REACTION_MS}ms: PASS
4. TTS queue bounded and fully drained post-run: PASS
5. No leaked active gateway sessions/channels/bridges: PASS

## Evidence Files

1. \`telephony/docs/phase_3/evidence/day8/day8_batch_tts_results.json\`
2. \`telephony/docs/phase_3/evidence/day8/day8_barge_in_reaction_summary.json\`
3. \`telephony/docs/phase_3/evidence/day8/day8_tts_playback_trace.log\`
4. \`telephony/docs/phase_3/evidence/day8/day8_tts_stop_reason_summary.json\`
5. \`telephony/docs/phase_3/evidence/day8/day8_gateway_runtime.log\`
6. \`telephony/docs/phase_3/evidence/day8/day8_ari_event_trace.log\`
7. \`telephony/docs/phase_3/evidence/day8/day8_gateway_stats.json\`
8. \`telephony/docs/phase_3/evidence/day8/day8_ari_baseline_state.json\`
9. \`telephony/docs/phase_3/evidence/day8/day8_ari_post_state.json\`
10. \`telephony/docs/phase_3/evidence/day8/day8_ari_leak_report.json\`
11. \`telephony/docs/phase_3/evidence/day8/day8_verifier_output.txt\`

## Gate Verdict

Day 8 gate is **COMPLETE** when all outputs are PASS and no post-run leakage is detected.
EOF

echo "Day 8 verification PASSED."
echo "Evidence directory: $EVIDENCE_DIR"
