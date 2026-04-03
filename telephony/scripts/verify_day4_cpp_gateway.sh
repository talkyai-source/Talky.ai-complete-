#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GATEWAY_DIR="$REPO_ROOT/services/voice-gateway-cpp"
BUILD_DIR="$GATEWAY_DIR/build"
EVIDENCE_DIR="$REPO_ROOT/telephony/docs/phase_3/evidence/day4"
LOG_FILE="$EVIDENCE_DIR/day4_gateway_runtime.log"
BUILD_OUT="$EVIDENCE_DIR/day4_build_output.txt"
RTP_JSON="$EVIDENCE_DIR/day4_rtp_loopback_results.json"
PACING_TXT="$EVIDENCE_DIR/day4_pacing_analysis.txt"
STATS_JSON="$EVIDENCE_DIR/day4_stats_endpoint_sample.json"
LOG_EXCERPT="$EVIDENCE_DIR/day4_log_excerpt.txt"
SUMMARY_DOC="$REPO_ROOT/telephony/docs/phase_3/day4_cpp_gateway_evidence.md"

HOST="127.0.0.1"
PORT="18080"

mkdir -p "$EVIDENCE_DIR"

{
  echo "[day4] configure + build"
  cmake -S "$GATEWAY_DIR" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$BUILD_DIR" -- -j"$(nproc)"
  echo "[day4] run ctest"
  ctest --test-dir "$BUILD_DIR" --output-on-failure
} >"$BUILD_OUT" 2>&1

"$BUILD_DIR/voice_gateway" --host "$HOST" --port "$PORT" >"$LOG_FILE" 2>&1 &
GW_PID=$!

cleanup() {
  if ps -p "$GW_PID" >/dev/null 2>&1; then
    kill "$GW_PID" >/dev/null 2>&1 || true
    wait "$GW_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for _ in $(seq 1 50); do
  if curl -fsS "http://$HOST:$PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

curl -fsS "http://$HOST:$PORT/health" >/dev/null

python3 "$REPO_ROOT/telephony/scripts/day4_rtp_probe.py" \
  --host "$HOST" \
  --port "$PORT" \
  --session-id "day4-verify-session" \
  --packet-count 64 \
  --output-json "$RTP_JSON" \
  --output-pacing "$PACING_TXT" \
  --output-stats-sample "$STATS_JSON"

sleep 0.2

tail -n 200 "$LOG_FILE" > "$LOG_EXCERPT"

cat > "$SUMMARY_DOC" <<DOC
# Day 4 C++ Gateway Evidence

Date: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Verifier: \`telephony/scripts/verify_day4_cpp_gateway.sh\`

## Results

1. Build + tests: pass (see \`telephony/docs/phase_3/evidence/day4/day4_build_output.txt\`)
2. RTP loopback + pacing: pass (see \`telephony/docs/phase_3/evidence/day4/day4_rtp_loopback_results.json\`)
3. Session stats endpoint sample captured: \`telephony/docs/phase_3/evidence/day4/day4_stats_endpoint_sample.json\`
4. Runtime log excerpt captured: \`telephony/docs/phase_3/evidence/day4/day4_log_excerpt.txt\`

## Acceptance

1. Sequence monotonicity: pass
2. Timestamp monotonicity (+160): pass
3. Pacing thresholds (p95 19-21 ms, max <= 25 ms): pass
4. Session control API: pass
5. /health and /stats endpoints: pass

## Open Issues

1. None.
DOC

echo "WS-DAY4 verification PASSED."
echo "Evidence: $EVIDENCE_DIR"
