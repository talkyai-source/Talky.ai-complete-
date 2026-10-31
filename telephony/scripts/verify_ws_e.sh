#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

# Preserve original env values so verifier is side-effect free.
ENV_BACKUP="$(mktemp)"
cp "$ENV_FILE" "$ENV_BACKUP"
cleanup() {
  cp "$ENV_BACKUP" "$ENV_FILE"
  rm -f "$ENV_BACKUP"
}
trap cleanup EXIT

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
SIP_PORT="${OPENSIPS_SIP_PORT:-$(grep -E '^OPENSIPS_SIP_PORT=' "$ENV_FILE" | tail -n1 | cut -d= -f2)}"

echo "[1/10] Ensuring WS-D baseline verifier still passes..."
"$SCRIPT_DIR/verify_ws_d.sh" "$ENV_FILE"

echo "[2/10] Validating WS-E scripts exist and are executable..."
if [[ ! -f "$SCRIPT_DIR/assert_canary_ingress.sh" ]]; then
  echo "[ERROR] Missing startup assertion script: $SCRIPT_DIR/assert_canary_ingress.sh"
  exit 1
fi
for script in canary_set_stage.sh canary_freeze.sh canary_rollback.sh; do
  if [[ ! -x "$SCRIPT_DIR/$script" ]]; then
    echo "[ERROR] Missing executable script: $SCRIPT_DIR/$script"
    exit 1
  fi
done

echo "[3/10] Validating WS-E env keys are present..."
for key in OPENSIPS_CANARY_ENABLED OPENSIPS_CANARY_PERCENT OPENSIPS_CANARY_FREEZE OPENSIPS_CANARY_DID OPENSIPS_CANARY_AGENT_ID OPENSIPS_CANARY_SOURCE_REGEX; do
  if ! grep -q "^${key}=" "$ENV_FILE"; then
    echo "[ERROR] Missing env key in $ENV_FILE: $key"
    exit 1
  fi
done

echo "[4/10] Validating fail-closed config and Asterisk-only dispatcher..."
sh "$SCRIPT_DIR/assert_canary_ingress.sh" all "$ENV_FILE"

echo "[5/10] Validating OpenSIPS WS-E routing markers..."
for marker in \
  'loadmodule "cfgutils.so"' \
  'loadmodule "mi_fifo.so"' \
  'modparam("cfgutils", "initial_probability", 0)' \
  'modparam("mi_fifo", "fifo_name", "/tmp/opensips_fifo")' \
  'OPENSIPS_CANARY_ENABLED' \
  'OPENSIPS_CANARY_PERCENT' \
  '$rU != "$def(OPENSIPS_CANARY_DID)"' \
  'if (!ds_select_dst(2, 4))'; do
  if ! grep -Fq "$marker" "$TELEPHONY_ROOT/opensips/conf/opensips.cfg"; then
    echo "[ERROR] Missing WS-E marker in opensips.cfg: $marker"
    exit 1
  fi
done

if grep -Fq 'rand_set_prob' "$TELEPHONY_ROOT/opensips/conf/opensips.cfg" || \
   grep -Fq 'ds_select_dst(1,' "$TELEPHONY_ROOT/opensips/conf/opensips.cfg"; then
  echo "[ERROR] Unsafe sampled/stable fallback remains in OpenSIPS"
  exit 1
fi

echo "[6/10] Unfreezing and applying dedicated-DID canary at 100%..."
"$SCRIPT_DIR/canary_freeze.sh" unfreeze "$ENV_FILE"
"$SCRIPT_DIR/canary_set_stage.sh" 100 "$ENV_FILE"

if ! grep -q '^OPENSIPS_CANARY_ENABLED=1' "$ENV_FILE"; then
  echo "[ERROR] Canary enable flag not set after stage apply"
  exit 1
fi
if ! grep -q '^OPENSIPS_CANARY_PERCENT=100' "$ENV_FILE"; then
  echo "[ERROR] Dedicated-DID canary percent not set to 100 after stage apply"
  exit 1
fi

echo "[7/10] SIP smoke probe after canary stage update..."
python3 "$SCRIPT_DIR/sip_options_probe.py" \
  --host 127.0.0.1 \
  --port "$SIP_PORT" \
  --timeout 3.0 >/dev/null

echo "[8/10] Applying the fail-closed ingress freeze kill switch..."
"$SCRIPT_DIR/canary_freeze.sh" freeze "$ENV_FILE"
if ! grep -q '^OPENSIPS_CANARY_FREEZE=1' "$ENV_FILE"; then
  echo "[ERROR] Canary freeze flag not set"
  exit 1
fi
if "$SCRIPT_DIR/canary_set_stage.sh" 100 "$ENV_FILE" --no-docker >/dev/null 2>&1; then
  echo "[ERROR] Frozen canary unexpectedly accepted a stage command"
  exit 1
fi

echo "[9/10] Applying full rollback (must remain allowed while frozen)..."
"$SCRIPT_DIR/canary_rollback.sh" full "$ENV_FILE"

if ! grep -q '^OPENSIPS_CANARY_ENABLED=0' "$ENV_FILE"; then
  echo "[ERROR] Canary enable flag not reset to 0"
  exit 1
fi
if ! grep -q '^OPENSIPS_CANARY_PERCENT=0' "$ENV_FILE"; then
  echo "[ERROR] Canary percent not reset to 0"
  exit 1
fi
if ! grep -q '^OPENSIPS_CANARY_FREEZE=1' "$ENV_FILE"; then
  echo "[ERROR] Rollback must leave canary frozen"
  exit 1
fi

# Optional dispatcher MI smoke. Active runtime may disable dispatcher until DB-backed routing is enabled.
if ! "${compose_cmd[@]}" exec -T opensips sh -lc "opensips-cli -x mi ds_list >/dev/null 2>&1"; then
  echo "[WARN] Dispatcher MI not available in active runtime (expected when canary partition is disabled)"
fi

echo "[10/10] WS-E verification complete"
echo
echo "WS-E verification PASSED."
