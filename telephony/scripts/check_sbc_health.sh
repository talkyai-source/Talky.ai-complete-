#!/usr/bin/env bash
# check_sbc_health.sh — keepalived health probe for SBC nodes
#
# Exits 0 if the local SBC is healthy (keepalived keeps/gains the VIP).
# Exits 1 if the SBC is unhealthy (keepalived demotes this node).
#
# Configuration
# -------------
# Set SBC_TYPE to "opensips" or "kamailio" before deploying.
# The script is identical on both nodes; only SBC_TYPE differs.
#
# Usage in keepalived.conf:
#   vrrp_script chk_sbc {
#       script "/etc/keepalived/check_sbc_health.sh"
#       interval 2
#       fall     2
#       rise     3
#       timeout  3
#   }

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
SBC_TYPE="${SBC_TYPE:-opensips}"   # "opensips" or "kamailio"
SIP_PORT="${SIP_PORT:-15060}"      # UDP port for the SIP OPTIONS probe
SIP_HOST="${SIP_HOST:-127.0.0.1}"  # Local SIP listen address
MI_SOCKET="${MI_SOCKET:-/tmp/opensips_fifo}"  # OpenSIPS MI FIFO path
KAMCTL_CMD="${KAMCTL_CMD:-kamctl}"             # kamailio control binary

# ── Probe function ────────────────────────────────────────────────────────────
probe_opensips() {
    # Method 1: MI FIFO uptime check (preferred — sub-millisecond, no network)
    if [[ -S "$MI_SOCKET" || -p "$MI_SOCKET" ]]; then
        if opensips-cli -x mi uptime >/dev/null 2>&1; then
            return 0
        fi
    fi

    # Method 2: SIP OPTIONS self-probe (sends a real SIP OPTIONS to loopback)
    # Requires sipsak or ngrep. Falls back gracefully if not installed.
    if command -v sipsak >/dev/null 2>&1; then
        if sipsak -s "sip:health@${SIP_HOST}:${SIP_PORT}" -o 2 -T 3 >/dev/null 2>&1; then
            return 0
        fi
        return 1
    fi

    # Method 3: Check if opensips process is alive
    if pgrep -x opensips >/dev/null 2>&1; then
        return 0
    fi

    return 1
}

probe_kamailio() {
    # Method 1: kamctl stats (preferred)
    if command -v "$KAMCTL_CMD" >/dev/null 2>&1; then
        if "$KAMCTL_CMD" stats >/dev/null 2>&1; then
            return 0
        fi
        return 1
    fi

    # Method 2: SIP OPTIONS self-probe
    if command -v sipsak >/dev/null 2>&1; then
        if sipsak -s "sip:health@${SIP_HOST}:${SIP_PORT}" -o 2 -T 3 >/dev/null 2>&1; then
            return 0
        fi
        return 1
    fi

    # Method 3: Check if kamailio process is alive
    if pgrep -x kamailio >/dev/null 2>&1; then
        return 0
    fi

    return 1
}

# ── Main ──────────────────────────────────────────────────────────────────────
case "$SBC_TYPE" in
    opensips)
        probe_opensips
        exit $?
        ;;
    kamailio)
        probe_kamailio
        exit $?
        ;;
    *)
        echo "check_sbc_health.sh: unknown SBC_TYPE=${SBC_TYPE}" >&2
        exit 1
        ;;
esac
