#!/usr/bin/env bash
# install-services.sh — Install Talky.ai systemd services
# Usage: sudo bash systemd/install-services.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== Talky.ai Systemd Service Installer ==="
echo ""

# 1. Symlink every service/target/timer file in this directory.
#
# backend/systemd/ is now the SINGLE source of truth for units. The second
# historical directory, backend/deploy/systemd/, held only the trunk-status
# .service + .timer; both were moved here (2026-08-27) and that directory no
# longer contains units. Add new units here, and add them to the enable list
# below — a unit that is symlinked but never enabled is on disk and dead after
# a reboot, which looks exactly like "still missing".
for unit in "$SCRIPT_DIR"/*.service "$SCRIPT_DIR"/*.target "$SCRIPT_DIR"/*.timer; do
  [ -e "$unit" ] || continue
  name="$(basename "$unit")"
  echo "  Linking $name -> $SYSTEMD_DIR/$name"
  ln -sf "$unit" "$SYSTEMD_DIR/$name"
done

# The compatibility definition keeps old symlinks resolvable during checkout.
# Disable the previous timer after linking it, before reloading definitions.
# Only the service's own loop will refresh runtime evidence after this release.
systemctl disable --now talky-trunk-status.timer

# 2. Reload systemd
echo ""
echo "  Reloading systemd daemon..."
systemctl daemon-reload

# 3. Enable all services
#
# Every active unit symlinked in step 1 must be enabled here, or it is present on disk
# but dead after a reboot — which is indistinguishable from "still missing" to
# anyone who runs this installer and moves on.
#
# Deliberate exceptions:
#   * talky-trunk-status.timer — retained only for upgrade compatibility and
#     disabled above; the long-running service replaces it.
#   * talky-migrate.service — oneshot, started explicitly by deploy_to_server.sh
#     before the app restarts. Migrations must never run just because the
#     machine booted. (See the unit's own header.)
#   * talky-cleanup.service / talky-healthwatch.service / talky-db-backup.service
#     / talky-inbound-synthetic.service — oneshots activated by their .timer,
#     which IS enabled below.
#   * talky-trunk-status.service — long-running loop since 2026-10-07 (its
#     15-second timer was retired), enabled directly.
echo "  Enabling services..."
systemctl enable talky-api.service
systemctl enable talky-voice-worker.service
systemctl enable talky-dialer-worker.service
systemctl enable talky-reminder-worker.service
systemctl enable talky-voice-gateway.service   # C++ media gateway; see the unit's header
systemctl enable talky-cleanup.timer   # activates talky-cleanup.service nightly
systemctl enable talky-healthwatch.timer   # activates talky-healthwatch.service every 2 min
systemctl enable talky-trunk-status.service  # refreshes runtime SIP evidence every 10 sec (loop)
systemctl enable talky-inbound-synthetic.timer  # hourly carrier-hairpin proof
systemctl enable talky-db-backup.timer  # nightly verified pg_dump (deploy/db-backup.sh)
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
