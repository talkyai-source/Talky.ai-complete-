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
#
# A restart window is not an outage: talky-api takes a while to listen
# (wait-for-deps) and the worker heartbeats refill after a deploy restart, so
# a single failed probe used to page CRITICAL on every deploy. Each tick now
# retries (default 6 tries, 10 s apart, ~1-2 min worst case) and pages only if
# EVERY try failed — a worker dead past the 180 s heartbeat TTL, or an API that
# stays down, still pages CRITICAL and exits 1. Retry notes go to stdout (info),
# never stderr (err), so they cannot trip a `journalctl -p err` alert.
set -euo pipefail

HEALTH_URL="${TALKY_HEALTHWATCH_URL:-http://localhost:8000/api/v1/healthz/workers}"
retries="${TALKY_HEALTHWATCH_RETRIES:-6}"
retry_delay="${TALKY_HEALTHWATCH_RETRY_DELAY:-10}"

attempt=1
while :; do
    if response="$(curl -sS -m 10 -w '\n%{http_code}' "$HEALTH_URL" 2>&1)"; then
        http_code="$(printf '%s\n' "$response" | tail -n1)"
        body="$(printf '%s\n' "$response" | sed '$d')"
        if [ "$http_code" = "200" ]; then
            echo "healthwatch: all workers healthy"
            exit 0
        fi
        failure="workers unhealthy (http $http_code): $body"
    else
        failure="could not reach $HEALTH_URL: $response"
    fi

    if [ "$attempt" -ge "$retries" ]; then
        echo "HEALTHWATCH CRITICAL: $failure" >&2
        exit 1
    fi
    echo "healthwatch: attempt $attempt/$retries failed (possible restart window), retrying in ${retry_delay}s: $failure"
    attempt=$((attempt + 1))
    sleep "$retry_delay"
done
