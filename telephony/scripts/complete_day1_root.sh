#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOC_ROOT="$TELEPHONY_ROOT/docs/phase_3"
EVIDENCE_DIR="$DOC_ROOT/evidence/day1"
mkdir -p "$EVIDENCE_DIR"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "[ERROR] This script must run as root."
  echo "Use: sudo bash telephony/scripts/complete_day1_root.sh"
  exit 1
fi

echo "[1/4] Installing Day 1 required LAN tools..."
apt-get update
apt-get install -y --no-install-recommends sngrep iftop htop

echo "[2/4] Capturing root UFW posture..."
ufw status verbose >"$EVIDENCE_DIR/day1_ufw_raw.txt" 2>&1 || true

echo "[3/4] Re-running Day 1 verifier with root context..."
bash "$SCRIPT_DIR/verify_day1_lan_setup.sh"

echo "[4/4] Day 1 root completion finished."
echo "Evidence:"
echo "  - $EVIDENCE_DIR/day1_tools_check.txt"
echo "  - $EVIDENCE_DIR/day1_ufw_raw.txt"
echo "  - $DOC_ROOT/inventory.md"
echo "  - $DOC_ROOT/ufw_status.md"
