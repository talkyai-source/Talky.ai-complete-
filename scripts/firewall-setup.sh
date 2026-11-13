#!/usr/bin/env bash
# scripts/firewall-setup.sh
# ============================================================
# Talky.ai — Production Firewall Rules (single Linux server)
# Run as root: sudo bash scripts/firewall-setup.sh
#
# What this script protects:
#   - SIP port 5060 restricted to every active carrier's signalling IPs
#   - RTP range 10000-20000 (Asterisk rtp.conf) restricted to carrier IPs
#   - PostgreSQL 5432 blocked from host interface (internal only)
#   - Redis 6379 blocked from host interface (internal only)
#   - pgAdmin 5050 blocked from host interface (SSH tunnel access)
#   - Backend API 8000 accessible only via reverse proxy (nginx/caddy)
# ============================================================
set -euo pipefail

# ── CONFIGURE THESE before running ───────────────────────────
# Add your SIP carrier IP ranges here.
# Get them from your carrier's documentation.
# Examples:
#   Vonage:  66.19.144.0/21
#   Twilio:  54.172.60.0/23, 54.244.51.0/24
CARRIER_IPS=(
  # "1.2.3.4/32"       # Your SIP carrier — replace with real IP
  # "5.6.7.0/24"       # Carrier secondary range
)

# Your admin IP(s) for SSH and maintenance access
ADMIN_IPS=(
  # "your.home.ip/32"  # Your office/home IP
)
# Optional reviewed root-owned file defining CARRIER_IPS and ADMIN_IPS arrays.
# Include ALL active tenant trunks, not just the platform carrier. Carrier RTP
# may use different addresses: add those as MEDIA_IPS (defaults to CARRIER_IPS).
if [[ -n "${TALKY_FIREWALL_CONFIG:-}" ]]; then
  [[ -f "$TALKY_FIREWALL_CONFIG" && ! -L "$TALKY_FIREWALL_CONFIG" ]] || { echo "Invalid firewall config" >&2; exit 1; }
  [[ $(stat -c %u "$TALKY_FIREWALL_CONFIG") == 0 && $(stat -c %a "$TALKY_FIREWALL_CONFIG") == 600 ]] || { echo "Firewall config must be root-owned mode 600" >&2; exit 1; }
  # shellcheck source=/dev/null
  source "$TALKY_FIREWALL_CONFIG"
fi
MEDIA_IPS=("${MEDIA_IPS[@]:-${CARRIER_IPS[@]}}")
# ─────────────────────────────────────────────────────────────

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GRN}[INFO]${NC} $*"; }
warn()  { echo -e "${YLW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Must run as root: sudo $0"

if [[ "${1:-}" == --confirm ]]; then
  systemctl is-active --quiet talky-firewall-rollback.timer || error "No pending firewall rollback"
  ufw status | grep -q '^Status: active' || error "Firewall is not active"
  systemctl stop talky-firewall-rollback.timer
  info "Firewall confirmed; automatic disable cancelled."
  exit 0
fi
[[ $# -eq 0 ]] || error "Usage: $0 [--confirm]"

# The reset below puts ufw on default-deny, and without carrier rules that
# blocks ALL inbound SIP and RTP (inbound calls fail, outbound calls lose their
# inbound audio). Refuse to run rather than silently take telephony down.
if [[ ${#CARRIER_IPS[@]} -eq 0 ]]; then
  error "No CARRIER_IPS configured. Edit this script and add your SIP carrier IPs. Refusing to continue: default-deny without carrier rules would block all inbound SIP and RTP."
fi

for tool in ufw systemd-run systemctl asterisk python3; do
  command -v "$tool" >/dev/null || error "Required tool missing: $tool (install ufw first)"
done
# Refuse an incomplete signalling inventory. These are Asterisk's resolved
# identify networks, including every loaded tenant trunk and DNS multi-address
# result. Reconcile all active DB trunks first; re-run after any carrier change.
TALKY_SIP_IDENTIFIES="$(asterisk -rx 'pjsip show identifies')"
export TALKY_SIP_IDENTIFIES
python3 - "${CARRIER_IPS[@]}" <<'PY'
import ipaddress
import os
import re
import sys

try:
    allowed = [ipaddress.ip_network(value, strict=False) for value in sys.argv[1:]]
    matches = [value for value in re.findall(r'^\s*Match:\s*(\S+)\s*$', os.environ['TALKY_SIP_IDENTIFIES'], re.M)
               if not value.startswith('<')]  # CLI's table header, not a network.
    if not matches:
        raise ValueError('No loaded carrier identify networks; reconcile Asterisk first')
    for value in matches:
        network = ipaddress.ip_network(value, strict=False)
        if not any(network.version == item.version and network.subnet_of(item) for item in allowed):
            raise ValueError(f'Loaded carrier network missing from CARRIER_IPS: {network}')
except ValueError as exc:
    sys.exit(str(exc))
PY
unset TALKY_SIP_IDENTIFIES
UFW_CONFIG_DIR="${UFW_CONFIG_DIR:-/etc/ufw}"
SIP_RATE="${SIP_RATE:-100}"
SIP_BURST="${SIP_BURST:-200}"
[[ "$SIP_RATE" =~ ^[1-9][0-9]*$ && "$SIP_BURST" =~ ^[1-9][0-9]*$ ]] || error "SIP_RATE and SIP_BURST must be positive integers"
for rules in "$UFW_CONFIG_DIR/before.rules" "$UFW_CONFIG_DIR/before6.rules"; do
  chain=ufw-before-input
  [[ "$rules" != */before6.rules ]] || chain=ufw6-before-input
  [[ -f "$rules" ]] && grep -q "^-A $chain " "$rules" || error "Missing UFW before-input chain: $rules"
done

# The reset wipes every existing rule (including hand-added ones), so keep a copy.
ufw_backup="${UFW_BACKUP_DIR:-/root}/ufw-rules-before-$(date -u +%Y%m%dT%H%M%SZ).txt"
(umask 077; ufw status numbered > "$ufw_backup" 2>&1)
cp -a "$UFW_CONFIG_DIR" "${ufw_backup%.txt}.d"
cp -p "$UFW_CONFIG_DIR/before.rules" "${ufw_backup%.txt}.before.rules"
cp -p "$UFW_CONFIG_DIR/before6.rules" "${ufw_backup%.txt}.before6.rules"
info "Saved the current ufw rules to $ufw_backup"

# Always arm a watchdog before changing rules. If SSH or telephony checks fail,
# do nothing: it disables UFW after three minutes. Confirm from a NEW SSH session.
systemctl is-active --quiet talky-firewall-rollback.timer && error "A firewall change is already awaiting confirmation"
systemd-run --unit=talky-firewall-rollback --on-active=180s --timer-property=RemainAfterElapse=no --collect "$(command -v ufw)" --force disable
trap 'ufw --force disable >/dev/null 2>&1 || true' ERR
info "Disabling ufw while constructing the replacement rules..."
ufw --force disable
info "Resetting ufw to default-deny..."
ufw --force reset

# Persistent per-source SIP flood cap, before established/loopback ACCEPTs.
# Shared carrier IPs aggregate all their tenant traffic: tune these documented
# budgets to the reviewed carrier capacity before applying in production.
for rules in "$UFW_CONFIG_DIR/before.rules" "$UFW_CONFIG_DIR/before6.rules"; do
  chain=ufw-before-input
  [[ "$rules" != */before6.rules ]] || chain=ufw6-before-input
  sed -i '/^# BEGIN TALKY SIP LIMIT$/,/^# END TALKY SIP LIMIT$/d' "$rules"
  awk -v rate="$SIP_RATE" -v burst="$SIP_BURST" -v chain="$chain" \
      -v carriers="${CARRIER_IPS[*]}" -v media="${MEDIA_IPS[*]}" '
    function allow_sources(networks, port, addresses, count, i) {
      count=split(networks, addresses, " ")
      for (i=1; i<=count; i++) {
        if ((index(addresses[i], ":") > 0) == (chain == "ufw6-before-input"))
          print "-A " chain " ! -i lo -p udp --dport " port " -s " addresses[i] " -j ACCEPT"
      }
    }
    !inserted && $1 == "-A" && $2 == chain {
      print "# BEGIN TALKY SIP LIMIT"
      print "-A " chain " -p udp --dport 5060 -m hashlimit --hashlimit-above " rate "/second --hashlimit-burst " burst " --hashlimit-mode srcip --hashlimit-name talky_sip --hashlimit-htable-expire 10000 -j DROP"
      # UFW accepts ESTABLISHED before its user rules. Enforce ingress here too
      # so a socket kept open across activation cannot bypass the new policy.
      # Loopback, SSH and HTTPS retain their existing semantics.
      allow_sources(carriers, "5060")
      print "-A " chain " ! -i lo -p udp --dport 5060 -j DROP"
      allow_sources(media, "10000:20000")
      print "-A " chain " ! -i lo -p udp --dport 10000:20000 -j DROP"
      print "-A " chain " ! -i lo -p udp --dport 4569 -j DROP"
      print "-A " chain " ! -i lo -p tcp -m multiport --dports 5432,6379,5050,8000 -j DROP"
      print "# END TALKY SIP LIMIT"
      inserted=1
    }
    {print}
    END {if (!inserted) exit 1}
  ' "$rules" > "$rules.talky-new"
  cat "$rules.talky-new" > "$rules"
  rm -f -- "$rules.talky-new"
done

# ── SSH — always allow (don't lock yourself out) ──────────────
if [[ ${#ADMIN_IPS[@]} -gt 0 ]]; then
  for ip in "${ADMIN_IPS[@]}"; do
    info "Allowing SSH from admin IP: $ip"
    ufw allow from "$ip" to any port 22 proto tcp comment "Admin SSH"
  done
else
  warn "No ADMIN_IPS set — allowing SSH from all IPs (less secure)"
  ufw allow 22/tcp comment "SSH"
fi

# SSH rules are present before the default-deny policy is selected.
ufw default deny incoming
ufw default allow outgoing

# ── HTTP/HTTPS — for reverse proxy (nginx/caddy in front of backend) ──
ufw allow 80/tcp  comment "HTTP (redirect to HTTPS)"
ufw allow 443/tcp comment "HTTPS"

# ── SIP & RTP — restricted to carrier IPs only ───────────────
# Every rule is port-scoped: a bare "allow from <ip> proto udp" would open ALL
# UDP ports to the carrier, wider than the ranges documented in the header.
for ip in "${CARRIER_IPS[@]}"; do
  info "Allowing SIP from carrier: $ip"
  ufw allow from "$ip" to any port 5060 proto udp comment "SIP UDP from carrier"
done
for ip in "${MEDIA_IPS[@]}"; do
  ufw allow from "$ip" to any port 10000:20000 proto udp comment "RTP range from carrier"
done

# ── Internal services — block from external interface ─────────
# These are handled by docker networking and should never be
# reached from outside the server.
info "Blocking external access to internal service ports..."
ufw deny 5432/tcp  comment "Block PostgreSQL from internet"
ufw deny 6379/tcp  comment "Block Redis from internet"
ufw deny 5050/tcp  comment "Block pgAdmin from internet"
ufw deny 8000/tcp  comment "Block backend API direct (use reverse proxy)"
ufw deny 4569/udp  comment "Block unused IAX2"

# ── Enable ────────────────────────────────────────────────────
info "Enabling ufw..."
ufw --force enable
trap - ERR

info "Current rules:"
ufw status verbose

echo ""
info "Firewall configured; AUTOMATIC DISABLE IN THREE MINUTES unless confirmed."
echo "  Verify a NEW SSH session, HTTPS, and each active carrier's SIP/RTP first."
echo "  Then: sudo bash $0 --confirm"
echo ""
warn "NEXT STEPS:"
echo "  1. Set up a reverse proxy (nginx/caddy) on 443 → localhost:8000"
echo "  2. Access pgAdmin via SSH tunnel:"
echo "       ssh -L 5050:localhost:5050 user@$(hostname -I | awk '{print $1}')"
echo "       Then open: http://localhost:5050"
echo "  3. Test SIP connectivity from your carrier"
