#!/bin/bash
# Firewall hardening for telephony stack
# Implements rate limiting and DDoS protection

set -euo pipefail

echo "=== Telephony Firewall Hardening ==="
echo ""

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo "❌ This script must be run as root (for iptables configuration)"
   exit 1
fi

# Backup existing iptables rules
echo "📦 Backing up existing iptables rules..."
iptables-save > /tmp/iptables.backup.$(date +%Y%m%d_%H%M%S)

echo "🔒 Applying telephony security rules..."

# 1. RTP Port Range Rate Limiting (CVE-2025-53399 mitigation layer)
echo "  - RTP flood protection (ports 30000-34999)"
iptables -A INPUT -p udp --dport 30000:34999 -m hashlimit \
  --hashlimit-name rtp_flood \
  --hashlimit-mode srcip \
  --hashlimit-above 1000/sec \
  --hashlimit-burst 2000 \
  --hashlimit-htable-expire 10000 \
  -j DROP

# 2. SIP Flood Protection
echo "  - SIP flood protection (port 15060)"
iptables -A INPUT -p udp --dport 15060 -m hashlimit \
  --hashlimit-name sip_flood \
  --hashlimit-mode srcip \
  --hashlimit-above 100/sec \
  --hashlimit-burst 200 \
  -j DROP

# 3. SIP TLS Rate Limiting
echo "  - SIP TLS rate limiting (port 15061)"
iptables -A INPUT -p tcp --dport 15061 -m hashlimit \
  --hashlimit-name sip_tls_flood \
  --hashlimit-mode srcip \
  --hashlimit-above 50/sec \
  --hashlimit-burst 100 \
  -j DROP

# 4. FreeSWITCH ESL Protection (CRITICAL)
echo "  - FreeSWITCH ESL access control (port 8021)"
# Only allow localhost - ESL should NEVER be exposed externally
iptables -A INPUT -p tcp --dport 8021 ! -s 127.0.0.1 -j DROP
iptables -A INPUT -p tcp --dport 8021 -s 127.0.0.1 -m connlimit \
  --connlimit-above 10 --connlimit-mask 32 -j REJECT

# 5. Asterisk ARI Protection
echo "  - Asterisk ARI access control (port 8088)"
# Only allow localhost - ARI should NEVER be exposed externally
iptables -A INPUT -p tcp --dport 8088 ! -s 127.0.0.1 -j DROP

# 6. Connection Tracking Optimization
echo "  - Optimizing connection tracking for VoIP"
sysctl -w net.netfilter.nf_conntrack_max=262144
sysctl -w net.netfilter.nf_conntrack_udp_timeout=30
sysctl -w net.netfilter.nf_conntrack_udp_timeout_stream=60

# 7. SYN Flood Protection
echo "  - SYN flood protection"
sysctl -w net.ipv4.tcp_syncookies=1
sysctl -w net.ipv4.tcp_max_syn_backlog=2048
sysctl -w net.ipv4.tcp_synack_retries=2

# 8. ICMP Rate Limiting
echo "  - ICMP rate limiting"
iptables -A INPUT -p icmp -m limit --limit 1/sec --limit-burst 5 -j ACCEPT
iptables -A INPUT -p icmp -j DROP

# Make sysctl changes persistent
echo "💾 Making sysctl changes persistent..."
cat >> /etc/sysctl.conf <<EOF

# Telephony Stack Security Hardening
# Added: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
net.netfilter.nf_conntrack_max=262144
net.netfilter.nf_conntrack_udp_timeout=30
net.netfilter.nf_conntrack_udp_timeout_stream=60
net.ipv4.tcp_syncookies=1
net.ipv4.tcp_max_syn_backlog=2048
net.ipv4.tcp_synack_retries=2
EOF

# Save iptables rules
echo "💾 Saving iptables rules..."
if command -v netfilter-persistent &> /dev/null; then
    netfilter-persistent save
elif command -v iptables-save &> /dev/null; then
    iptables-save > /etc/iptables/rules.v4
fi

echo ""
echo "✅ Firewall hardening complete!"
echo ""
echo "Applied protections:"
echo "  ✓ RTP flood protection (1000 pps per source IP)"
echo "  ✓ SIP flood protection (100 pps per source IP)"
echo "  ✓ SIP TLS rate limiting (50 pps per source IP)"
echo "  ✓ FreeSWITCH ESL localhost-only access"
echo "  ✓ Asterisk ARI localhost-only access"
echo "  ✓ Connection tracking optimization"
echo "  ✓ SYN flood protection"
echo "  ✓ ICMP rate limiting"
echo ""
echo "⚠️  Test thoroughly before production deployment!"
echo "⚠️  Backup saved to: /tmp/iptables.backup.*"
echo ""
