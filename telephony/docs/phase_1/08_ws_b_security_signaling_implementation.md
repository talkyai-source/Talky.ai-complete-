# WS-B Implementation Log: Security and Signaling Baseline

Date: 2026-02-23  
Workstream: WS-B  
Status: Complete

---

## 1. Objective

Implement production-oriented signaling security controls on top of WS-A:
1. TLS transport path for SIP signaling.
2. Trusted source filtering.
3. Flood and rate protection.
4. FreeSWITCH event socket hardening.
5. Repeatable verification with objective pass/fail evidence.

---

## 2. Changes Implemented

## 2.1 Kamailio TLS Baseline

Files:
1. `telephony/kamailio/conf/kamailio.cfg`
2. `telephony/kamailio/conf/tls.cfg`
3. `telephony/deploy/docker/docker-compose.telephony.yml`
4. `telephony/deploy/docker/.env.telephony.example`
5. `telephony/scripts/generate_kamailio_tls_certs.sh`

Implementation:
1. Added TLS listener:
   - `listen = tls:KAMAILIO_SIP_IP:KAMAILIO_TLS_PORT`
2. Enabled TLS stack:
   - `enable_tls = yes`
   - `loadmodule "tls.so"`
   - `modparam("tls", "config", "/etc/kamailio/tls.cfg")`
3. Added environment controls:
   - `KAMAILIO_TLS_PORT` (default `15061`)
4. Added cert generation utility for staging:
   - creates `telephony/kamailio/certs/server.key`
   - creates `telephony/kamailio/certs/server.crt`

Notes:
1. Staging uses self-signed certs generated locally.
2. Production should replace with CA-issued certs and managed rotation.

## 2.2 Trusted Source ACL Baseline

Files:
1. `telephony/kamailio/conf/kamailio.cfg`
2. `telephony/kamailio/conf/address.list`
3. `telephony/deploy/docker/docker-compose.telephony.yml`

Implementation:
1. Enabled permissions module:
   - `loadmodule "permissions.so"`
   - `modparam("permissions", "address_file", "/etc/kamailio/address.list")`
2. Added request-route gate:
   - `if (!allow_source_address("1")) { ... 403 ... }`
3. Trusted ranges in group `1`:
   - loopback v4/v6
   - RFC1918 ranges
   - CGNAT range `100.64.0.0/10`

## 2.3 Flood and Rate Limiting Baseline

Files:
1. `telephony/kamailio/conf/kamailio.cfg`

Implementation:
1. Enabled pike module:
   - `loadmodule "pike.so"`
   - sampling and density thresholds configured
   - request-route gate with `pike_check_req()`
2. Enabled ratelimit module:
   - `loadmodule "ratelimit.so"`
   - per-method queues and pipes configured
   - request-route gate with `rl_check()`
   - returns `503 Rate Limited` with `Retry-After: 5`

## 2.4 FreeSWITCH ESL Hardening

File:
1. `telephony/freeswitch/conf/autoload_configs/event_socket.conf.xml`

Implementation:
1. Restricted event socket bind:
   - `listen-ip = 127.0.0.1`
2. Restricted ACL:
   - `apply-inbound-acl = loopback.auto`
3. Password retained from env/config baseline (`ClueCon` in staging; production rotation required).

## 2.5 Verification Automation

Files:
1. `telephony/scripts/sip_options_probe_tls.sh`
2. `telephony/scripts/verify_ws_b.sh`

Implementation:
1. `verify_ws_b.sh` runs:
   - cert presence/generation
   - WS-A baseline verification
   - Kamailio WS-B syntax check
   - TLS listener check
   - SIP OPTIONS over TLS probe
   - FreeSWITCH ESL bind + ACL checks
2. Output is deterministic and gate-friendly.

---

## 3. Validation Evidence

Primary command:
1. `bash telephony/scripts/verify_ws_b.sh telephony/deploy/docker/.env.telephony.example`

Observed result:
1. `WS-B verification PASSED.`

Key evidence excerpts:
1. `PASS: SIP response ... SIP/2.0 200 OK` (UDP/TCP baseline preserved)
2. `PASS: TLS SIP response: SIP/2.0 200 OK`
3. `TLS listener active on port 15061`
4. `FreeSWITCH ESL bound to loopback with loopback ACL`

---

## 4. Issues Found During Implementation and Fixes

1. Kamailio failed on `$proto` pseudo-variable parsing in this image build.
   - Fix: removed runtime `$proto` check; kept TLS transport listener active.
2. Kamailio failed on xlog messages using unsupported pseudo vars (`$si`) in this image.
   - Fix: replaced with static xlog strings.
3. TLS probe initially returned empty response due newline formatting.
   - Fix: switched to explicit SIP payload with CRLF via `printf '%b'`.
4. `ss` command unavailable in some containers.
   - Fix: moved listener checks to host-level `ss` where appropriate.

---

## 5. Security Posture After WS-B

1. TLS transport path is active and validated.
2. Source filtering is enforced through trusted CIDR policy.
3. Flood and method-level rate limiting is active in request route.
4. FreeSWITCH control socket is no longer broadly exposed.
5. Repeatable verifier exists for regression prevention.

Remaining production hardening for later phases:
1. Replace self-signed certs with managed CA certs.
2. Add tenant-specific trust policies and per-tenant rate profiles.
3. Add SIEM-grade security audit pipelines.

---

## 6. Official Reference Links Used

1. Kamailio modules index (stable): https://www.kamailio.org/docs/modules/5.8.x/  
2. Kamailio pike module: https://kamailio.org/docs/modules/devel/modules/pike.html  
3. Kamailio ratelimit module: https://www.kamailio.org/docs/modules/stable/modules/ratelimit.html  
4. Kamailio permissions module: https://www.kamailio.org/docs/modules/5.2.x/modules/permissions.html  
5. Kamailio TLS module: https://www.kamailio.org/docs/modules/5.8.x/modules/tls.html  
6. FreeSWITCH mod_event_socket: https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_event_socket_1048924/

