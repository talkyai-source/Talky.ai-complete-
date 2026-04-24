#!/usr/bin/env bash
# scripts/firewall-setup.sh
# ============================================================
# Talky.ai — Production Firewall Rules (single Linux server)
# Run as root: sudo bash scripts/firewall-setup.sh
#
# What this script protects:
#   - SIP port 5080 restricted to known carrier IPs only
#   - RTP range 16384-32768 restricted to carrier IPs
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
# ─────────────────────────────────────────────────────────────

RED='\033[0;31m'
GRN='\033[0;32m'
YLW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GRN}[INFO]${NC} $*"; }
warn()  { echo -e "${YLW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Must run as root: sudo $0"

if [[ ${#CARRIER_IPS[@]} -eq 0 ]]; then
  warn "No CARRIER_IPS configured. Edit this script and add your SIP carrier IPs."
  warn "Skipping SIP/RTP rules — those ports will remain open."
fi

info "Installing ufw if not present..."
apt-get install -y ufw > /dev/null 2>&1 || true

info "Resetting ufw to default-deny..."
ufw --force reset

# ── Default policies ──────────────────────────────────────────
ufw default deny incoming
ufw default allow outgoing

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

# ── HTTP/HTTPS — for reverse proxy (nginx/caddy in front of backend) ──
ufw allow 80/tcp  comment "HTTP (redirect to HTTPS)"
ufw allow 443/tcp comment "HTTPS"

# ── SIP & RTP — restricted to carrier IPs only ───────────────
if [[ ${#CARRIER_IPS[@]} -gt 0 ]]; then
  for ip in "${CARRIER_IPS[@]}"; do
    info "Allowing SIP/RTP from carrier: $ip"
    ufw allow from "$ip" to any port 5080 proto udp comment "SIP UDP from carrier"
    ufw allow from "$ip" to any port 5080 proto tcp comment "SIP TLS from carrier"
    ufw allow from "$ip" to any port 5060 proto udp comment "SIP UDP alt from carrier"
    ufw allow from "$ip" proto udp comment "RTP from carrier" \
      && ufw allow from "$ip" to any port 16384:32768 proto udp comment "RTP range from carrier" 2>/dev/null || true
  done
else
  warn "SIP port 5080 and RTP range are currently open to all IPs."
  warn "Add carrier IPs to CARRIER_IPS[] and re-run this script."
fi

# ── Internal services — block from external interface ─────────
# These are handled by docker networking and should never be
# reached from outside the server.
info "Blocking external access to internal service ports..."
ufw deny 5432/tcp  comment "Block PostgreSQL from internet"
ufw deny 6379/tcp  comment "Block Redis from internet"
ufw deny 5050/tcp  comment "Block pgAdmin from internet"
ufw deny 8000/tcp  comment "Block backend API direct (use reverse proxy)"

# ── Enable ────────────────────────────────────────────────────
info "Enabling ufw..."
ufw --force enable

info "Current rules:"
ufw status verbose

echo ""
info "✅ Firewall configured."
echo ""
warn "NEXT STEPS:"
echo "  1. Set up a reverse proxy (nginx/caddy) on 443 → localhost:8000"
echo "  2. Access pgAdmin via SSH tunnel:"
echo "       ssh -L 5050:localhost:5050 user@$(hostname -I | awk '{print $1}')"
echo "       Then open: http://localhost:5050"
echo "  3. Test SIP connectivity from your carrier"
