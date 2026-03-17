#!/usr/bin/env bash
# notify.sh — keepalived state-change notification hook
#
# keepalived calls this script whenever the VRRP instance changes state.
# Use it to: send alerts, reload configs, update monitoring, etc.
#
# Arguments: MASTER | BACKUP | FAULT
#
# Deployment: copy to /etc/keepalived/notify.sh on both SBC hosts.

set -euo pipefail

STATE="$1"
HOSTNAME="$(hostname)"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
LOGFILE="/var/log/keepalived-notify.log"

log() {
    echo "${TIMESTAMP} [${HOSTNAME}] SBC_HA state → ${STATE}: $*" | tee -a "$LOGFILE"
}

case "$STATE" in
    MASTER)
        log "This node is now ACTIVE SBC — VIP is held here."
        # Optional: reload dispatcher list so this SBC starts accepting calls
        # opensips-cli -x mi ds_reload
        ;;
    BACKUP)
        log "This node is now STANDBY — VIP moved to peer."
        ;;
    FAULT)
        log "FAULT detected — SBC health check failed on this node."
        ;;
    *)
        log "Unknown state: ${STATE}"
        ;;
esac

# Optional: POST to a monitoring endpoint
# MONITOR_URL="${MONITOR_URL:-}"
# if [[ -n "$MONITOR_URL" ]]; then
#     curl -sf -X POST "$MONITOR_URL" \
#         -H "Content-Type: application/json" \
#         -d "{\"host\":\"${HOSTNAME}\",\"state\":\"${STATE}\",\"ts\":\"${TIMESTAMP}\"}" \
#         || true
# fi

exit 0
