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

echo "[1/4] Running WS-B baseline verifier (prerequisite)..."
"$SCRIPT_DIR/verify_ws_b.sh" "$ENV_FILE"

echo "[2/4] Running WS-C transfer unit/API tests..."
(
  cd "$REPO_ROOT/backend"
  PYTHON_BIN="./venv/bin/python"
  if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
  fi
  "$PYTHON_BIN" -m unittest -v \
    tests.unit.test_freeswitch_transfer_control \
    tests.unit.test_freeswitch_transfer_api
)

echo "[3/4] Validating WS-C endpoints are present..."
rg -n "@router.post\\(\"/transfer/blind\"|@router.post\\(\"/transfer/attended\"|@router.post\\(\"/transfer/deflect\"|@router.get\\(\"/transfer/\\{attempt_id\\}\"" \
  "$REPO_ROOT/backend/app/api/v1/endpoints/freeswitch_bridge.py" >/dev/null

echo "[4/4] Validating WS-C core methods are present..."
for marker in "class TransferRequest" "class TransferResult" "async def request_transfer" "def _build_transfer_command" "def _update_transfer_state_from_event"; do
  if ! grep -Fq "$marker" "$REPO_ROOT/backend/app/infrastructure/telephony/freeswitch_esl.py"; then
    echo "[ERROR] Missing WS-C marker in freeswitch_esl.py: $marker"
    exit 1
  fi
done

echo
echo "WS-C verification PASSED."
