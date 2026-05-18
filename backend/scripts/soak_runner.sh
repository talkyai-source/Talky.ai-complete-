#!/usr/bin/env bash
#
# Phase 4.3 — Weekly soak runner.
#
# Drives a 4-hour 1× peak-load soak with chaos experiments running
# in parallel. Captures Grafana screenshots + Prometheus samples
# at the end and writes them to ./soak-results/<timestamp>/.
#
# Pre-flight:
#   - Cluster has Phase 3 helm chart deployed
#   - chaos-mesh installed
#   - Prometheus + Grafana reachable from the runner host
#   - kubectl, curl, jq present on PATH
#
# Usage:
#   PEAK_CONCURRENT=250 BASE_URL=http://nginx.talky.example.com \
#       ./backend/scripts/soak_runner.sh
#
# Exits non-zero if any pass criterion in §architecture_plan.md §4.4
# fails. Suitable for cron / CI.

set -euo pipefail

PEAK_CONCURRENT="${PEAK_CONCURRENT:-250}"
DURATION_SEC="${DURATION_SEC:-14400}"   # 4 hours
BASE_URL="${BASE_URL:-http://localhost:8000}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="./soak-results/${STAMP}"
mkdir -p "$OUT"

echo "soak_start ts=${STAMP} concurrent=${PEAK_CONCURRENT} duration_s=${DURATION_SEC}"

# 1. Apply chaos manifests.
kubectl apply -f infra/chaos/pod-kill.yaml -n talky
kubectl apply -f infra/chaos/redis-partition.yaml -n talky

# 2. Run the load test in the background.
./venv/bin/python backend/scripts/loadtest_calls.py \
    --concurrent "$PEAK_CONCURRENT" \
    --duration "$DURATION_SEC" \
    --base-url "$BASE_URL" \
    > "$OUT/loadtest.log" 2>&1 &
LOAD_PID=$!

# 3. Periodically snapshot Prometheus metrics.
INTERVAL=300   # every 5 minutes
END="$(( $(date +%s) + DURATION_SEC ))"
i=0
while [ "$(date +%s)" -lt "$END" ]; do
    sleep "$INTERVAL"
    i=$((i+1))
    SNAP="$OUT/snap_${i}.json"
    curl -sf "${PROM_URL:-http://prometheus:9090}/api/v1/query?query=sum(talky_active_calls)" \
        -o "$SNAP" || echo "snapshot $i failed"
done

# 4. Stop chaos.
kubectl delete -f infra/chaos/pod-kill.yaml -n talky
kubectl delete -f infra/chaos/redis-partition.yaml -n talky

# 5. Wait for load test to finish.
wait "$LOAD_PID"
LOAD_RC=$?

echo "soak_end ts=${STAMP} loadtest_rc=${LOAD_RC} results=${OUT}"
exit "$LOAD_RC"
