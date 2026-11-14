#!/usr/bin/env bash
# install-services.sh — Install Talky.ai systemd services
# Usage: sudo bash systemd/install-services.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_DIR="/etc/systemd/system"

echo "=== Talky.ai Systemd Service Installer ==="
echo ""

# Keep rebuilds on the same journal budget as the existing host. install -D
# creates the drop-in directory and gives the non-secret policy an explicit mode.
install -D -m 0644 "$SCRIPT_DIR/../deploy/infra/systemd/journald-talky.conf" \
  /etc/systemd/journald.conf.d/60-talky.conf
systemctl restart systemd-journald.service

# Apply the daemon priority on its next normal start; never restart Docker
# from an application deploy, because it owns the live database containers.
install -D -m 0644 "$SCRIPT_DIR/../deploy/infra/systemd/docker-oom.conf" \
  /etc/systemd/system/docker.service.d/60-talky-oom.conf

# Boot-order and restart drop-ins (sources: deploy/infra/systemd/). Without them
# the Python services can start before Postgres/Redis after a reboot, and
# the gateway and nginx stay down after a crash. install overwrites, so a re-run
# is idempotent. The deps drop-in runs /opt/talky-infra/bin/wait-for-deps, so
# that root-owned helper is installed first (same as prepare-service-user.sh).
install -D -m 0755 "$SCRIPT_DIR/../deploy/infra/wait-for-deps" /opt/talky-infra/bin/wait-for-deps
for unit in talky-api talky-api-web talky-dialer-worker talky-voice-worker talky-reminder-worker; do
  install -D -m 0644 "$SCRIPT_DIR/../deploy/infra/systemd/talky-deps.conf" \
    "/etc/systemd/system/$unit.service.d/20-talky-deps.conf"
done
install -D -m 0644 "$SCRIPT_DIR/../deploy/infra/systemd/talky-restart.conf" \
  /etc/systemd/system/talky-voice-gateway.service.d/20-talky-restart.conf
install -D -m 0644 "$SCRIPT_DIR/../deploy/infra/systemd/nginx-restart.conf" \
  /etc/systemd/system/nginx.service.d/20-talky-restart.conf

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

# The selected recovery tool remains in scripts/database. Install only its
# read-only health pair; physical/PITR schedules require separate owner setup.
for name in talklee-db-backup-health.service talklee-db-backup-health.timer; do
  ln -sf "$SCRIPT_DIR/../../scripts/database/systemd/$name" "$SYSTEMD_DIR/$name"
done

# The compatibility definition keeps old symlinks resolvable during checkout.
# Disable the previous timer after linking it, before reloading definitions.
# Only the service's own loop will refresh runtime evidence after this release.
systemctl disable --now talky-trunk-status.timer

# Deliberate operator test: dry-run unless --send is explicitly supplied.
ln -sf "$SCRIPT_DIR/../deploy/talky-alert-test" /usr/local/bin/talky-alert-test

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
#   * talky-notify@.service — instantiated only by OnFailure, never enabled as
#     a daemon and deliberately has no recursive failure notification.
#   * talky-trunk-status.timer — retained only for upgrade compatibility and
#     disabled above; the long-running service replaces it.
#   * talky-migrate.service — oneshot, started explicitly by deploy_to_server.sh
#     before the app restarts. Migrations must never run just because the
#     machine booted. (See the unit's own header.)
#   * talky-cleanup.service / talky-healthwatch.service / talky-db-backup.service
#     / talky-inbound-synthetic.service / talky-support-jobs.service — oneshots
#     activated by their .timer,
#     which IS enabled below.
#   * talky-trunk-status.service — long-running loop since 2026-10-07 (its
#     15-second timer was retired), enabled directly.
echo "  Enabling services..."
systemctl enable talky-api.service
systemctl enable talky-api-web.service   # dashboard API, telephony off (2026-10-07)
systemctl enable talky-voice-worker.service
systemctl enable talky-dialer-worker.service
systemctl enable talky-reminder-worker.service
systemctl enable talky-voice-gateway.service   # C++ media gateway; see the unit's header
systemctl enable talky-cleanup.timer   # activates talky-cleanup.service nightly
systemctl enable talky-recording-discrepancy.timer --now  # read-only file/row reconciliation nightly
systemctl enable talky-model-catalog.timer --now  # nightly credential-scoped saved-model report
systemctl enable talky-healthwatch.timer   # activates bounded operational checks every minute
systemctl enable talky-trunk-status.service  # refreshes runtime SIP evidence every 10 sec (loop)
systemctl enable talky-inbound-synthetic.timer  # hourly carrier-hairpin proof
systemctl enable talky-db-backup.timer  # nightly verified pg_dump (deploy/db-backup.sh)
systemctl enable talklee-db-backup-health.timer --now  # actual local/remote integrity + age; unconfigured offhost alerts
systemctl enable talky-support-jobs.timer  # support deadlines every 15 min + hourly housekeeping (no-op while SUPPORT_ENABLED=false)
systemctl enable --now talky-asterisk-reconcile.timer # applies website line/number/inbound changes to Asterisk, every minute, only on drift
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
