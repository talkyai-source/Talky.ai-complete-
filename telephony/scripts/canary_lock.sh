#!/usr/bin/env bash

# Shared fail-closed serialization for every durable canary state mutation.
# Source this file, then call acquire_canary_state_lock with the exact env file.

acquire_canary_state_lock() {
  local env_file="$1"
  local lock_file

  lock_file="${TELEPHONY_CANARY_LOCK_FILE:-${env_file}.canary.lock}"
  umask 077
  if command -v flock >/dev/null 2>&1; then
    CANARY_STATE_LOCK_MODE="flock"
    exec {CANARY_STATE_LOCK_FD}>"$lock_file"
    if flock -n "$CANARY_STATE_LOCK_FD"; then
      return 0
    fi
  else
    # Atomic mkdir is the portable fallback used by minimal recovery hosts.
    # A process crash intentionally leaves a fail-closed lock that requires
    # operator inspection/removal instead of guessing that activation is safe.
    CANARY_STATE_LOCK_MODE="mkdir"
    CANARY_STATE_LOCK_DIR="${lock_file}.d"
    if mkdir "$CANARY_STATE_LOCK_DIR" 2>/dev/null; then
      printf '%s\n' "$$" >"$CANARY_STATE_LOCK_DIR/owner_pid"
      return 0
    fi
  fi

  if [[ "${CANARY_STATE_LOCK_FD:-}" =~ ^[0-9]+$ ]]; then
    eval "exec ${CANARY_STATE_LOCK_FD}>&-"
  fi
  CANARY_STATE_LOCK_MODE=""
  echo "[ERROR] Another canary state change is in progress; no state was changed" >&2
  return 1
}

release_canary_state_lock() {
  if [[ "${CANARY_STATE_LOCK_MODE:-}" == "mkdir" ]]; then
    rm -f "${CANARY_STATE_LOCK_DIR}/owner_pid"
    rmdir "${CANARY_STATE_LOCK_DIR}" 2>/dev/null || true
  elif [[ "${CANARY_STATE_LOCK_MODE:-}" == "flock" ]] && \
       [[ "${CANARY_STATE_LOCK_FD:-}" =~ ^[0-9]+$ ]]; then
    eval "exec ${CANARY_STATE_LOCK_FD}>&-"
  fi
  CANARY_STATE_LOCK_MODE=""
}
