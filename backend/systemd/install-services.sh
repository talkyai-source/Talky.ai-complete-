#!/usr/bin/env bash
# install-services.sh — Install Talky.ai systemd services
# Usage: sudo bash systemd/install-services.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== Talky.ai Systemd Service Installer ==="
echo ""

# 1. Symlink all service/target/timer files
for unit in "$SCRIPT_DIR"/*.service "$SCRIPT_DIR"/*.target "$SCRIPT_DIR"/*.timer; do
    name="$(basename "$unit")"
    echo "  Linking $name -> $SYSTEMD_DIR/$name"
    ln -sf "$unit" "$SYSTEMD_DIR/$name"
done

# 2. Reload systemd
echo ""
echo "  Reloading systemd daemon..."
systemctl daemon-reload

# 3. Enable all services
#
# Every unit symlinked in step 1 must be enabled here, or it is present on disk
# but dead after a reboot — which is indistinguishable from "still missing" to
# anyone who runs this installer and moves on.
echo "  Enabling services..."
systemctl enable talky-api.service
systemctl enable talky-voice-worker.service
systemctl enable talky-dialer-worker.service
systemctl enable talky-reminder-worker.service
systemctl enable talky-voice-gateway.service   # C++ media gateway; see the unit's header
systemctl enable talky-cleanup.timer   # activates talky-cleanup.service nightly
systemctl enable talky-healthwatch.timer   # activates talky-healthwatch.service every 2 min
systemctl enable talky.target

# NOTE (F-4): talky-trunk-status.{service,timer} live in backend/deploy/systemd/,
# a second systemd directory this installer does not read. Until the two
# directories are consolidated, install those two units by hand:
#   ln -sf "$SCRIPT_DIR/../deploy/systemd/talky-trunk-status.service" "$SYSTEMD_DIR/"
#   ln -sf "$SCRIPT_DIR/../deploy/systemd/talky-trunk-status.timer"   "$SYSTEMD_DIR/"
#   systemctl daemon-reload && systemctl enable --now talky-trunk-status.timer

echo ""
echo "=== Installation complete ==="
echo ""
echo "Usage:"
echo "  sudo systemctl start talky.target      # Start all services"
echo "  sudo systemctl stop talky.target       # Stop all services"
echo "  sudo systemctl status talky-api        # Check API status"
echo "  journalctl -u talky-voice -f           # Follow voice worker logs"
echo ""
