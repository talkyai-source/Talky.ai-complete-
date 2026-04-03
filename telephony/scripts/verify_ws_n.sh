#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${1:-$TELEPHONY_ROOT/deploy/docker/.env.telephony.example}"
EVIDENCE_DIR="$TELEPHONY_ROOT/docs/phase_3/evidence/ws_n"
PHASE3_CHECKLIST="$TELEPHONY_ROOT/docs/phase_3/02_phase_three_gated_checklist.md"
PHASE3_PLAN="$TELEPHONY_ROOT/docs/phase_3/12_ws_n_failure_injection_recovery_plan.md"
PHASE3_REPORT="$TELEPHONY_ROOT/docs/phase_3/13_ws_n_failure_recovery_report.md"
PROM_RULES="$TELEPHONY_ROOT/observability/prometheus/rules/telephony_ws_k_rules.yml"
ALERTMANAGER_CFG="$TELEPHONY_ROOT/observability/alertmanager/alertmanager.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] Missing env file: $ENV_FILE"
  exit 1
fi

mkdir -p "$EVIDENCE_DIR"

echo "[1/10] Running WS-M verifier (WS-N prerequisite)..."
"$SCRIPT_DIR/verify_ws_m.sh" "$ENV_FILE"

echo "[2/10] Validating WS-N script inventory..."
for script in \
  "failure_drill_opensips.sh" \
  "failure_drill_rtpengine.sh" \
  "failure_drill_freeswitch_backup.sh" \
  "failure_drill_combined.sh" \
  "verify_ws_n.sh"; do
  if [[ ! -x "$SCRIPT_DIR/$script" ]]; then
    echo "[ERROR] Missing executable WS-N script: $SCRIPT_DIR/$script"
    exit 1
  fi
done

echo "[3/10] Validating WS-N docs/checklist markers..."
for path in "$PHASE3_PLAN" "$PHASE3_REPORT" "$PHASE3_CHECKLIST"; do
  if [[ ! -f "$path" ]]; then
    echo "[ERROR] Missing WS-N doc artifact: $path"
    exit 1
  fi
done
for marker in \
  "WS-N Gate: Failure Injection and Automated Recovery" \
  "OpenSIPS failure drill completed." \
  "rtpengine degradation drill completed." \
  "FreeSWITCH disruption drill completed." \
  "WS-N Plan: Failure Injection and Automated Recovery"; do
  if ! rg -n "$marker" "$PHASE3_CHECKLIST" "$PHASE3_PLAN" >/dev/null; then
    echo "[ERROR] Missing WS-N marker: $marker"
    exit 1
  fi
done

echo "[4/10] Validating alerting signal-quality baseline markers..."
for marker in \
  "group_by:" \
  "inhibit_rules:" \
  "team=\"telephony\"" \
  "TalkyTelephonyMetricsScrapeFailed"; do
  if ! rg -n "$marker" "$ALERTMANAGER_CFG" "$PROM_RULES" >/dev/null; then
    echo "[ERROR] Missing alert quality marker: $marker"
    exit 1
  fi
done

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[5/10] Running N1 OpenSIPS outage drill..."
  "$SCRIPT_DIR/failure_drill_opensips.sh" "$ENV_FILE" "$EVIDENCE_DIR"

  echo "[6/10] Running N2 RTPengine degradation drill..."
  "$SCRIPT_DIR/failure_drill_rtpengine.sh" "$ENV_FILE" "$EVIDENCE_DIR"

  echo "[7/10] Running N3 FreeSWITCH backup disruption drill..."
  "$SCRIPT_DIR/failure_drill_freeswitch_backup.sh" "$ENV_FILE" "$EVIDENCE_DIR"

  if [[ "${WS_N_RUN_COMBINED:-0}" == "1" ]]; then
    echo "[8/10] Running optional N4 combined drill..."
    "$SCRIPT_DIR/failure_drill_combined.sh" "$ENV_FILE" "$EVIDENCE_DIR"
  else
    echo "[8/10] Optional N4 combined drill skipped (set WS_N_RUN_COMBINED=1 to run)"
  fi

  echo "[9/10] Validating WS-N drill result contracts..."
  python3 - "$EVIDENCE_DIR" "${WS_N_RUN_COMBINED:-0}" <<'PY'
import json
import sys
from pathlib import Path

base = Path(sys.argv[1])
run_combined = sys.argv[2] == "1"

required = [
    ("n1_opensips_result.json", "N1"),
    ("n2_rtpengine_result.json", "N2"),
    ("n3_freeswitch_backup_result.json", "N3"),
]
if run_combined:
    required.append(("n4_combined_result.json", "N4"))

for filename, drill in required:
    path = base / filename
    if not path.exists():
        raise SystemExit(f"Missing result file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("drill_id") != drill:
        raise SystemExit(f"{path}: unexpected drill_id {payload.get('drill_id')} (expected {drill})")
    if payload.get("status") != "passed":
        raise SystemExit(f"{path}: status is not passed ({payload.get('status')})")
    for key in ("outage_seconds", "recovery_seconds"):
        if not isinstance(payload.get(key), int):
            raise SystemExit(f"{path}: {key} must be integer")
    timeline = base / filename.replace("_result.json", "_timeline.log")
    if not timeline.exists():
        raise SystemExit(f"Missing timeline file: {timeline}")
print("WS-N result contract validation passed")
PY
else
  echo "[5/10] Docker unavailable/inaccessible: live WS-N drills skipped"
  echo "[6/10] Docker unavailable/inaccessible: live WS-N drills skipped"
  echo "[7/10] Docker unavailable/inaccessible: live WS-N drills skipped"
  echo "[8/10] Docker unavailable/inaccessible: live WS-N drills skipped"
  echo "[9/10] Docker unavailable/inaccessible: live WS-N drills skipped"
fi

echo "[10/10] WS-N verification complete"
echo
echo "WS-N verification PASSED."

