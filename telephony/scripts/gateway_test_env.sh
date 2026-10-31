#!/usr/bin/env bash

# Provision process-only credentials for local voice-gateway verification.
#
# The production gateway deliberately refuses to start without strong callback
# and control-plane secrets. Verification scripts must not reuse credentials
# sourced from backend/.env, print them, or persist them in evidence. This
# helper overwrites only the current process environment with independent
# random values; it never edits an environment file.
provision_voice_gateway_test_env() {
  local internal_token
  local gateway_token

  internal_token="$(od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]')"
  gateway_token="$(od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]')"
  if [[ ! "$internal_token" =~ ^[0-9a-f]{64}$ ]] || \
     [[ ! "$gateway_token" =~ ^[0-9a-f]{64}$ ]] || \
     [[ "$internal_token" == "$gateway_token" ]]; then
    echo "[ERROR] Unable to provision isolated voice-gateway test credentials." >&2
    return 1
  fi

  INTERNAL_SERVICE_TOKEN="$internal_token"
  VOICE_GATEWAY_AUTH_TOKEN="$gateway_token"
  VOICE_GATEWAY_CALLBACK_HOST="127.0.0.1"
  BACKEND_INTERNAL_URL="http://127.0.0.1:8000"
  export INTERNAL_SERVICE_TOKEN VOICE_GATEWAY_AUTH_TOKEN
  export VOICE_GATEWAY_CALLBACK_HOST BACKEND_INTERNAL_URL

  # Do not echo or return either secret. The exported process environment is
  # inherited by the local gateway and its authenticated test clients only.
  unset internal_token gateway_token
}
