# Telephony Stack Security Hardening Checklist

**Status:** 🔴 CRITICAL FIXES APPLIED - TESTING REQUIRED  
**Date:** March 11, 2026  
**Priority:** MUST COMPLETE BEFORE PRODUCTION DEPLOYMENT

---

## ✅ COMPLETED FIXES

### 1. RTPEngine CVE-2025-53399 Mitigation (CRITICAL)
- [x] Updated `rtpengine.conf` with `strict-source = yes`
- [x] Added DTLS-SRTP support with `dtls-passive = yes`
- [x] Updated Dockerfile to enforce version >= mr13.4.1.1
- [x] Added version check in container build

**Files Modified:**
- `telephony/rtpengine/conf/rtpengine.conf`
- `telephony/rtpengine/Dockerfile`

**Testing Required:**
```bash
# Verify RTPEngine version
docker exec rtpengine rtpengine --version

# Test strict-source enforcement
# Attempt RTP injection from unauthorized source (should fail)
```

---

### 2. TLS Certificate Validation Enabled (CRITICAL)
- [x] OpenSIPS: `verify_cert = 1`, `require_cert = 1`
- [x] Kamailio: `verify_certificate = yes`, `require_certificate = yes`
- [x] Added CA certificate list references

**Files Modified:**
- `telephony/opensips/conf/opensips.cfg`
- `telephony/kamailio/conf/tls.cfg`

**Testing Required:**
```bash
# Test TLS connection with valid certificate
openssl s_client -connect localhost:15061 -CAfile /etc/ssl/certs/ca-certificates.crt

# Test TLS connection with invalid certificate (should fail)
openssl s_client -connect localhost:15061 -cert invalid.crt
```

---

### 3. SRTP Encryption Enforced (CRITICAL)
- [x] Updated OpenSIPS rtpengine_offer() with `DTLS=passive`
- [x] Updated OpenSIPS rtpengine_answer() with `DTLS=passive`
- [x] Added security comments explaining SRTP enforcement

**Files Modified:**
- `telephony/opensips/conf/opensips.cfg`

**Testing Required:**
```bash
# Capture SDP and verify SRTP crypto attributes
sngrep -c

# Verify RTP packets are encrypted (should not see plaintext audio)
tcpdump -i any -n udp portrange 30000-34999 -X
```

---

### 4. Secure Password Generation Script (CRITICAL)
- [x] Created `generate_secure_passwords.sh`
- [x] Generates 256-bit entropy passwords
- [x] Creates secure .env file with 600 permissions
- [x] Includes secrets manager integration examples

**Files Created:**
- `telephony/scripts/generate_secure_passwords.sh`
- `telephony/freeswitch/conf/autoload_configs/event_socket.conf.xml.secure`

**Action Required:**
```bash
# Generate passwords
cd telephony/scripts
./generate_secure_passwords.sh

# Store in secrets manager
vault kv put secret/telephony/freeswitch esl_password='<generated>'
vault kv put secret/telephony/asterisk ari_password='<generated>'

# Update FreeSWITCH config to use environment variable
cp ../freeswitch/conf/autoload_configs/event_socket.conf.xml.secure \
   ../freeswitch/conf/autoload_configs/event_socket.conf.xml
```

---

### 5. Firewall Hardening Script (HIGH)
- [x] Created `setup_firewall_hardening.sh`
- [x] RTP flood protection (1000 pps limit)
- [x] SIP flood protection (100 pps limit)
- [x] FreeSWITCH ESL localhost-only access
- [x] Asterisk ARI localhost-only access
- [x] Connection tracking optimization
- [x] SYN flood protection

**Files Created:**
- `telephony/scripts/setup_firewall_hardening.sh`

**Action Required:**
```bash
# Apply firewall rules (requires root)
sudo telephony/scripts/setup_firewall_hardening.sh

# Verify rules
sudo iptables -L -n -v | grep -E "rtp_flood|sip_flood"
```

---

## 🔴 REMAINING CRITICAL TASKS

### 6. Password Rotation Implementation
**Status:** NOT STARTED  
**Priority:** CRITICAL

**Required Actions:**
- [ ] Implement automated password rotation (90-day cycle)
- [ ] Create rotation script with secrets manager integration
- [ ] Add monitoring for password age
- [ ] Document rotation procedure

---

### 7. SIP Digest Authentication
**Status:** ✅ COMPLETED  
**Priority:** HIGH

**Completed Actions:**
- [x] Created subscriber database schema with RFC 8760 support
- [x] Implemented add_sip_subscriber.sh script
- [x] Created opensips-with-auth.cfg with full authentication
- [x] Added multi-algorithm support (MD5, SHA-256, SHA-512-256)
- [x] Implemented nonce-based replay protection
- [x] Added failed authentication logging
- [x] Credential consumption (security best practice)

**Files Created:**
- `telephony/database/subscriber_table.sql`
- `telephony/scripts/add_sip_subscriber.sh`
- `telephony/opensips/conf/opensips-with-auth.cfg`
- `telephony/SIP_AUTHENTICATION_IMPLEMENTATION.md`

**Testing Required:**
```bash
# Deploy database
mysql -u root -p opensips < telephony/database/subscriber_table.sql

# Add test subscriber
./telephony/scripts/add_sip_subscriber.sh -u test -d talky.local -p 'SecurePass123!'

# Test authentication
# Should return 407 without credentials
# Should succeed with valid credentials
```

---

### 8. STIR/SHAKEN Implementation
**Status:** NOT STARTED  
**Priority:** MEDIUM

**Required Actions:**
- [ ] Obtain STIR/SHAKEN certificate
- [ ] Configure attestation service
- [ ] Implement verification in OpenSIPS
- [ ] Add call marking for unverified calls

---

### 9. Certificate Expiration Monitoring
**Status:** NOT STARTED  
**Priority:** MEDIUM

**Required Actions:**
- [ ] Add Prometheus alert for cert expiration
- [ ] Set alert threshold to 30 days
- [ ] Document cert renewal procedure
- [ ] Test alert delivery

**Prometheus Alert:**
```yaml
- alert: TelephonyTLSCertExpiringSoon
  expr: (x509_cert_expiry - time()) / 86400 < 30
  for: 1h
  labels:
    severity: warning
  annotations:
    summary: "TLS certificate expires in < 30 days"
```

---

### 10. Security Audit Logging
**Status:** NOT STARTED  
**Priority:** MEDIUM

**Required Actions:**
- [ ] Enable security event logging in OpenSIPS
- [ ] Log failed authentication attempts
- [ ] Log rate limit violations
- [ ] Forward logs to SIEM

---

## 🧪 TESTING REQUIREMENTS

### Pre-Production Testing Checklist

- [ ] **RTP Injection Test**: Attempt to inject RTP packets from unauthorized source
- [ ] **TLS MITM Test**: Attempt connection with invalid certificate
- [ ] **SRTP Verification**: Confirm all media is encrypted
- [ ] **Password Strength Test**: Verify no default passwords remain
- [ ] **Firewall Test**: Verify rate limits trigger correctly
- [ ] **Failover Test**: Ensure security controls survive failover
- [ ] **Load Test**: Verify security controls don't impact performance
- [ ] **Penetration Test**: Third-party security assessment

---

## 📋 DEPLOYMENT PROCEDURE

### Step 1: Generate Secure Passwords
```bash
cd telephony/scripts
./generate_secure_passwords.sh
# Store passwords in secrets manager
```

### Step 2: Update Configurations
```bash
# Update FreeSWITCH ESL password
cp telephony/freeswitch/conf/autoload_configs/event_socket.conf.xml.secure \
   telephony/freeswitch/conf/autoload_configs/event_socket.conf.xml

# Update environment file
cp telephony/deploy/docker/.env.telephony.secure \
   telephony/deploy/docker/.env.telephony
```

### Step 3: Rebuild Containers
```bash
cd telephony/deploy/docker
docker-compose build --no-cache rtpengine
docker-compose build --no-cache opensips
docker-compose build --no-cache kamailio
```

### Step 4: Apply Firewall Rules
```bash
sudo telephony/scripts/setup_firewall_hardening.sh
```

### Step 5: Deploy and Test
```bash
docker-compose up -d
# Run security tests
telephony/scripts/verify_security_hardening.sh
```

---

## 🚨 SECURITY INCIDENT RESPONSE

### If Breach Detected:

1. **Immediate Actions:**
   - Isolate affected components
   - Rotate all credentials immediately
   - Enable verbose logging
   - Capture forensic evidence

2. **Investigation:**
   - Review security logs
   - Check for unauthorized access
   - Identify attack vector
   - Assess data exposure

3. **Remediation:**
   - Patch vulnerabilities
   - Update firewall rules
   - Implement additional controls
   - Document lessons learned

---

## 📞 SECURITY CONTACTS

- **Security Team:** [email protected]
- **On-Call:** +1-XXX-XXX-XXXX
- **Incident Response:** [email protected]

---

## 📚 REFERENCES

1. [CVE-2025-53399 Advisory](https://www.enablesecurity.com/blog/rtpengine-critical-security-advisory-cve-2025-53399)
2. [OpenSIPS TLS Documentation](https://opensips.org/Documentation/Tutorials-TLS-2-1)
3. [FreeSWITCH Security Guide](https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Security/)
4. [SRTP Best Practices](https://telnyx.com/resources/srtp-protocol)

---

**Last Updated:** March 11, 2026  
**Next Review:** Before production deployment  
**Status:** 🔴 CRITICAL - Security hardening in progress
