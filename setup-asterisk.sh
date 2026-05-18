#!/bin/bash
# Configure Asterisk on the production server:
#   - PJSIP trunk to Blazedigitel (sip3.blazedigitel.com)
#   - ARI on 127.0.0.1:8088 with user 'talky' (random password)
#   - Stasis app 'talky_ai' as the dialplan target for inbound + outbound
#   - rtp.conf restricted to 10000-20000 (matches what UFW allows)
#
# Idempotent: re-running it overwrites the same five config files and
# regenerates the ARI password.  Backs up the current /etc/asterisk
# to /etc/asterisk.bak.<ts> the first time it runs (and every run, so
# you can always roll back to whatever was just there).
#
# Run with:   sudo bash /opt/talky/setup-asterisk.sh
set -e

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: must run with sudo"
    exit 1
fi

SERVER_IP="144.76.17.150"
TRUNK_HOST="sip3.blazedigitel.com"
TRUNK_USER="17789249977"
TRUNK_PASS="17789249977xL02"
ARI_USER="talky"

# 1) Make sure Asterisk is installed
if ! command -v asterisk >/dev/null 2>&1; then
    echo "==> Asterisk not installed, installing now..."
    apt update
    DEBIAN_FRONTEND=noninteractive apt install -y asterisk
else
    echo "==> Asterisk already installed: $(asterisk -V)"
fi

# 2) Stop Asterisk before rewriting config (it auto-starts after install)
systemctl stop asterisk || true

# 3) Generate a strong ARI password
ARI_PW=$(openssl rand -hex 24)
mkdir -p /opt/talky/secrets
echo "$ARI_PW" > /opt/talky/secrets/ari-password
chmod 600 /opt/talky/secrets/ari-password
chown admins:admins /opt/talky/secrets/ari-password 2>/dev/null || true
echo "==> Generated ARI password, saved to /opt/talky/secrets/ari-password"

# 4) Back up existing /etc/asterisk
BACKUP="/etc/asterisk.bak.$(date +%s)"
cp -r /etc/asterisk "$BACKUP"
echo "==> Backed up /etc/asterisk to $BACKUP"

# 5) http.conf — ARI HTTP listener on loopback
cat > /etc/asterisk/http.conf <<'HTTPEOF'
[general]
enabled = yes
bindaddr = 127.0.0.1
bindport = 8088
HTTPEOF

# 6) ari.conf — one user 'talky' with the generated password
cat > /etc/asterisk/ari.conf <<ARIEOF
[general]
enabled = yes
pretty = yes
allowed_origins = http://localhost:8000,http://127.0.0.1:8000

[$ARI_USER]
type = user
password = $ARI_PW
password_format = plain
read_only = no
ARIEOF

# 7) rtp.conf — RTP range 10000-20000 (matches UFW)
cat > /etc/asterisk/rtp.conf <<'RTPEOF'
[general]
rtpstart=10000
rtpend=20000
RTPEOF

# 8) pjsip.conf — Blazedigitel trunk: transport, auth, registration, AOR, endpoint, identify
cat > /etc/asterisk/pjsip.conf <<PJSIPEOF
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060
external_media_address=$SERVER_IP
external_signaling_address=$SERVER_IP

[blazedigitel-auth]
type=auth
auth_type=userpass
username=$TRUNK_USER
password=$TRUNK_PASS

[blazedigitel-aor]
type=aor
contact=sip:$TRUNK_HOST:5060
qualify_frequency=60

[blazedigitel-reg]
type=registration
transport=transport-udp
outbound_auth=blazedigitel-auth
server_uri=sip:$TRUNK_HOST
client_uri=sip:$TRUNK_USER@$TRUNK_HOST
contact_user=$TRUNK_USER
retry_interval=60
expiration=600

[blazedigitel-endpoint]
type=endpoint
transport=transport-udp
context=from-blazedigitel
disallow=all
allow=ulaw
allow=alaw
outbound_auth=blazedigitel-auth
aors=blazedigitel-aor
from_user=$TRUNK_USER
from_domain=$TRUNK_HOST
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes

[blazedigitel-identify]
type=identify
endpoint=blazedigitel-endpoint
match=$TRUNK_HOST
PJSIPEOF

# 9) extensions.conf — dialplan
cat > /etc/asterisk/extensions.conf <<'EXTEOF'
[general]
static=yes
writeprotect=no

[globals]

; Incoming calls from Blazedigitel — handed to the talky_ai Stasis app
[from-blazedigitel]
exten => _.,1,NoOp(Inbound from Blazedigitel: ${CALLERID(num)} -> ${EXTEN})
 same => n,Stasis(talky_ai,inbound,${CALLERID(num)},${EXTEN})
 same => n,Hangup()

; Outbound calls originated by the app (via ARI) — dial via the trunk
[from-talky]
exten => _X.,1,NoOp(Outbound to ${EXTEN} via Blazedigitel)
 same => n,Dial(PJSIP/${EXTEN}@blazedigitel-endpoint,60,T)
 same => n,Hangup()
EXTEOF

echo "==> Wrote /etc/asterisk/{http,ari,rtp,pjsip,extensions}.conf"

# 10) Update backend .env with the new ARI password
sed -i "s|^ASTERISK_ARI_PASSWORD=.*|ASTERISK_ARI_PASSWORD=$ARI_PW|" /opt/talky/backend/.env
echo "==> Updated /opt/talky/backend/.env with new ARI password"

# 11) Restart Asterisk + talky-api so they pick up the new config
systemctl restart asterisk
echo "==> Asterisk restarted; sleeping 10s for registration to settle..."
sleep 10
systemctl restart talky-api
sleep 4

# 12) Status checks
echo
echo "=========================================="
echo "  STATUS CHECKS"
echo "=========================================="

echo
echo "--- asterisk service ---"
systemctl is-active asterisk && echo "asterisk: active" || echo "asterisk: NOT ACTIVE"

echo
echo "--- pjsip registration to Blazedigitel ---"
asterisk -rx 'pjsip show registrations' 2>&1 | head -20 || echo "(asterisk -rx failed)"

echo
echo "--- pjsip endpoint state ---"
asterisk -rx 'pjsip show endpoint blazedigitel-endpoint' 2>&1 | grep -E "(Endpoint|Aor|Status|DeviceState)" | head -10 || true

echo
echo "--- ARI HTTP health (200 = good, 401 = wrong pw, 404 = ARI off) ---"
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -u "$ARI_USER:$ARI_PW" http://127.0.0.1:8088/ari/asterisk/info

echo
echo "--- talky-api telephony log (last 15 lines mentioning asterisk/ari) ---"
journalctl -u talky-api -n 100 --no-pager 2>/dev/null | grep -iE "(asterisk|ari|telephony)" | tail -15 || echo "(no telephony log entries found)"

echo
echo "=========================================="
echo "  COMPLETE"
echo "=========================================="
echo "Useful follow-up commands:"
echo "  sudo cat /opt/talky/secrets/ari-password"
echo "  sudo asterisk -rvvvv                              # interactive Asterisk console (type 'exit' to leave)"
echo "  sudo asterisk -rx 'pjsip show registrations'      # is the trunk registered?"
echo "  sudo asterisk -rx 'pjsip show endpoints'          # list all SIP endpoints"
echo "  sudo asterisk -rx 'core show channels'            # any live calls?"
echo "  sudo systemctl status asterisk talky-api"
echo "  sudo journalctl -u asterisk -f                    # tail asterisk logs"
echo "  sudo journalctl -u talky-api -f                   # tail backend logs"
