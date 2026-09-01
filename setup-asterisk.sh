#!/bin/bash
# Configure Asterisk on the production server:
#   - PJSIP trunk to Blazedigitel (sip3.blazedigitel.com)
#   - ARI on 127.0.0.1:8088 with user 'talky' (random password)
#   - Stasis app 'talky_ai' as the dialplan target for inbound + outbound
#   - rtp.conf restricted to 10000-20000 (matches what UFW allows)
#
# Idempotent: re-running it overwrites the same four service config files and
# reconciles the repository-owned inbound dialplan through the same
# candidate/digest/runtime-proof path used by deploy. It never overwrites
# extensions.conf.
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
# ── Carrier credentials ───────────────────────────────────────────────────────
# These were hardcoded literals in this file from 2026-05-18 (b5351a50) until
# 2026-07-24, in a PUBLIC repository. See docs/v2/09-known-issues.md F-17.
#
# They are now REQUIRED from the environment and this script aborts if either is
# unset — the `:?` expansion fails closed rather than provisioning a broken trunk.
#
# Provide them for a run, e.g.:
#     TRUNK_USER=... TRUNK_PASS=... sudo -E bash setup-asterisk.sh
# or source them from /opt/talky/secrets/ alongside the ARI password, which this
# same script already generates correctly with `openssl rand`.
#
# ROTATION IS STILL REQUIRED. Removing them from the working tree does NOT remove
# them from git history, and the history is public. Rotate with the carrier and
# request recent CDRs to confirm no unauthorised origination occurred.
# ──────────────────────────────────────────────────────────────────────────────
TRUNK_USER="${TRUNK_USER:?TRUNK_USER must be set in the environment — see docs/v2/09-known-issues.md F-17}"
TRUNK_PASS="${TRUNK_PASS:?TRUNK_PASS must be set in the environment — see docs/v2/09-known-issues.md F-17}"
ARI_USER="talky"

# Dialplan safety preflight. The working production extensions.conf contains
# operator-owned configuration and must never be erased by this bootstrap
# script. Asterisk must already include extensions.d/*.conf; adding that include
# is a one-time reviewed operator change. The managed include itself is rendered
# later from the database into a secured candidate and installed atomically.
if ! grep -Eq '^[[:space:]]*#include[[:space:]]+"?extensions\.d/\*\.conf"?[[:space:]]*$' /etc/asterisk/extensions.conf; then
    echo "ERROR: /etc/asterisk/extensions.conf does not include extensions.d/*.conf." >&2
    echo "       Refusing to rewrite it. Add the include in a reviewed maintenance window." >&2
    exit 1
fi

# Prove the database-derived account routes agree with the reviewed carrier
# inventory before installing packages, stopping Asterisk, writing any config,
# or rotating ARI credentials. A conflict must leave the operator's working
# live context completely untouched.
bash /opt/talky/backend/scripts/reconcile_asterisk_release.sh --check-only

# 1) Make sure Asterisk is installed
if ! command -v asterisk >/dev/null 2>&1; then
    echo "==> Asterisk not installed, installing now..."
    apt update
    DEBIAN_FRONTEND=noninteractive apt install -y asterisk
else
    echo "==> Asterisk already installed: $(asterisk -V)"
fi

# 2) Stop Asterisk before rewriting config (it auto-starts after install).
# Restart it on any later error so a failed provisioning run does not leave
# the production PBX down.
ASTERISK_WAS_ACTIVE=0
if systemctl is-active --quiet asterisk; then
    ASTERISK_WAS_ACTIVE=1
fi
restore_asterisk_on_error() {
    rc=$?
    if [ "$rc" -ne 0 ] && [ "$ASTERISK_WAS_ACTIVE" -eq 1 ]; then
        systemctl start asterisk || true
    fi
    exit "$rc"
}
trap restore_asterisk_on_error ERR
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
context=from-talky-inbound
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

; Per-tenant SIP trunks generated by the backend
; (app/infrastructure/telephony/pjsip_config_generator.py) into
; /etc/asterisk/pjsip.d/trunk-<trunkid>.conf.  The generator owns that
; directory EXCLUSIVELY and documents this include as a hard prerequisite:
; without it Asterisk never loads a tenant trunk, and a 'pjsip reload' still
; reports success — the trunk silently does not exist.  It lives inside this
; heredoc because this script rewrites pjsip.conf wholesale on every run, so a
; hand-added include would be destroyed by the next run.
#include pjsip.d/*.conf
PJSIPEOF

# 8b) pjsip.d — the directory the backend writes per-tenant trunks into.
# Prerequisites (b) and (c) from pjsip_config_generator.py's docstring:
#   - owned asterisk:asterisk, mode 2770 (setgid) so files created inside
#     inherit group 'asterisk';
#   - the backend service user is in the 'asterisk' group, so that combined
#     with the generator's 0640 file mode the asterisk process can read the
#     files the backend creates (a 0600/wrong-group file is silently skipped
#     by the #include and the trunk never loads — proven live).
mkdir -p /etc/asterisk/pjsip.d
chown asterisk:asterisk /etc/asterisk/pjsip.d
chmod 2770 /etc/asterisk/pjsip.d
usermod -aG asterisk admins 2>/dev/null || echo "WARN: could not add 'admins' to the 'asterisk' group — tenant trunk writes will fail with EACCES"
echo "==> Provisioned /etc/asterisk/pjsip.d (asterisk:asterisk, 2770)"

# 9) Provision only the managed include directory. The reconciler owns the
# talky-inbound.conf file; extensions.conf remains byte-for-byte untouched.
install -d -o root -g asterisk -m 0755 /etc/asterisk/extensions.d

echo "==> Wrote /etc/asterisk/{http,ari,rtp,pjsip}.conf and preserved extensions.conf"

# 10) Update backend .env with the new ARI password
sed -i "s|^ASTERISK_ARI_PASSWORD=.*|ASTERISK_ARI_PASSWORD=$ARI_PW|" /opt/talky/backend/.env
echo "==> Updated /opt/talky/backend/.env with new ARI password"

# 11) Start Asterisk, then install the database-derived candidate only after
# the reconciler proves zero channels. This owns pjsip.d + talky-inbound.conf,
# checks the approved digest again, reloads, verifies runtime objects, and rolls
# the complete managed set back on any failure.
systemctl restart asterisk
echo "==> Asterisk restarted; sleeping 10s for registration to settle..."
sleep 10
bash /opt/talky/backend/scripts/reconcile_asterisk_release.sh
trap - ERR
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
