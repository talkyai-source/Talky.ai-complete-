#!/bin/bash
# Generate secure passwords for telephony components
# SECURITY: Replace all default passwords before production deployment

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../deploy/docker/.env.telephony"

echo "=== Telephony Security: Password Generation ==="
echo ""
echo "⚠️  WARNING: This will generate new secure passwords."
echo "⚠️  Store these in a secrets manager (HashiCorp Vault, AWS Secrets Manager, etc.)"
echo "⚠️  NEVER commit passwords to git!"
echo ""

# Generate strong passwords (32 bytes = 256 bits of entropy)
FREESWITCH_ESL_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
ASTERISK_ARI_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)

echo "Generated passwords:"
echo ""
echo "FreeSWITCH ESL Password:"
echo "  ${FREESWITCH_ESL_PASSWORD}"
echo ""
echo "Asterisk ARI Password:"
echo "  ${ASTERISK_ARI_PASSWORD}"
echo ""

# Create secure env file
cat > "${ENV_FILE}.secure" <<EOF
# SECURITY-HARDENED TELEPHONY CONFIGURATION
# Generated: $(date -u +"%Y-%m-%d %H:%M:%S UTC")
# 
# ⚠️  CRITICAL: Store these credentials in a secrets manager
# ⚠️  DO NOT commit this file to version control
# ⚠️  Rotate passwords every 90 days

# OpenSIPS runtime with TLS modules
OPENSIPS_IMAGE=talky/opensips:3.4-tls
RTPENGINE_IMAGE=talky/rtpengine:ubuntu24.04-secure
FREESWITCH_IMAGE=safarov/freeswitch:latest
ASTERISK_IMAGE=talky/asterisk:bookworm

OPENSIPS_SIP_IP=0.0.0.0
OPENSIPS_SIP_PORT=15060
OPENSIPS_TLS_PORT=15061
OPENSIPS_TLS_ONLY=0

OPENSIPS_CANARY_ENABLED=0
OPENSIPS_CANARY_PERCENT=0
OPENSIPS_CANARY_FREEZE=0

FREESWITCH_SIP_PORT=5080
FREESWITCH_ESL_PORT=8021
# SECURITY: Strong password replaces default "ClueCon"
FREESWITCH_ESL_PASSWORD=${FREESWITCH_ESL_PASSWORD}

ASTERISK_SIP_PORT=5070
ASTERISK_ARI_HOST=127.0.0.1
ASTERISK_ARI_PORT=8088
ASTERISK_ARI_USERNAME=talky_ari_user
# SECURITY: Strong password replaces weak default
ASTERISK_ARI_PASSWORD=${ASTERISK_ARI_PASSWORD}
ASTERISK_ARI_APP=talky_production

DAY5_TEST_EXTENSION=750

RTPENGINE_NG_PORT=2223
RTP_PORT_MIN=40000
RTP_PORT_MAX=44999
EOF

chmod 600 "${ENV_FILE}.secure"

echo "✅ Secure configuration written to: ${ENV_FILE}.secure"
echo ""
echo "Next steps:"
echo "1. Store passwords in your secrets manager"
echo "2. Update FreeSWITCH config: telephony/freeswitch/conf/autoload_configs/event_socket.conf.xml"
echo "3. Update Asterisk ARI config"
echo "4. Add ${ENV_FILE}.secure to .gitignore"
echo "5. Use environment variable injection at runtime"
echo ""
echo "Example secrets manager storage:"
echo "  vault kv put secret/telephony/freeswitch esl_password='${FREESWITCH_ESL_PASSWORD}'"
echo "  vault kv put secret/telephony/asterisk ari_password='${ASTERISK_ARI_PASSWORD}'"
echo ""
