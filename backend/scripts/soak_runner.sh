#!/usr/bin/env bash
#
# Phase 4.3 — Weekly soak runner.
#
# Drives a 4-hour 1× peak-load soak with chaos experiments running
# in parallel. Captures periodic Prometheus samples and writes them to
# ./soak-results/<timestamp>/.
#
# Pre-flight:
#   - Cluster has Phase 3 helm chart deployed
#   - chaos-mesh installed
#   - Prometheus reachable from the runner host
#   - kubectl and curl present on PATH
#   - INTERNAL_SERVICE_TOKEN and LOADTEST_TENANT_ID exported
#
# Usage:
#   INTERNAL_SERVICE_TOKEN=... LOADTEST_TENANT_ID=... \
#   REQUIRED_PEAK_LIVE_CALLS=250 BASE_URL=http://nginx.talky.example.com \
#       ./backend/scripts/soak_runner.sh
#
# Exits non-zero on load-driver, metrics-snapshot, or chaos-cleanup failure.
# SLO and per-call acceptance evidence must be evaluated separately; this
# transport/resilience driver alone is not a release verdict.

set -euo pipefail

REQUIRED_PEAK_LIVE_CALLS="${REQUIRED_PEAK_LIVE_CALLS:-}"
REQUIRED_ORIGINATIONS=300
REQUEST_WORKERS="${SOAK_REQUEST_WORKERS:-10}"
DURATION_SEC="${DURATION_SEC:-14400}"   # 4 hours
BASE_URL="${BASE_URL:-http://localhost:8000}"
PYTHON_BIN="${SOAK_PYTHON_BIN:-./backend/venv/bin/python}"
LOADTEST_SCRIPT="${SOAK_LOADTEST_SCRIPT:-backend/scripts/loadtest_calls.py}"
POLL_INTERVAL_SEC="${SOAK_POLL_INTERVAL_SEC:-1}"
METRICS_INTERVAL_SEC="${SOAK_METRICS_INTERVAL_SEC:-300}"
LIVE_STATUS_POLL_SEC="${SOAK_LIVE_STATUS_POLL_SEC:-0.25}"
DRAIN_TIMEOUT_SEC="${SOAK_DRAIN_TIMEOUT_SEC:-300}"
PROM_URL="${PROM_URL:-http://prometheus:9090}"
# Code-owned exporter gauge. Do not replace this with an aspirational metric:
# an empty result is a hard monitoring failure below.
PROM_METRIC="talky_telephony_calls_setup_attempts"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULTS_ROOT="${SOAK_RESULTS_DIR:-./soak-results}"
OUT="${RESULTS_ROOT}/${STAMP}"

LOAD_PID=""
LOAD_RC=""
# This is set *before* the first apply. A request can be accepted by the API
# server even when kubectl loses the response, so a failed apply is uncertain
# and must still trigger deletion of both experiments.
CHAOS_CLEANUP_REQUIRED=0

terminate_load() {
    if [[ -z "$LOAD_PID" ]]; then
        return 0
    fi
    if kill -0 "$LOAD_PID" >/dev/null 2>&1; then
        kill "$LOAD_PID" >/dev/null 2>&1 || true
    fi
    wait "$LOAD_PID" >/dev/null 2>&1 || true
    LOAD_PID=""
}

cleanup_chaos() {
    local cleanup_rc=0
    if [[ "$CHAOS_CLEANUP_REQUIRED" -ne 1 ]]; then
        return 0
    fi
    kubectl delete -f infra/chaos/pod-kill.yaml -n talky --ignore-not-found || cleanup_rc=1
    kubectl delete -f infra/chaos/redis-partition.yaml -n talky --ignore-not-found || cleanup_rc=1
    if [[ "$cleanup_rc" -eq 0 ]]; then
        CHAOS_CLEANUP_REQUIRED=0
    fi
    return "$cleanup_rc"
}

cleanup() {
    local original_rc=$?
    local cleanup_rc=0
    trap - EXIT INT TERM
    terminate_load
    if ! cleanup_chaos; then
        cleanup_rc=1
    fi
    if [[ "$original_rc" -eq 0 && "$cleanup_rc" -ne 0 ]]; then
        original_rc=1
    fi
    exit "$original_rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

require_positive_integer() {
    local name="$1"
    local value="$2"
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        echo "$name must be a positive integer: $value" >&2
        exit 2
    fi
}

require_positive_number() {
    local name="$1"
    local value="$2"
    if [[ ! "$value" =~ ^([0-9]+([.][0-9]+)?|[.][0-9]+)$ ]] || \
       [[ "$value" =~ ^0+([.]0+)?$ ]]; then
        echo "$name must be a positive number: $value" >&2
        exit 2
    fi
}

require_positive_integer "REQUIRED_PEAK_LIVE_CALLS" "$REQUIRED_PEAK_LIVE_CALLS"
require_positive_integer "SOAK_REQUEST_WORKERS" "$REQUEST_WORKERS"
require_positive_integer "DURATION_SEC" "$DURATION_SEC"
require_positive_integer "SOAK_POLL_INTERVAL_SEC" "$POLL_INTERVAL_SEC"
require_positive_integer "SOAK_METRICS_INTERVAL_SEC" "$METRICS_INTERVAL_SEC"
require_positive_number "SOAK_LIVE_STATUS_POLL_SEC" "$LIVE_STATUS_POLL_SEC"
require_positive_number "SOAK_DRAIN_TIMEOUT_SEC" "$DRAIN_TIMEOUT_SEC"
if [[ "$REQUIRED_PEAK_LIVE_CALLS" -gt "$REQUIRED_ORIGINATIONS" ]]; then
    echo "REQUIRED_PEAK_LIVE_CALLS cannot exceed $REQUIRED_ORIGINATIONS" >&2
    exit 2
fi

for required_command in kubectl curl mktemp ln; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
        echo "missing required command: $required_command" >&2
        exit 2
    fi
done
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "load-test Python is not executable: $PYTHON_BIN" >&2
    exit 2
fi
for required_file in infra/chaos/pod-kill.yaml infra/chaos/redis-partition.yaml "$LOADTEST_SCRIPT"; do
    if [[ ! -f "$required_file" ]]; then
        echo "missing required file: $required_file" >&2
        exit 2
    fi
done
if [[ -z "${INTERNAL_SERVICE_TOKEN:-}" ]]; then
    echo "INTERNAL_SERVICE_TOKEN must be exported for authenticated load traffic" >&2
    exit 2
fi
if [[ -z "${LOADTEST_TENANT_ID:-}" ]]; then
    echo "LOADTEST_TENANT_ID must identify the authorized test tenant" >&2
    exit 2
fi

capture_snapshot() {
    local snapshot="$1"
    local partial

    if [[ -e "$snapshot" || -L "$snapshot" ]]; then
        echo "refusing to overwrite Prometheus snapshot: $snapshot" >&2
        return 1
    fi
    if ! partial="$(mktemp "${snapshot}.partial.XXXXXX")"; then
        return 1
    fi

    if ! curl -fsS --max-time 10 --get \
        --data-urlencode "query=${PROM_METRIC}" \
        "${PROM_URL%/}/api/v1/query" \
        --output "$partial"; then
        rm -f "$partial"
        return 1
    fi

    # Prometheus returns HTTP 200 for a valid query with no series. Validate
    # the body and exact exported metric so an empty/NaN snapshot cannot be
    # mistaken for evidence.
    if ! "$PYTHON_BIN" - "$partial" "$PROM_METRIC" <<'PY'
import json
import math
import sys

path, expected_metric = sys.argv[1:]
try:
    with open(path, "r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("status") != "success":
        raise ValueError("Prometheus status is not success")
    result = payload.get("data", {}).get("result")
    if not isinstance(result, list) or not result:
        raise ValueError("Prometheus result is empty")
    for series in result:
        if series.get("metric", {}).get("__name__") != expected_metric:
            raise ValueError("Prometheus returned an unexpected metric")
        value = series.get("value")
        if not isinstance(value, list) or len(value) < 2:
            raise ValueError("Prometheus series has no sample value")
        if not math.isfinite(float(value[1])):
            raise ValueError("Prometheus sample is not finite")
except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
    print(f"invalid Prometheus snapshot: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
    then
        rm -f "$partial"
        return 1
    fi

    # A same-directory hard link is atomic and fails if another writer created
    # the final name after the existence check. Never use mv here: common mv
    # implementations replace an existing destination.
    if ! ln -- "$partial" "$snapshot"; then
        echo "refusing to overwrite Prometheus snapshot: $snapshot" >&2
        rm -f -- "$partial"
        return 1
    fi
    if ! rm -f -- "$partial"; then
        echo "could not remove Prometheus snapshot partial: $partial" >&2
        return 1
    fi
}

validate_load_evidence() {
    local evidence_path="$1"
    "$PYTHON_BIN" - "$evidence_path" "$REQUIRED_ORIGINATIONS" \
        "$REQUIRED_PEAK_LIVE_CALLS" "$DURATION_SEC" "$REQUEST_WORKERS" <<'PY'
import json
import sys

path, required_originations, required_peak, duration, request_workers = sys.argv[1:]
required_originations = int(required_originations)
required_peak = int(required_peak)
duration = int(duration)
request_workers = int(request_workers)

try:
    with open(path, "r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("schema_version") != 1:
        raise ValueError("unexpected evidence schema")
    if payload.get("passed") is not True or payload.get("exit_code") != 0:
        raise ValueError("load driver did not record a passing result")
    requirements = payload.get("requirements")
    observed = payload.get("observed")
    configuration = payload.get("configuration")
    if not all(isinstance(item, dict) for item in (requirements, observed, configuration)):
        raise ValueError("evidence sections are missing")
    if requirements.get("minimum_originated") != required_originations:
        raise ValueError("minimum-originated requirement does not match runner")
    if requirements.get("required_peak_live") != required_peak:
        raise ValueError("peak-live requirement does not match runner")
    if requirements.get("zero_live_baseline") is not True:
        raise ValueError("zero-live baseline was not required")
    if requirements.get("zero_live_drain") is not True:
        raise ValueError("zero-live drain was not required")
    if configuration.get("duration_seconds") != duration:
        raise ValueError("duration does not match runner")
    if configuration.get("request_workers") != request_workers:
        raise ValueError("request-worker count does not match runner")

    originated = observed.get("originated")
    call_ids = observed.get("originated_call_ids")
    if isinstance(originated, bool) or not isinstance(originated, int):
        raise ValueError("originated count is not an integer")
    if originated < required_originations:
        raise ValueError("fewer than 300 calls were actually originated")
    if not isinstance(call_ids, list) or len(call_ids) != originated:
        raise ValueError("unique call-id evidence does not match originated count")
    if any(not isinstance(call_id, str) or not call_id.strip() for call_id in call_ids):
        raise ValueError("call-id evidence contains an invalid value")
    if len(set(call_ids)) != len(call_ids):
        raise ValueError("call-id evidence contains duplicates")
    if observed.get("baseline_live_calls") != 0:
        raise ValueError("cluster live-call baseline was not zero")
    max_live = observed.get("max_live_calls")
    if isinstance(max_live, bool) or not isinstance(max_live, int) or max_live < required_peak:
        raise ValueError("required Redis-backed live-call peak was not observed")
    if observed.get("final_live_calls") != 0 or observed.get("drain_complete") is not True:
        raise ValueError("cluster did not drain back to zero")
    if int(observed.get("live_samples") or 0) < 3:
        raise ValueError("insufficient live-call samples")
    for field in ("queued", "request_errors", "live_status_errors", "rejected_other"):
        if observed.get(field) != 0:
            raise ValueError(f"{field} is non-zero")
    if payload.get("failures") != []:
        raise ValueError("evidence contains failure reasons")
except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
    print(f"invalid load evidence: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
}

if ! mkdir -p -- "$RESULTS_ROOT"; then
    echo "could not create soak results root: $RESULTS_ROOT" >&2
    exit 2
fi
if ! mkdir -- "$OUT"; then
    echo "refusing to reuse existing soak result directory: $OUT" >&2
    exit 2
fi

echo "soak_start ts=${STAMP} request_workers=${REQUEST_WORKERS} required_originations=${REQUIRED_ORIGINATIONS} required_peak_live=${REQUIRED_PEAK_LIVE_CALLS} duration_s=${DURATION_SEC}"

# Validate monitoring before introducing either load or chaos.
if ! capture_snapshot "$OUT/snap_0.json"; then
    echo "initial Prometheus snapshot failed or returned no ${PROM_METRIC} series" >&2
    exit 1
fi

# 1. Apply chaos manifests.
CHAOS_CLEANUP_REQUIRED=1
kubectl apply -f infra/chaos/pod-kill.yaml -n talky
kubectl apply -f infra/chaos/redis-partition.yaml -n talky

# 2. Run the load test in the background.
LOAD_EVIDENCE="$OUT/loadtest-evidence.json"
"$PYTHON_BIN" "$LOADTEST_SCRIPT" \
    --request-workers "$REQUEST_WORKERS" \
    --minimum-originated "$REQUIRED_ORIGINATIONS" \
    --required-peak-live "$REQUIRED_PEAK_LIVE_CALLS" \
    --evidence-json "$LOAD_EVIDENCE" \
    --status-poll-interval "$LIVE_STATUS_POLL_SEC" \
    --drain-timeout "$DRAIN_TIMEOUT_SEC" \
    --duration "$DURATION_SEC" \
    --base-url "$BASE_URL" \
    > "$OUT/loadtest.log" 2>&1 &
LOAD_PID=$!

# 3. Poll the load child and periodically snapshot real Prometheus metrics.
# A dead child is reaped within POLL_INTERVAL_SEC, rather than after four hours.
END="$(( $(date +%s) + DURATION_SEC ))"
NEXT_SNAPSHOT="$(( $(date +%s) + METRICS_INTERVAL_SEC ))"
i=1
SNAPSHOT_FAILURES=0
MONITOR_FAILURE=0

reap_load_if_exited() {
    if [[ -z "$LOAD_PID" ]]; then
        return 0
    fi
    if kill -0 "$LOAD_PID" >/dev/null 2>&1; then
        return 1
    fi
    if wait "$LOAD_PID"; then
        LOAD_RC=0
    else
        LOAD_RC=$?
    fi
    LOAD_PID=""
    return 0
}

while [ "$(date +%s)" -lt "$END" ]; do
    if reap_load_if_exited; then
        echo "load driver exited before monitor deadline rc=${LOAD_RC}" >&2
        if [[ "$LOAD_RC" -eq 0 && "$(date +%s)" -lt $((END - POLL_INTERVAL_SEC)) ]]; then
            echo "load driver reported success before the configured soak duration" >&2
            LOAD_RC=1
        fi
        break
    fi

    now="$(date +%s)"
    if [[ "$now" -ge "$NEXT_SNAPSHOT" ]]; then
        SNAP="$OUT/snap_${i}.json"
        if ! capture_snapshot "$SNAP"; then
            echo "snapshot $i failed or returned no ${PROM_METRIC} series" >&2
            SNAPSHOT_FAILURES=$((SNAPSHOT_FAILURES+1))
            MONITOR_FAILURE=1
            break
        fi
        i=$((i+1))
        NEXT_SNAPSHOT=$((now + METRICS_INTERVAL_SEC))
    fi

    remaining=$((END - now))
    sleep_for="$POLL_INTERVAL_SEC"
    if [[ "$remaining" -lt "$sleep_for" ]]; then
        sleep_for="$remaining"
    fi
    if [[ "$sleep_for" -gt 0 ]]; then
        sleep "$sleep_for"
    fi
done

if [[ "$MONITOR_FAILURE" -ne 0 ]]; then
    terminate_load
    LOAD_RC=1
elif [[ -n "$LOAD_PID" ]]; then
    if wait "$LOAD_PID"; then
        LOAD_RC=0
    else
        LOAD_RC=$?
    fi
    LOAD_PID=""
fi

if [[ -z "$LOAD_RC" ]]; then
    # Defensive: every path above must either reap or wait for the child.
    echo "load driver status could not be determined" >&2
    LOAD_RC=1
fi

EVIDENCE_RC=0
if [[ "$LOAD_RC" -eq 0 ]]; then
    if ! validate_load_evidence "$LOAD_EVIDENCE"; then
        echo "load evidence is missing, contradictory, or below the release thresholds" >&2
        EVIDENCE_RC=1
    fi
fi

# 4. Stop chaos. Keep the cleanup-required bit set on any uncertain delete so
# the EXIT trap retries both resources and the final result remains non-zero.
CHAOS_CLEANUP_RC=0
if ! cleanup_chaos; then
    CHAOS_CLEANUP_RC=1
fi

FINAL_RC="$LOAD_RC"
if [[ "$SNAPSHOT_FAILURES" -ne 0 || "$CHAOS_CLEANUP_RC" -ne 0 || "$EVIDENCE_RC" -ne 0 ]]; then
    FINAL_RC=1
fi

echo "soak_end ts=${STAMP} loadtest_rc=${LOAD_RC} evidence_rc=${EVIDENCE_RC} snapshot_failures=${SNAPSHOT_FAILURES} chaos_cleanup_rc=${CHAOS_CLEANUP_RC} results=${OUT}"
exit "$FINAL_RC"
