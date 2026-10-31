#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ASSERT_SCRIPT="$SCRIPT_DIR/assert_canary_ingress.sh"
LOCK_SCRIPT="$SCRIPT_DIR/canary_lock.sh"

STAGE_PERCENT="${1:-}"
ENV_FILE="${2:-$TELEPHONY_ROOT/deploy/docker/.env.telephony}"
NO_DOCKER=0

if [[ -z "$STAGE_PERCENT" ]]; then
  echo "Usage: $0 <0|100> [env_file] [--force] [--no-docker]"
  exit 1
fi

for flag in "${@:3}"; do
  case "$flag" in
    --force)
      # Retained for compatibility. It never bypasses freeze or validation.
      :
      ;;
    --no-docker)
      NO_DOCKER=1
      ;;
    *)
      echo "[ERROR] Unknown flag: $flag"
      echo "Usage: $0 <0|100> [env_file] [--force] [--no-docker]"
      exit 1
      ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

case "$STAGE_PERCENT" in
  0|100) ;;
  *)
    echo "[ERROR] Invalid stage percent: $STAGE_PERCENT"
    echo "Allowed stages: 0, 100 (dedicated-DID canary is never sampled)"
    exit 1
    ;;
esac

set_kv() {
  local key="$1"
  local value="$2"
  local file="$3"
  if grep -q "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    echo "${key}=${value}" >> "$file"
  fi
}

# Hold one shared lock across validation, durable mutation, and runtime apply.
# Rollback/freeze use the same lock file and therefore cannot race activation.
source "$LOCK_SCRIPT"
acquire_canary_state_lock "$ENV_FILE"

original_env="$(mktemp "${ENV_FILE}.original.XXXXXX")"
candidate_env=""
safe_env=""
activation_apply_started=0
activation_committed=0
docker_bin="${TELEPHONY_DOCKER_BIN:-docker}"
cp -p "$ENV_FILE" "$original_env"

force_safe_durable_state() {
  safe_env="$(mktemp "${ENV_FILE}.safe.XXXXXX")"
  cp -p "$original_env" "$safe_env"
  set_kv "OPENSIPS_CANARY_ENABLED" "0" "$safe_env"
  set_kv "OPENSIPS_CANARY_PERCENT" "0" "$safe_env"
  set_kv "OPENSIPS_CANARY_FREEZE" "1" "$safe_env"
  if mv -f "$safe_env" "$ENV_FILE"; then
    safe_env=""
    return 0
  fi

  # If the atomic replacement itself is unavailable, make one last explicit
  # fail-closed write before the runtime is stopped.
  set_kv "OPENSIPS_CANARY_ENABLED" "0" "$ENV_FILE"
  set_kv "OPENSIPS_CANARY_PERCENT" "0" "$ENV_FILE"
  set_kv "OPENSIPS_CANARY_FREEZE" "1" "$ENV_FILE"
}

cleanup_set_stage() {
  local exit_code=$?
  trap - EXIT
  set +e
  if [[ "$STAGE_PERCENT" == "100" && "$activation_apply_started" == "1" && "$activation_committed" != "1" ]]; then
    echo "[ERROR] Activation did not commit; forcing disabled, 0%, frozen state" >&2
    force_safe_durable_state
    safe_compose=("$docker_bin" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
    "${safe_compose[@]}" stop opensips >/dev/null 2>&1 || true
  fi
  [[ -z "$candidate_env" ]] || rm -f "$candidate_env"
  [[ -z "$safe_env" ]] || rm -f "$safe_env"
  rm -f "$original_env"
  release_canary_state_lock
  exit "$exit_code"
}
trap cleanup_set_stage EXIT

# Every mutable safety value is read only after the shared lock is held and
# the exact original file has been retained for recovery.
current_freeze="$(grep -E '^OPENSIPS_CANARY_FREEZE=' "$ENV_FILE" | tail -n1 | cut -d= -f2 || true)"
if [[ -z "$current_freeze" ]]; then
  current_freeze="1"
fi
current_enabled="$(grep -E '^OPENSIPS_CANARY_ENABLED=' "$ENV_FILE" | tail -n1 | cut -d= -f2 || true)"
current_percent="$(grep -E '^OPENSIPS_CANARY_PERCENT=' "$ENV_FILE" | tail -n1 | cut -d= -f2 || true)"
current_enabled="${current_enabled:-0}"
current_percent="${current_percent:-0}"

case "${current_enabled}:${current_percent}:${current_freeze}" in
  0:0:0|0:0:1|1:100:0) ;;
  *)
    echo "[ERROR] Existing canary state is inconsistent; activation refused"
    exit 1
    ;;
esac

if [[ "$current_freeze" == "1" && "$STAGE_PERCENT" != "0" ]]; then
  echo "[ERROR] Canary is frozen (OPENSIPS_CANARY_FREEZE=1). Explicitly unfreeze before activation; --force cannot bypass this safety gate."
  exit 1
fi

if [[ "$STAGE_PERCENT" == "0" ]]; then
  enabled="0"
else
  enabled="1"
fi

# Build and validate a same-directory candidate. It is never the durable env
# until the requested runtime state is proven healthy.
candidate_env="$(mktemp "${ENV_FILE}.candidate.XXXXXX")"
cp -p "$original_env" "$candidate_env"
set_kv "OPENSIPS_CANARY_ENABLED" "$enabled" "$candidate_env"
set_kv "OPENSIPS_CANARY_PERCENT" "$STAGE_PERCENT" "$candidate_env"
if [[ "$STAGE_PERCENT" == "0" ]]; then
  set_kv "OPENSIPS_CANARY_FREEZE" "1" "$candidate_env"
fi
sh "$ASSERT_SCRIPT" env "$candidate_env"

if [[ "$NO_DOCKER" -eq 1 && "$STAGE_PERCENT" == "100" ]]; then
  echo "[OK] Canary activation candidate validated (no docker); env/runtime unchanged"
  exit 0
fi

if [[ "$STAGE_PERCENT" == "0" ]]; then
  # Closing ingress publishes the durable kill state before any runtime action.
  mv -f "$candidate_env" "$ENV_FILE"
  candidate_env=""
  if [[ "$NO_DOCKER" -eq 1 ]]; then
    echo "[OK] Canary disabled, 0%, and frozen durably (no docker apply)"
    exit 0
  fi

  compose_cmd=("$docker_bin" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
  "${compose_cmd[@]}" stop opensips >/dev/null
  "${compose_cmd[@]}" up -d opensips >/dev/null
  sleep 2
  "${compose_cmd[@]}" exec -T opensips opensips -C -f /etc/opensips/opensips.cfg >/dev/null
  echo "[OK] Canary disabled, 0%, frozen, and runtime config validated"
  exit 0
fi

# Activation is the inverse: apply and validate the candidate runtime first,
# then atomically publish the already-proven candidate as durable state.
candidate_compose=("$docker_bin" compose --env-file "$candidate_env" -f "$COMPOSE_FILE")
activation_apply_started=1
echo "[INFO] Applying canary activation candidate"
"${candidate_compose[@]}" up -d opensips >/dev/null
sleep 2
running_services="$("${candidate_compose[@]}" ps --status running --services)"
if ! grep -qx "opensips" <<<"$running_services"; then
  echo "[ERROR] OpenSIPS is not running after candidate activation"
  "${candidate_compose[@]}" ps || true
  exit 1
fi
"${candidate_compose[@]}" exec -T opensips opensips -C -f /etc/opensips/opensips.cfg >/dev/null

mv -f "$candidate_env" "$ENV_FILE"
candidate_env=""
activation_committed=1
echo "[OK] Canary activation runtime validated and durable state committed atomically"
