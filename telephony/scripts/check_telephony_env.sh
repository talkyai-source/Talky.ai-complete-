#!/usr/bin/env bash
set -euo pipefail

# Basic environment validation for telephony migration.

required_vars=(
  "DATABASE_URL"
  "REDIS_URL"
  "DEEPGRAM_API_KEY"
  "GROQ_API_KEY"
)

missing=0
for v in "${required_vars[@]}"; do
  if [[ -z "${!v:-}" ]]; then
    echo "[ERROR] Missing required env: $v"
    missing=1
  else
    echo "[OK] $v set"
  fi
done

echo
if [[ $missing -ne 0 ]]; then
  echo "Telephony env check failed."
  exit 1
fi

echo "Telephony env check passed."
