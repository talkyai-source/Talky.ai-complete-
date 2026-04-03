#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATEWAY_DIR="$REPO_ROOT/services/voice-gateway-cpp"
BUILD_DIR="$GATEWAY_DIR/build"
EVIDENCE_DIR="$REPO_ROOT/telephony/docs/phase_3/evidence/day6"
SUMMARY_DOC="$REPO_ROOT/telephony/docs/phase_3/day6_media_resilience_evidence.md"

HOST="127.0.0.1"
PORT="18080"

mkdir -p "$EVIDENCE_DIR"

VERIFIER_OUT="$EVIDENCE_DIR/day6_verifier_output.txt"
FAULT_RESULTS="$EVIDENCE_DIR/day6_fault_injection_results.json"
TIMEOUT_SUMMARY="$EVIDENCE_DIR/day6_timeout_reason_summary.json"
JITTER_METRICS="$EVIDENCE_DIR/day6_jitter_buffer_metrics.json"
GATEWAY_LOG="$EVIDENCE_DIR/day6_gateway_runtime.log"
ARI_TRACE="$EVIDENCE_DIR/day6_ari_event_trace.log"
MEMORY_PROFILE="$EVIDENCE_DIR/day6_memory_profile.txt"

exec > >(tee "$VERIFIER_OUT") 2>&1

GW_PID=""
cleanup() {
  set +e
  if [[ -n "$GW_PID" ]] && ps -p "$GW_PID" >/dev/null 2>&1; then
    kill "$GW_PID" >/dev/null 2>&1 || true
    wait "$GW_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[1/8] Building and testing voice-gateway-cpp..."
cmake -S "$GATEWAY_DIR" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$BUILD_DIR" -- -j"$(nproc)" >/dev/null
ctest --test-dir "$BUILD_DIR" --output-on-failure

echo "[2/8] Starting voice-gateway-cpp runtime..."
"$BUILD_DIR/voice_gateway" --host "$HOST" --port "$PORT" >"$GATEWAY_LOG" 2>&1 &
GW_PID=$!

for _ in $(seq 1 80); do
  if curl -fsS "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
curl -fsS "http://$HOST:$PORT/health" >/dev/null

echo "[3/8] Running Day 6 fault-injection probe..."
python3 "$REPO_ROOT/telephony/scripts/day6_media_resilience_probe.py" \
  --host "$HOST" \
  --port "$PORT" \
  --output-results "$FAULT_RESULTS" \
  --output-timeout-summary "$TIMEOUT_SUMMARY" \
  --output-jitter-metrics "$JITTER_METRICS"

echo "[4/8] Capturing memory profile and runtime stats..."
{
  echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "pid=$GW_PID"
  ps -o pid,ppid,comm,rss,vsz,%cpu,%mem,etime -p "$GW_PID"
  echo ""
  echo "gateway_stats:"
  curl -fsS "http://$HOST:$PORT/stats"
} > "$MEMORY_PROFILE"

echo "[5/8] Recording ARI trace note for Day 6 verifier context..."
cat > "$ARI_TRACE" <<TRACE
{"event":"day6_local_probe","note":"ARI signaling path is validated by Day 5 verifier. Day 6 verifier focuses media resilience in C++ gateway runtime.","ts":"$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
TRACE

echo "[6/8] Validating Day 6 acceptance checks..."
python3 - <<PY
import json
from pathlib import Path

fault = json.loads(Path("$FAULT_RESULTS").read_text(encoding="utf-8"))
timeouts = json.loads(Path("$TIMEOUT_SUMMARY").read_text(encoding="utf-8"))
jitter = json.loads(Path("$JITTER_METRICS").read_text(encoding="utf-8"))

if int(fault.get("failed", 1)) != 0:
    raise SystemExit(f"fault scenarios failed: {fault.get('failed')}")

stats = fault.get("process_stats", {})
if int(stats.get("active_sessions", -1)) != 0:
    raise SystemExit(f"expected active_sessions=0, got {stats.get('active_sessions')}")

if int(timeouts.get("start_timeout", 0)) < 1:
    raise SystemExit("expected at least one start_timeout event")
if int(timeouts.get("no_rtp_timeout", 0)) < 1:
    raise SystemExit("expected at least one no_rtp_timeout event")
if int(timeouts.get("no_rtp_timeout_hold", 0)) < 1:
    raise SystemExit("expected at least one no_rtp_timeout_hold event")

if int(jitter.get("out_of_order_packets", 0)) < 1:
    raise SystemExit("expected out_of_order_packets > 0")
if int(jitter.get("jitter_buffer_overflow_drops", 0)) < 1:
    raise SystemExit("expected jitter_buffer_overflow_drops > 0")

print("acceptance validation: ok")
PY

echo "[7/8] Optional regression checks (Day 4 / Day 5) ..."
if [[ "${DAY6_RUN_DAY4_REGRESSION:-0}" == "1" ]]; then
  bash "$REPO_ROOT/telephony/scripts/verify_day4_cpp_gateway.sh"
fi
if [[ "${DAY6_RUN_DAY5_REGRESSION:-0}" == "1" ]]; then
  bash "$REPO_ROOT/telephony/scripts/verify_day5_asterisk_cpp_echo.sh" "${DAY6_ENV_FILE:-$REPO_ROOT/telephony/deploy/docker/.env.telephony.example}"
fi

echo "[8/8] Writing Day 6 evidence summary..."
cat > "$SUMMARY_DOC" <<DOC
# Day 6 Media Resilience Evidence

Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Verifier: \`telephony/scripts/verify_day6_media_resilience.sh\`

## Acceptance Status

1. Startup silence timeout path: PASS (\`start_timeout\`)
2. Active no-RTP timeout path: PASS (\`no_rtp_timeout\`)
3. Hold timeout path: PASS (\`no_rtp_timeout_hold\`)
4. Reorder/duplicate accounting: PASS
5. Queue-pressure bounded drops: PASS
6. Active sessions after scenarios: 0 (PASS)

## Evidence Artifacts

1. \`telephony/docs/phase_3/evidence/day6/day6_verifier_output.txt\`
2. \`telephony/docs/phase_3/evidence/day6/day6_fault_injection_results.json\`
3. \`telephony/docs/phase_3/evidence/day6/day6_timeout_reason_summary.json\`
4. \`telephony/docs/phase_3/evidence/day6/day6_jitter_buffer_metrics.json\`
5. \`telephony/docs/phase_3/evidence/day6/day6_gateway_runtime.log\`
6. \`telephony/docs/phase_3/evidence/day6/day6_ari_event_trace.log\`
7. \`telephony/docs/phase_3/evidence/day6/day6_memory_profile.txt\`

## Regression Notes

1. Day 4 regression can be enabled with \`DAY6_RUN_DAY4_REGRESSION=1\`.
2. Day 5 regression can be enabled with \`DAY6_RUN_DAY5_REGRESSION=1\` and an env file.
DOC

echo "DAY6 verification PASSED."
echo "Evidence: $EVIDENCE_DIR"
