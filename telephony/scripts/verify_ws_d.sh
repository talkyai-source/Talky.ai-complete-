#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony.example}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

echo "[1/6] Running WS-C baseline verifier (prerequisite)..."
"$SCRIPT_DIR/verify_ws_c.sh" "$ENV_FILE"

echo "[2/6] Running backend WS-D unit tests..."
(
  cd "$REPO_ROOT/backend"
  PYTHON_BIN="./venv/bin/python"
  if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
  fi
  "$PYTHON_BIN" -m pytest -q \
    tests/unit/test_browser_media_gateway_ws_d.py \
    tests/unit/test_latency_tracker.py
)

echo "[3/6] Validating WS-D bridge markers are present..."
for marker in "validate_pcm_format(" "_max_queue_size" "_target_buffer_ms" "_max_buffer_ms" "_ws_send_timeout_ms" "ws_send_timeouts" "dropped_output_bytes" "async def _send_payload"; do
  if ! grep -Fq "$marker" "$REPO_ROOT/backend/app/infrastructure/telephony/browser_media_gateway.py"; then
    echo "[ERROR] Missing WS-D marker in browser_media_gateway.py: $marker"
    exit 1
  fi
done

echo "[4/6] Validating WS-D latency markers are present..."
for marker in "def mark_listening_start" "def mark_stt_first_transcript" "def mark_llm_first_token" "def mark_tts_first_chunk" "def mark_response_start" "def get_percentiles" "def build_baseline_snapshot"; do
  if ! grep -Fq "$marker" "$REPO_ROOT/backend/app/domain/services/latency_tracker.py"; then
    echo "[ERROR] Missing WS-D marker in latency_tracker.py: $marker"
    exit 1
  fi
done

echo "[5/6] Validating WS-D planning/report docs exist..."
for doc in \
  "$TELEPHONY_ROOT/docs/12_ws_d_media_bridge_latency_plan.md" \
  "$TELEPHONY_ROOT/docs/phase1_baseline_latency.md"; do
  if [[ ! -f "$doc" ]]; then
    echo "[ERROR] Missing WS-D document: $doc"
    exit 1
  fi
done

echo "[6/6] WS-D verification complete"
echo
echo "WS-D verification PASSED."
