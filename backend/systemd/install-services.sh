#!/usr/bin/env bash
# install-services.sh — Install Talky.ai systemd services
# Usage: sudo bash systemd/install-services.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== Talky.ai Systemd Service Installer ==="
echo ""

# 1. Symlink all service/target files
for unit in "$SCRIPT_DIR"/*.service "$SCRIPT_DIR"/*.target; do
    name="$(basename "$unit")"
    echo "  Linking $name -> $SYSTEMD_DIR/$name"
    ln -sf "$unit" "$SYSTEMD_DIR/$name"
done

# 2. Reload systemd
echo ""
echo "  Reloading systemd daemon..."
systemctl daemon-reload

# 3. Enable all services
echo "  Enabling services..."
systemctl enable talky-api.service
systemctl enable talky-voice-worker.service
systemctl enable talky-dialer-worker.service
systemctl enable talky-reminder-worker.service
systemctl enable talky.target

echo ""
echo "=== Installation complete ==="
echo ""
echo "Usage:"
echo "  sudo systemctl start talky.target      # Start all services"
echo "  sudo systemctl stop talky.target       # Stop all services"
echo "  sudo systemctl status talky-api        # Check API status"
echo "  journalctl -u talky-voice -f           # Follow voice worker logs"
echo ""
