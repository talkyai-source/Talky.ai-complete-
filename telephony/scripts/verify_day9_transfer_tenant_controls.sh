#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$TELEPHONY_ROOT/.." && pwd)"
COMPOSE_FILE="$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony.example}"
DOC_ROOT="$TELEPHONY_ROOT/docs/phase_3"
EVIDENCE_DIR="$DOC_ROOT/evidence/day9"
mkdir -p "$EVIDENCE_DIR"

VERIFIER_OUT="$EVIDENCE_DIR/day9_verifier_output.txt"
exec > >(tee "$VERIFIER_OUT") 2>&1

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

compose_cmd=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

SIP_PORT="${OPENSIPS_SIP_PORT:-15060}"
EXTENSION="${DAY5_TEST_EXTENSION:-750}"
ARI_HOST="${ASTERISK_ARI_HOST:-127.0.0.1}"
ARI_PORT="${ASTERISK_ARI_PORT:-8088}"
ARI_USERNAME="${ASTERISK_ARI_USERNAME:-day5}"
ARI_PASSWORD="${ASTERISK_ARI_PASSWORD:-day5_local_only_change_me}"
ARI_APP="${ASTERISK_ARI_APP:-talky_day5}"

GATEWAY_DIR="$REPO_ROOT/services/voice-gateway-cpp"
GATEWAY_BUILD_DIR="$GATEWAY_DIR/build"
GATEWAY_HOST="127.0.0.1"
GATEWAY_HTTP_PORT="18080"
GATEWAY_BASE_URL="http://${GATEWAY_HOST}:${GATEWAY_HTTP_PORT}"

PYTHON_BIN="${DAY9_PYTHON_BIN:-$REPO_ROOT/backend/venv/bin/python}"
BATCHES="${DAY9_BATCHES:-2}"
CALLS_PER_BATCH="${DAY9_CALLS_PER_BATCH:-3}"
TENANT_ID="${DAY9_TENANT_ID:-day9-default}"
TENANT_MAX_ACTIVE_CALLS="${DAY9_TENANT_MAX_ACTIVE_CALLS:-2}"
TENANT_MAX_TRANSFER_INFLIGHT="${DAY9_TENANT_MAX_TRANSFER_INFLIGHT:-1}"
TRANSFER_DELAY_SECONDS="${DAY9_TRANSFER_DELAY_SECONDS:-1.2}"
TRANSFER_ENDPOINT="${DAY9_TRANSFER_ENDPOINT:-Local/blind_target@wsm-synthetic}"
MIN_TRANSFER_SUCCESSES="${DAY9_MIN_TRANSFER_SUCCESSES:-2}"
MIN_REMOTE_BYE_CALLS="${DAY9_MIN_REMOTE_BYE_CALLS:-0}"
MIN_CONCURRENCY_REJECTS="${DAY9_MIN_CONCURRENCY_REJECTS:-1}"

TOTAL_CALLS=$((BATCHES * CALLS_PER_BATCH))
MAX_STARTED_PER_BATCH="$CALLS_PER_BATCH"
if (( TENANT_MAX_ACTIVE_CALLS < MAX_STARTED_PER_BATCH )); then
  MAX_STARTED_PER_BATCH="$TENANT_MAX_ACTIVE_CALLS"
fi
EXPECTED_COMPLETED_CALLS="${DAY9_EXPECTED_COMPLETED_CALLS:-$((BATCHES * MAX_STARTED_PER_BATCH))}"
if (( EXPECTED_COMPLETED_CALLS < 1 )); then
  EXPECTED_COMPLETED_CALLS=1
fi

PROBE_RESULTS="$EVIDENCE_DIR/day9_transfer_batch_results.json"
PROBE_SUMMARY="$EVIDENCE_DIR/day9_transfer_probe_summary.json"
TRANSFER_SUMMARY="$EVIDENCE_DIR/day9_transfer_reason_summary.json"
CONCURRENCY_REPORT="$EVIDENCE_DIR/day9_concurrency_events.json"
POLICY_SNAPSHOT="$EVIDENCE_DIR/day9_concurrency_policy_snapshot.json"
GHOST_REPORT="$EVIDENCE_DIR/day9_ghost_session_report.json"
GATEWAY_LOG="$EVIDENCE_DIR/day9_gateway_runtime.log"
ARI_TRACE="$EVIDENCE_DIR/day9_ari_event_trace.log"
ARI_BASELINE_JSON="$EVIDENCE_DIR/day9_ari_baseline_state.json"
ARI_POST_JSON="$EVIDENCE_DIR/day9_ari_post_state.json"
GATEWAY_STATS_JSON="$EVIDENCE_DIR/day9_gateway_stats.json"
EVIDENCE_DOC="$DOC_ROOT/day9_transfer_tenant_controls_evidence.md"
GATEWAY_SESSION_REPORT="$EVIDENCE_DIR/day9_gateway_session_report.json"

GW_PID=""
ARI_PID=""

cleanup() {
  set +e
  if [[ -n "$ARI_PID" ]] && ps -p "$ARI_PID" >/dev/null 2>&1; then
    kill "$ARI_PID" >/dev/null 2>&1 || true
    wait "$ARI_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "$GW_PID" ]] && ps -p "$GW_PID" >/dev/null 2>&1; then
    kill "$GW_PID" >/dev/null 2>&1 || true
    wait "$GW_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[1/16] Verifying Python runtime for Day 9 probe..."
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] Python interpreter not found: $PYTHON_BIN"
  echo "Set DAY9_PYTHON_BIN to a Python with requests + websockets installed."
  exit 1
fi
"$PYTHON_BIN" - <<'PY'
import importlib.util
mods = ["requests", "websockets"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
    raise SystemExit(f"Missing required modules: {', '.join(missing)}")
print("python dependency check: ok")
PY

echo "[2/16] Ensuring required telephony containers are running..."
"${compose_cmd[@]}" up -d --no-recreate asterisk rtpengine opensips

echo "[3/16] Reloading Asterisk ARI/http config..."
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "core show uptime seconds" >/dev/null
docker exec -i talky-asterisk sh -lc "cat > /etc/asterisk/http.conf" < "$TELEPHONY_ROOT/asterisk/conf/http.conf"
docker exec -i talky-asterisk sh -lc "cat > /etc/asterisk/ari.conf" < "$TELEPHONY_ROOT/asterisk/conf/ari.conf"
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "core reload" >/dev/null
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_http_websocket.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_ari.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "module load res_ari_channels.so" >/dev/null || true
"${compose_cmd[@]}" exec -T asterisk asterisk -rx "http show status" | grep -qi "Server Enabled and Bound to"

echo "[4/16] Verifying ARI API reachability..."
"$PYTHON_BIN" - <<PY
import requests
url = "http://${ARI_HOST}:${ARI_PORT}/ari/asterisk/info"
resp = requests.get(url, params={"api_key": "${ARI_USERNAME}:${ARI_PASSWORD}"}, timeout=5)
if resp.status_code != 200:
    raise SystemExit(f"ARI ping failed: {resp.status_code} {resp.text[:300]}")
print("ari ping: ok")
PY

echo "[5/16] Capturing ARI baseline state..."
"$PYTHON_BIN" - <<PY
import json
import requests
from pathlib import Path
api = {"api_key": "${ARI_USERNAME}:${ARI_PASSWORD}"}
base = "http://${ARI_HOST}:${ARI_PORT}/ari"
channels = requests.get(f"{base}/channels", params=api, timeout=5).json()
bridges = requests.get(f"{base}/bridges", params=api, timeout=5).json()
payload = {
    "external_channel_ids": sorted(str(ch.get("id") or "") for ch in channels if str(ch.get("name") or "").startswith("UnicastRTP/")),
    "bridge_ids": sorted(str(b.get("id") or "") for b in bridges),
}
Path("$ARI_BASELINE_JSON").write_text(json.dumps(payload, indent=2), encoding="utf-8")
print("ari baseline capture: ok")
PY

echo "[6/16] Building C++ voice gateway and running unit tests..."
cmake -S "$GATEWAY_DIR" -B "$GATEWAY_BUILD_DIR" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$GATEWAY_BUILD_DIR" -- -j"$(nproc)" >/dev/null
ctest --test-dir "$GATEWAY_BUILD_DIR" --output-on-failure >/dev/null

echo "[7/16] Starting C++ voice gateway runtime..."
"$GATEWAY_BUILD_DIR/voice_gateway" --host "$GATEWAY_HOST" --port "$GATEWAY_HTTP_PORT" >"$GATEWAY_LOG" 2>&1 &
GW_PID=$!
for _ in $(seq 1 80); do
  if curl -fsS "$GATEWAY_BASE_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
curl -fsS "$GATEWAY_BASE_URL/health" >/dev/null

echo "[8/16] Starting ARI external media controller (Day 9 transfer mode, expected_completed_calls=${EXPECTED_COMPLETED_CALLS})..."
"$PYTHON_BIN" "$SCRIPT_DIR/day5_ari_external_media_controller.py" \
  --ari-host "$ARI_HOST" \
  --ari-port "$ARI_PORT" \
  --ari-username "$ARI_USERNAME" \
  --ari-password "$ARI_PASSWORD" \
  --app-name "$ARI_APP" \
  --gateway-base-url "$GATEWAY_BASE_URL" \
  --gateway-rtp-ip "127.0.0.1" \
  --gateway-rtp-port-start 32000 \
  --gateway-rtp-port-end 32999 \
  --max-completed-calls "$EXPECTED_COMPLETED_CALLS" \
  --idle-timeout-seconds 60 \
  --gateway-echo-enabled 0 \
  --blind-transfer-enabled 1 \
  --blind-transfer-endpoint "$TRANSFER_ENDPOINT" \
  --blind-transfer-use-continue 1 \
  --blind-transfer-delay-seconds "$TRANSFER_DELAY_SECONDS" \
  --tenant-id "$TENANT_ID" \
  --tenant-max-active-calls "$TENANT_MAX_ACTIVE_CALLS" \
  --tenant-max-transfer-inflight "$TENANT_MAX_TRANSFER_INFLIGHT" >"$ARI_TRACE" 2>&1 &
ARI_PID=$!

for _ in $(seq 1 100); do
  if grep -q '"event": "controller_started"' "$ARI_TRACE" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if ! grep -q '"event": "controller_started"' "$ARI_TRACE" 2>/dev/null; then
  echo "[ERROR] Controller did not start correctly."
  tail -n 80 "$ARI_TRACE" || true
  exit 1
fi

echo "[9/16] Running Day 9 transfer probe (batches=${BATCHES}, calls_per_batch=${CALLS_PER_BATCH})..."
"$PYTHON_BIN" "$SCRIPT_DIR/day9_transfer_tenant_probe.py" \
  --host 127.0.0.1 \
  --port "$SIP_PORT" \
  --extension "$EXTENSION" \
  --bind-ip 127.0.0.1 \
  --batches "$BATCHES" \
  --calls-per-batch "$CALLS_PER_BATCH" \
  --require-remote-bye 0 \
  --min-success-calls "$MIN_TRANSFER_SUCCESSES" \
  --min-remote-bye-calls "$MIN_REMOTE_BYE_CALLS" \
  --output-results "$PROBE_RESULTS" \
  --output-summary "$PROBE_SUMMARY"

echo "[10/16] Waiting for ARI controller completion..."
deadline=$((SECONDS + 120))
while ps -p "$ARI_PID" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "[ERROR] Controller did not finish within timeout."
    tail -n 120 "$ARI_TRACE" || true
    exit 1
  fi
  sleep 1
done
wait "$ARI_PID"
ari_exit=$?
ARI_PID=""
if [[ "$ari_exit" -ne 0 ]]; then
  echo "[ERROR] Controller exited with code $ari_exit"
  tail -n 160 "$ARI_TRACE" || true
  exit 1
fi

echo "[11/16] Validating transfer and concurrency events from ARI trace..."
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path

trace = Path("$ARI_TRACE")
events = []
for line in trace.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        events.append(json.loads(line))
    except json.JSONDecodeError:
        continue

counts = {
    "transfer_redirect_dispatched": 0,
    "transfer_continue_dispatched": 0,
    "transfer_failed": 0,
    "call_rejected_concurrency": 0,
    "transfer_rejected_concurrency": 0,
}
controller_finished = None
for event in events:
    et = str(event.get("event") or "")
    if et in counts:
        counts[et] += 1
    if et == "controller_finished":
        controller_finished = event

transfer_success_total = counts["transfer_redirect_dispatched"] + counts["transfer_continue_dispatched"]
reject_total = counts["call_rejected_concurrency"] + counts["transfer_rejected_concurrency"]

summary = {
    "counts": counts,
    "transfer_success_total": transfer_success_total,
    "concurrency_reject_total": reject_total,
    "controller_finished": controller_finished or {},
}
Path("$TRANSFER_SUMMARY").write_text(json.dumps(summary, indent=2), encoding="utf-8")
Path("$CONCURRENCY_REPORT").write_text(json.dumps({
    "call_rejected_concurrency": counts["call_rejected_concurrency"],
    "transfer_rejected_concurrency": counts["transfer_rejected_concurrency"],
    "concurrency_reject_total": reject_total,
    "tenant_id": "${TENANT_ID}",
    "tenant_max_active_calls": int("${TENANT_MAX_ACTIVE_CALLS}"),
    "tenant_max_transfer_inflight": int("${TENANT_MAX_TRANSFER_INFLIGHT}"),
}, indent=2), encoding="utf-8")
Path("$POLICY_SNAPSHOT").write_text(json.dumps({
    "tenant_id": "${TENANT_ID}",
    "tenant_max_active_calls": int("${TENANT_MAX_ACTIVE_CALLS}"),
    "tenant_max_transfer_inflight": int("${TENANT_MAX_TRANSFER_INFLIGHT}"),
    "transfer_endpoint": "${TRANSFER_ENDPOINT}",
}, indent=2), encoding="utf-8")

if transfer_success_total < int("${MIN_TRANSFER_SUCCESSES}"):
    raise SystemExit(f"transfer_success_total={transfer_success_total} < min={int('${MIN_TRANSFER_SUCCESSES}')}")
if counts["transfer_failed"] != 0:
    raise SystemExit(f"transfer_failed must be 0, got {counts['transfer_failed']}")
if reject_total < int("${MIN_CONCURRENCY_REJECTS}"):
    raise SystemExit(f"concurrency_reject_total={reject_total} < min={int('${MIN_CONCURRENCY_REJECTS}')}")

print("ari transfer/concurrency validation: ok")
PY

echo "[12/16] Capturing gateway stats and enforcing no active sessions..."
curl -fsS "$GATEWAY_BASE_URL/stats" >"$GATEWAY_STATS_JSON"
"$PYTHON_BIN" - <<PY
import json
import requests
from pathlib import Path

sessions = requests.get("${GATEWAY_BASE_URL}/v1/sessions", timeout=5).json().get("sessions", [])
active = [s for s in sessions if str(s.get("state") or "") not in {"stopped", "failed"}]
report = {
    "active_session_count": len(active),
    "active_session_ids": [str(s.get("session_id") or "") for s in active],
}
Path("$GATEWAY_SESSION_REPORT").write_text(json.dumps(report, indent=2), encoding="utf-8")
if active:
    raise SystemExit(f"Gateway active sessions not cleaned up: {report}")
print("gateway cleanup: ok")
PY

echo "[13/16] Capturing ARI post state and leak report..."
"$PYTHON_BIN" - <<PY
import json
import requests
from pathlib import Path

api = {"api_key": "${ARI_USERNAME}:${ARI_PASSWORD}"}
base = "http://${ARI_HOST}:${ARI_PORT}/ari"
baseline = json.loads(Path("$ARI_BASELINE_JSON").read_text(encoding="utf-8"))
gateway = json.loads(Path("$GATEWAY_SESSION_REPORT").read_text(encoding="utf-8"))
channels = requests.get(f"{base}/channels", params=api, timeout=5).json()
bridges = requests.get(f"{base}/bridges", params=api, timeout=5).json()
post = {
    "external_channel_ids": sorted(str(ch.get("id") or "") for ch in channels if str(ch.get("name") or "").startswith("UnicastRTP/")),
    "bridge_ids": sorted(str(b.get("id") or "") for b in bridges),
}
Path("$ARI_POST_JSON").write_text(json.dumps(post, indent=2), encoding="utf-8")

baseline_external = set(baseline.get("external_channel_ids", []))
baseline_bridges = set(baseline.get("bridge_ids", []))
post_external = set(post.get("external_channel_ids", []))
post_bridges = set(post.get("bridge_ids", []))

leaked_external = sorted(post_external - baseline_external)
leaked_bridges = sorted(post_bridges - baseline_bridges)

report = {
    "gateway": gateway,
    "baseline": baseline,
    "post": post,
    "leaked_external_channel_ids": leaked_external,
    "leaked_bridge_ids": leaked_bridges,
}
Path("$GHOST_REPORT").write_text(json.dumps(report, indent=2), encoding="utf-8")
if leaked_external or leaked_bridges:
    raise SystemExit(
        "ARI leak detected: "
        f"external={leaked_external} bridges={leaked_bridges}"
    )
print("ari cleanup: ok")
PY

echo "[14/16] Validating probe summary contract..."
"$PYTHON_BIN" - <<PY
import json
from pathlib import Path
summary = json.loads(Path("$PROBE_SUMMARY").read_text(encoding="utf-8"))
if int(summary.get("success", 0)) < int("${MIN_TRANSFER_SUCCESSES}"):
    raise SystemExit(f"Insufficient transfer successes in probe summary: {summary}")
if int(summary.get("remote_bye_total", 0)) < int("${MIN_REMOTE_BYE_CALLS}"):
    raise SystemExit(f"Insufficient remote BYE count in probe summary: {summary}")
print("probe summary validation: ok")
PY

echo "[15/16] Writing Day 9 evidence markdown..."
DAY9_PROBE_SUMMARY_PATH="$PROBE_SUMMARY" \
DAY9_TRANSFER_SUMMARY_PATH="$TRANSFER_SUMMARY" \
DAY9_EVIDENCE_DOC_PATH="$EVIDENCE_DOC" \
"$PYTHON_BIN" - <<'PY'
import json
import os
from pathlib import Path
from datetime import UTC, datetime

probe = json.loads(Path(os.environ["DAY9_PROBE_SUMMARY_PATH"]).read_text(encoding="utf-8"))
transfer = json.loads(Path(os.environ["DAY9_TRANSFER_SUMMARY_PATH"]).read_text(encoding="utf-8"))

content = f"""# Day 9 Transfer + Tenant Controls Evidence

Date: {datetime.now(UTC).strftime('%Y-%m-%d')}
Verifier: `telephony/scripts/verify_day9_transfer_tenant_controls.sh`

## Summary

1. Calls attempted: {probe.get("calls")}
2. Successful transfer call outcomes: {probe.get("success")}
3. Remote BYE confirmations: {probe.get("remote_bye_total")}
4. Transfer dispatch count: {transfer.get("transfer_success_total")}
5. Concurrency reject count: {transfer.get("concurrency_reject_total")}

## Acceptance

1. Blind transfer repeated outcomes: PASS
2. Tenant concurrency rejection behavior: PASS
3. Ghost session/bridge leak check: PASS

## Evidence Files

1. `telephony/docs/phase_3/evidence/day9/day9_verifier_output.txt`
2. `telephony/docs/phase_3/evidence/day9/day9_transfer_batch_results.json`
3. `telephony/docs/phase_3/evidence/day9/day9_transfer_probe_summary.json`
4. `telephony/docs/phase_3/evidence/day9/day9_transfer_reason_summary.json`
5. `telephony/docs/phase_3/evidence/day9/day9_concurrency_events.json`
6. `telephony/docs/phase_3/evidence/day9/day9_concurrency_policy_snapshot.json`
7. `telephony/docs/phase_3/evidence/day9/day9_ari_event_trace.log`
8. `telephony/docs/phase_3/evidence/day9/day9_gateway_runtime.log`
9. `telephony/docs/phase_3/evidence/day9/day9_gateway_session_report.json`
10. `telephony/docs/phase_3/evidence/day9/day9_ghost_session_report.json`
"""
Path(os.environ["DAY9_EVIDENCE_DOC_PATH"]).write_text(content, encoding="utf-8")
print("evidence markdown: ok")
PY

echo "[16/16] Day 9 verifier complete."
echo "Day 9 verification PASSED."
