#!/usr/bin/env bash
# healthwatch.sh — poll /api/v1/healthz/workers and log a CRITICAL journald
# line (severity err) whenever any background worker (dialer/voice/reminder)
# is unhealthy.
#
# Run by talky-healthwatch.service (oneshot), triggered every 2 minutes by
# talky-healthwatch.timer. StandardError=journal on the unit means the
# "HEALTHWATCH CRITICAL" line below lands in the journal at priority err, so
# `journalctl -p err` (or any journald-severity alert) catches it. A non-200
# response makes this script exit non-zero, which systemd also records as a
# failed unit run — a second, independent signal on top of the log line.
set -euo pipefail

HEALTH_URL="${TALKY_HEALTHWATCH_URL:-http://localhost:8000/api/v1/healthz/workers}"

response="$(curl -sS -m 10 -w '\n%{http_code}' "$HEALTH_URL" 2>&1)" || {
    echo "HEALTHWATCH CRITICAL: could not reach $HEALTH_URL: $response" >&2
    exit 1
}

http_code="$(printf '%s\n' "$response" | tail -n1)"
body="$(printf '%s\n' "$response" | sed '$d')"

if [ "$http_code" != "200" ]; then
    echo "HEALTHWATCH CRITICAL: workers unhealthy (http $http_code): $body" >&2
    exit 1
fi

echo "healthwatch: all workers healthy"
