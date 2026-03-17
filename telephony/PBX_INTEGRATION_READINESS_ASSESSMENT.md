# PBX Integration Readiness Assessment

**Date:** March 11, 2026  
**Question:** Is the telephony stack "call ready" and can it work with any PBX configuration?  
**Answer:** 🟡 PARTIALLY READY - Requires Configuration

---

## Executive Summary

The telephony stack is **architecturally sound** but **NOT plug-and-play** for arbitrary PBX configurations. It requires specific configuration for each PBX type.

**Current Status:**
- ✅ Internal call flow works (OpenSIPS → Asterisk → AI)
- ✅ Architecture supports external PBX integration
- ⚠️ External PBX requires manual configuration
- ❌ NOT "upload any PBX config and it works"

---

## What IS Ready

### 1. ✅ Core Telephony Infrastructure
**Status:** Production-ready with security hardening

- OpenSIPS 3.4 (SIP edge with authentication)
- RTPEngine (media relay with SRTP)
- Asterisk (B2BUA with ARI)
- FreeSWITCH (backup B2BUA)
- Dispatcher-based failover
- TLS certificate validation
- SRTP encryption enforced
- SIP Digest Authentication

### 2. ✅ Internal Call Flow
**Status:** Fully functional

```
SIP Client → OpenSIPS (auth) → RTPEngine (SRTP) → Asterisk → AI Backend
```

**Capabilities:**
- Inbound calls from authenticated users
- Outbound calls to PSTN (via Vonage/provider)
- Media encryption (DTLS-SRTP)
- Call recording
- AI voice interaction
- Call transfer
- Session timers (RFC 4028)

### 3. ✅ Security Hardening
**Status:** Implemented (Phase 1 & 2 complete)

- CVE-2025-53399 mitigated (RTPEngine)
- TLS certificate validation enabled
- SRTP encryption enforced
- SIP Digest Authentication implemented
- Firewall hardening
- Rate limiting
- Flood protection

---

## What IS NOT Ready

### 1. ❌ Plug-and-Play PBX Integration
**Problem:** Each PBX type requires specific configuration

**Why:**
- Different SIP implementations (3CX, FreePBX, Avaya, Cisco, etc.)
- Different authentication methods
- Different codec support
- Different NAT handling
- Different SIP header requirements
- Different registration mechanisms

**Example:** The current `lan-pbx` configuration in `pjsip.conf` is hardcoded for:
```
- IP: 192.168.1.6
- Port: 5060
- Username: 1002
- Password: 1002
- Codec: PCMU (G.711 µ-law)
```

This will NOT work for a different PBX without modification.

### 2. ❌ Dynamic PBX Configuration Upload
**Problem:** No mechanism to upload and apply PBX configs dynamically

**Missing:**
- Web UI for PBX configuration
- API endpoint for PBX registration
- Configuration validation
- Dynamic Asterisk reload
- PBX discovery/auto-configuration
- Template system for common PBX types

### 3. ❌ Multi-PBX Support
**Problem:** Current config supports only ONE external PBX

**Limitations:**
- Single `lan-pbx` endpoint defined
- No multi-tenant PBX isolation
- No PBX-specific routing rules
- No per-PBX codec negotiation

---

## Current PBX Integration Capabilities

### Supported (with manual configuration):

#### 1. **SIP Trunk Mode**
Connect as a SIP trunk to external PBX

**Requirements:**
- PBX must support SIP trunking
- PBX must register to OpenSIPS (or vice versa)
- Manual configuration of:
  - PBX IP address
  - SIP credentials
  - Codec preferences
  - NAT settings

**Example PBX Types:**
- 3CX (SIP trunk configuration)
- FreePBX (trunk with registration)
- Asterisk (peer/trunk)
- FusionPBX
- Cisco CUCM (SIP trunk)

#### 2. **Peer Mode**
Direct SIP peering with external PBX

**Requirements:**
- Static IP addressing
- Firewall rules
- Mutual authentication
- Codec agreement

#### 3. **Registration Mode**
Asterisk registers to external PBX as a client

**Requirements:**
- PBX provides registration server
- Credentials configured in Asterisk
- Proper NAT traversal

---

## What Would Make It "Call Ready" for Any PBX

### Phase 1: Configuration Templates (2-3 days)

**Create pre-built templates for common PBX types:**

1. **3CX Template**
   ```
   - SIP trunk configuration
   - Authentication settings
   - Codec preferences (G.722, PCMU, PCMA)
   - NAT traversal settings
   ```

2. **FreePBX Template**
   ```
   - Trunk with registration
   - Outbound routes
   - Inbound routes
   - Codec negotiation
   ```

3. **Cisco CUCM Template**
   ```
   - SIP trunk security profile
   - Route patterns
   - Translation patterns
   ```

4. **Generic SIP PBX Template**
   ```
   - Basic SIP trunk
   - Flexible authentication
   - Common codecs
   ```

### Phase 2: Dynamic Configuration API (1-2 weeks)

**Build API for PBX configuration:**

```python
POST /api/v1/telephony/pbx/configure
{
  "pbx_type": "3cx",
  "name": "Office PBX",
  "ip_address": "192.168.1.100",
  "port": 5060,
  "username": "trunk_user",
  "password": "secure_password",
  "codecs": ["ulaw", "alaw", "g722"],
  "nat_enabled": true,
  "registration_required": true
}
```

**Features:**
- Validate configuration
- Generate Asterisk pjsip.conf entries
- Reload Asterisk dynamically
- Test connectivity
- Monitor registration status

### Phase 3: Web UI for PBX Management (2-3 weeks)

**Admin panel features:**
- Add/edit/delete PBX configurations
- PBX status dashboard
- Call statistics per PBX
- Configuration templates
- Import/export configs
- Test connectivity button

### Phase 4: Auto-Discovery (Advanced, 3-4 weeks)

**Automatic PBX detection:**
- SIP OPTIONS probing
- Capability negotiation
- Codec detection
- NAT detection
- Automatic configuration suggestion

---

## Current Integration Process (Manual)

### To Connect a New PBX Today:

#### Step 1: Gather PBX Information
```
- PBX Type: (3CX, FreePBX, etc.)
- IP Address: 
- SIP Port: (default 5060)
- Username/Extension:
- Password:
- Supported Codecs:
- NAT: Yes/No
- Registration: Required/Optional
```

#### Step 2: Edit Asterisk Configuration
```bash
# Edit telephony/asterisk/conf/pjsip.conf

# Add authentication
[my-pbx-auth]
type=auth
auth_type=userpass
username=<PBX_USERNAME>
password=<PBX_PASSWORD>

# Add AOR
[my-pbx-aor]
type=aor
contact=sip:<PBX_IP>:<PBX_PORT>
qualify_frequency=30

# Add endpoint
[my-pbx]
type=endpoint
transport=transport-udp
context=from-opensips
disallow=all
allow=ulaw
allow=alaw
aors=my-pbx-aor
outbound_auth=my-pbx-auth
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes

# Add identify
[my-pbx-identify]
type=identify
endpoint=my-pbx
match=<PBX_IP>

# Optional: Add registration
[my-pbx-registration]
type=registration
transport=transport-udp
outbound_auth=my-pbx-auth
server_uri=sip:<PBX_IP>:<PBX_PORT>
client_uri=sip:<USERNAME>@<PBX_IP>
contact_user=<USERNAME>
retry_interval=60
expiration=300
```

#### Step 3: Reload Asterisk
```bash
docker exec asterisk asterisk -rx "pjsip reload"
```

#### Step 4: Test Connectivity
```bash
# Check registration status
docker exec asterisk asterisk -rx "pjsip show registrations"

# Check endpoint status
docker exec asterisk asterisk -rx "pjsip show endpoints"

# Test call
# Make test call from PBX to Asterisk
```

#### Step 5: Configure PBX Side
```
On the PBX:
1. Create SIP trunk pointing to OpenSIPS IP:15060
2. Configure authentication (if using OpenSIPS auth)
3. Set outbound routes
4. Set inbound routes
5. Test call
```

---

## Comparison: Current vs. Ideal State

| Feature | Current State | Ideal State |
|---------|---------------|-------------|
| **PBX Configuration** | Manual editing of pjsip.conf | Web UI with templates |
| **Supported PBX Types** | Any (with manual config) | Pre-configured templates |
| **Configuration Time** | 30-60 minutes | 5 minutes |
| **Technical Skill Required** | High (SIP/Asterisk knowledge) | Low (point-and-click) |
| **Multi-PBX Support** | Manual (one at a time) | Unlimited (dynamic) |
| **Configuration Validation** | Manual testing | Automatic validation |
| **Connectivity Testing** | Manual CLI commands | Built-in test button |
| **Status Monitoring** | CLI commands | Dashboard |
| **Config Backup/Restore** | Manual file management | Automatic |
| **Documentation** | Generic Asterisk docs | PBX-specific guides |

---

## Recommended Implementation Plan

### Immediate (This Week):
1. **Document current PBX integration process** ✅ (this document)
2. **Create 3CX configuration template**
3. **Create FreePBX configuration template**
4. **Create generic SIP PBX template**
5. **Write step-by-step integration guides**

### Short-term (2-4 Weeks):
1. **Build PBX configuration API**
   - POST /api/v1/telephony/pbx/configure
   - GET /api/v1/telephony/pbx/list
   - DELETE /api/v1/telephony/pbx/{id}
   - POST /api/v1/telephony/pbx/{id}/test

2. **Create configuration validation**
   - IP address validation
   - Port validation
   - Credential testing
   - Codec compatibility check

3. **Implement dynamic Asterisk reload**
   - Generate pjsip.conf entries
   - Reload without downtime
   - Rollback on failure

### Medium-term (1-2 Months):
1. **Build Admin UI for PBX management**
   - Add PBX form
   - PBX list/status
   - Edit/delete PBX
   - Test connectivity button
   - Call statistics per PBX

2. **Add monitoring and alerting**
   - PBX registration status
   - Call success rate per PBX
   - Codec negotiation failures
   - NAT traversal issues

### Long-term (3-6 Months):
1. **Auto-discovery and configuration**
   - SIP OPTIONS probing
   - Capability detection
   - Automatic template selection

2. **Advanced features**
   - Multi-tenant PBX isolation
   - Per-PBX routing rules
   - Failover between PBXs
   - Load balancing

---

## Conclusion

### Is it "call ready"?
**YES** - for the internal AI voice flow (OpenSIPS → Asterisk → AI Backend)

### Can any PBX config be uploaded and work?
**NO** - requires manual configuration per PBX type

### What's needed for "plug-and-play" PBX support?
1. Configuration templates (2-3 days)
2. Dynamic configuration API (1-2 weeks)
3. Web UI for management (2-3 weeks)
4. Auto-discovery (3-4 weeks)

### Current Workaround:
Follow the manual integration process documented above. Takes 30-60 minutes per PBX but works reliably.

---

**Status:** 🟡 Architecturally ready, operationally requires configuration  
**Recommendation:** Implement Phase 1 (templates) immediately for common PBX types  
**Timeline:** 2-3 days for basic template support, 4-6 weeks for full automation
