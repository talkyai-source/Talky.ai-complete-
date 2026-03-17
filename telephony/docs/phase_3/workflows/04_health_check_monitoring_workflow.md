# Runtime Health Check & Monitoring Workflow

> **Phase:** 3 — Production Rollout + Resiliency  
> **Scope:** Continuous runtime health verification for the telephony stack  
> **When to Use:** Daily operations, pre-canary checks, post-incident validation

---

## Overview

This workflow defines the standard procedures for verifying telephony stack health. It should be run:
- **Daily** — as part of routine operations
- **Before canary progression** — as a prerequisite gate
- **After any incident** — to confirm recovery
- **After deployments** — to verify stack integrity

---

## Quick Health Check (2 minutes)

```bash
# =================================================================
# QUICK HEALTH CHECK — Run this daily or before any canary operation
# =================================================================

echo "=== Container Health ==="
docker ps --filter name=talky- --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "=== SIP Probe (UDP) ==="
python3 telephony/scripts/sip_options_probe.py 127.0.0.1 15060 5 && echo "✅ PASS" || echo "❌ FAIL"

echo ""
echo "=== SIP Probe (TLS) ==="
bash telephony/scripts/sip_options_probe_tls.sh 127.0.0.1 15061 5 && echo "✅ PASS" || echo "❌ FAIL"

echo ""
echo "=== OpenSIPS Config ==="
docker exec talky-opensips opensips -C -f /etc/opensips/opensips.cfg > /dev/null 2>&1 && echo "✅ Config valid" || echo "❌ Config error"

echo ""
echo "=== Asterisk Status ==="
docker exec talky-asterisk asterisk -rx "core show version" 2>/dev/null && echo "✅ Running" || echo "❌ Not running"
docker exec talky-asterisk asterisk -rx "pjsip show transports" 2>/dev/null | head -5

echo ""
echo "=== RTPengine ==="
ss -lun | grep ':2223' > /dev/null && echo "✅ NG port active" || echo "❌ NG port missing"

echo ""
echo "=== Metrics Endpoint ==="
curl -s -o /dev/null -w "HTTP %{http_code}" http://127.0.0.1:8000/metrics && echo " ✅" || echo " ❌"
```

### Expected Output

| Component | Expected | 
|-----------|----------|
| `talky-opensips` | Up (healthy) |
| `talky-asterisk` | Up (healthy) |
| `talky-rtpengine` | Up (healthy) |
| SIP UDP probe | 200 OK |
| SIP TLS probe | 200 OK |
| OpenSIPS config | Valid |
| Asterisk version | Asterisk 20.x |
| PJSIP transport | `transport-udp` on `0.0.0.0:5088` |
| RTPengine NG port | `:2223` listening |
| Metrics endpoint | HTTP 200 |

---

## Full Verification (10 minutes)

```bash
# =================================================================
# FULL VERIFICATION — Run before canary progression or after incidents
# =================================================================

echo "=== WS-A: Infrastructure ==="
bash telephony/scripts/verify_ws_a.sh telephony/deploy/docker/.env.telephony.example

echo ""
echo "=== WS-B: Security Baseline ==="
bash telephony/scripts/verify_ws_b.sh telephony/deploy/docker/.env.telephony.example

echo ""
echo "=== WS-K: SLO & Telemetry ==="
bash telephony/scripts/verify_ws_k.sh telephony/deploy/docker/.env.telephony.example

echo ""
echo "=== WS-L: Canary Orchestration ==="
bash telephony/scripts/verify_ws_l.sh telephony/deploy/docker/.env.telephony.example

echo ""
echo "=== Integration Tests ==="
TELEPHONY_RUN_DOCKER_TESTS=1 python3 -m unittest -v telephony/tests/test_telephony_stack.py
```

### Gate Results Matrix

| Gate | Script | What It Validates |
|------|--------|-------------------|
| WS-A | `verify_ws_a.sh` | SIP OPTIONS 200 OK, RTPengine control socket, Docker health |
| WS-B | `verify_ws_b.sh` | TLS listener, ACL enforcement, flood control, rate limiting |
| WS-K | `verify_ws_k.sh` | Prometheus metrics presence, recording rules syntax, alert routing |
| WS-L | `verify_ws_l.sh` | Stage controller logic, freeze guard, rollback path, decision artifacts |
| Integration | `test_telephony_stack.py` | 19 end-to-end tests covering all workstreams |

---

## SLO Dashboard Queries

Use these Prometheus queries to assess telephony health:

### Call Setup Health

```promql
# Call setup success rate (5-minute window)
telephony_call_setup_success_ratio

# Call setup attempts (rate)
rate(telephony_call_setup_attempts_total[5m])
```

### Latency

```promql
# Answer latency p95
telephony_answer_latency_seconds{quantile="0.95"}

# Answer latency p50
telephony_answer_latency_seconds{quantile="0.50"}
```

### Transfer Reliability

```promql
# Transfer success rate
telephony_transfer_success_ratio

# Active transfers in-flight
telephony_transfers_inflight
```

### Canary State

```promql
# Current canary state
telephony_canary_enabled
telephony_canary_percent
telephony_canary_frozen
```

### Activation/Rollback

```promql
# Activation success rate
telephony_activation_success_ratio

# Rollback latency p95
telephony_rollback_latency_seconds{quantile="0.95"}
```

---

## Troubleshooting Guide

### Problem: SIP OPTIONS probe fails

```mermaid
flowchart TB
    A[SIP probe fails] --> B{Container running?}
    B -- No --> C[docker start talky-opensips]
    B -- Yes --> D{Config valid?}
    D -- No --> E[Check opensips.cfg syntax]
    D -- Yes --> F{Port bound?}
    F -- No --> G[Check for port conflict:\nss -ltn | grep 15060]
    F -- Yes --> H{Firewall blocking?}
    H -- Yes --> I[Check iptables/ufw rules]
    H -- No --> J[Check OpenSIPS logs:\ndocker logs talky-opensips]
```

### Problem: Asterisk PJSIP transport not visible

```bash
# 1. Check if Asterisk is running
docker exec talky-asterisk asterisk -rx "core show version"

# 2. Check module loading
docker exec talky-asterisk asterisk -rx "module show like pjsip"
# Expected: res_pjsip, res_pjsip_transport_management, etc.

# 3. Check for config errors
docker exec talky-asterisk asterisk -rx "pjsip show endpoints"

# 4. Check logs for errors
docker logs talky-asterisk --tail 50 | grep -i error
```

### Problem: Metrics endpoint returns 404

```bash
# 1. Verify backend is running
curl -s http://127.0.0.1:8000/api/v1/health

# 2. Check if metrics route is registered
curl -s http://127.0.0.1:8000/metrics

# 3. Verify telephony observability module is loaded
grep "telephony_observability" backend/app/main.py
```

### Problem: Integration tests fail

```bash
# 1. Run with verbose output
TELEPHONY_RUN_DOCKER_TESTS=1 python3 -m unittest -v telephony/tests/test_telephony_stack.py 2>&1

# 2. Run single failing test
TELEPHONY_RUN_DOCKER_TESTS=1 python3 -m unittest telephony.tests.test_telephony_stack.TestClass.test_method

# 3. Check if Docker services are all healthy
docker ps --filter name=talky-
```

---

## Scheduled Monitoring

### Recommended Crontab

```bash
# Daily quick health check (6 AM)
0 6 * * * /bin/bash -c 'cd /home/ai-lab/Desktop/Talky.ai-complete- && python3 telephony/scripts/sip_options_probe.py 127.0.0.1 15060 3 >> /var/log/talky-health.log 2>&1'

# Pre-business-hours full verification (7 AM, weekdays)
0 7 * * 1-5 /bin/bash -c 'cd /home/ai-lab/Desktop/Talky.ai-complete- && bash telephony/scripts/verify_ws_a.sh telephony/deploy/docker/.env.telephony.example >> /var/log/talky-gates.log 2>&1'
```

---

## Reference

- Verifier scripts: `telephony/scripts/verify_ws_*.sh`
- Probe scripts: `telephony/scripts/sip_options_probe.py`, `sip_options_probe_tls.sh`
- Observability module: `backend/app/core/telephony_observability.py`
- Prometheus config: `telephony/observability/prometheus/prometheus.yml`
- SLO metrics reference: `telephony/docs/phase_3/03_ws_k_completion.md`
