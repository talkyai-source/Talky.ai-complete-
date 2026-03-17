#!/bin/bash
# Verify security hardening has been applied correctly
# Run this after applying security fixes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="${SCRIPT_DIR}/.."

echo "=== Telephony Security Hardening Verification ==="
echo ""

PASS=0
FAIL=0
WARN=0

check_pass() {
    echo "  ✅ $1"
    ((PASS++))
}

check_fail() {
    echo "  ❌ $1"
    ((FAIL++))
}

check_warn() {
    echo "  ⚠️  $1"
    ((WARN++))
}

# 1. Check RTPEngine Configuration
echo "1. RTPEngine Security (CVE-2025-53399 Mitigation)"
if grep -q "strict-source = yes" "${TELEPHONY_ROOT}/rtpengine/conf/rtpengine.conf"; then
    check_pass "strict-source enabled"
else
    check_fail "strict-source NOT enabled - vulnerable to RTP injection"
fi

if grep -q "dtls-passive = yes" "${TELEPHONY_ROOT}/rtpengine/conf/rtpengine.conf"; then
    check_pass "DTLS-SRTP enabled"
else
    check_fail "DTLS-SRTP NOT enabled - media not encrypted"
fi

# 2. Check OpenSIPS TLS Configuration
echo ""
echo "2. OpenSIPS TLS Certificate Validation"
if grep -q 'verify_cert.*\[default\]1' "${TELEPHONY_ROOT}/opensips/conf/opensips.cfg"; then
    check_pass "TLS certificate verification enabled"
else
    check_fail "TLS certificate verification DISABLED - vulnerable to MITM"
fi

if grep -q 'require_cert.*\[default\]1' "${TELEPHONY_ROOT}/opensips/conf/opensips.cfg"; then
    check_pass "TLS certificate requirement enabled"
else
    check_fail "TLS certificate requirement DISABLED"
fi

# 3. Check OpenSIPS SRTP Enforcement
echo ""
echo "3. OpenSIPS SRTP Enforcement"
if grep -q "DTLS=passive" "${TELEPHONY_ROOT}/opensips/conf/opensips.cfg"; then
    check_pass "DTLS-SRTP enforced in rtpengine_offer"
else
    check_fail "DTLS-SRTP NOT enforced - media transmitted in plaintext"
fi

# 4. Check FreeSWITCH ESL Password
echo ""
echo "4. FreeSWITCH ESL Security"
if grep -q 'value="ClueCon"' "${TELEPHONY_ROOT}/freeswitch/conf/autoload_configs/event_socket.conf.xml" 2>/dev/null; then
    check_fail "DEFAULT PASSWORD 'ClueCon' still in use - CRITICAL VULNERABILITY"
elif grep -q 'value="${FREESWITCH_ESL_PASSWORD}"' "${TELEPHONY_ROOT}/freeswitch/conf/autoload_configs/event_socket.conf.xml.secure" 2>/dev/null; then
    check_pass "Environment variable password configured"
    check_warn "Ensure .env.telephony.secure is deployed and not committed to git"
else
    check_warn "Unable to verify FreeSWITCH ESL password configuration"
fi

# 5. Check Asterisk ARI Password
echo ""
echo "5. Asterisk ARI Security"
if [ -f "${TELEPHONY_ROOT}/deploy/docker/.env.telephony.secure" ]; then
    if grep -q "ASTERISK_ARI_PASSWORD=day5_local_only_change_me" "${TELEPHONY_ROOT}/deploy/docker/.env.telephony.secure"; then
        check_fail "Weak ARI password still in use"
    else
        check_pass "Strong ARI password configured"
    fi
else
    check_warn "Secure env file not generated - run generate_secure_passwords.sh"
fi

# 6. Check Firewall Rules (if running as root)
echo ""
echo "6. Firewall Protection"
if [[ $EUID -eq 0 ]]; then
    if iptables -L INPUT -n | grep -q "rtp_flood"; then
        check_pass "RTP flood protection active"
    else
        check_warn "RTP flood protection not active - run setup_firewall_hardening.sh"
    fi
    
    if iptables -L INPUT -n | grep -q "sip_flood"; then
        check_pass "SIP flood protection active"
    else
        check_warn "SIP flood protection not active"
    fi
    
    if iptables -L INPUT -n | grep -q "8021.*DROP"; then
        check_pass "FreeSWITCH ESL port protected"
    else
        check_warn "FreeSWITCH ESL port not protected"
    fi
else
    check_warn "Not running as root - cannot verify firewall rules"
fi

# 7. Check RTPEngine Version
echo ""
echo "7. RTPEngine Version Check"
if docker ps | grep -q rtpengine; then
    VERSION=$(docker exec rtpengine rtpengine --version 2>&1 || echo "unknown")
    if echo "$VERSION" | grep -qE "mr13\.[4-9]|mr1[4-9]\.|mr[2-9][0-9]\."; then
        check_pass "RTPEngine version >= mr13.4.1.1 (CVE-2025-53399 patched)"
    else
        check_fail "RTPEngine version < mr13.4.1.1 - VULNERABLE to CVE-2025-53399"
        echo "     Current version: $VERSION"
    fi
else
    check_warn "RTPEngine container not running - cannot verify version"
fi

# Summary
echo ""
echo "========================================="
echo "Security Hardening Verification Summary"
echo "========================================="
echo "✅ Passed:  $PASS"
echo "❌ Failed:  $FAIL"
echo "⚠️  Warnings: $WARN"
echo ""

if [ $FAIL -gt 0 ]; then
    echo "🔴 CRITICAL: $FAIL security checks failed"
    echo "   System is NOT production-ready"
    echo "   Review failures above and apply fixes"
    exit 1
elif [ $WARN -gt 0 ]; then
    echo "🟡 WARNING: $WARN checks need attention"
    echo "   Review warnings before production deployment"
    exit 0
else
    echo "✅ SUCCESS: All security checks passed"
    echo "   System meets minimum security requirements"
    exit 0
fi
