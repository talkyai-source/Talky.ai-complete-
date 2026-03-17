# Migration Plan (Current System -> Production Telephony Stack)

## Current Baseline

Current backend contains mixed telephony paths (browser/vonage/rtp/sip/freeswitch bridge).
Migration will keep existing system live while new stack is introduced in parallel.

## Phase 0 - Foundation Freeze (Week 0)

1. Freeze feature churn in telephony-critical code.
2. Document current call flows and dependencies.
3. Define migration KPIs and rollback criteria.

Deliverables:
- Inventory of active call paths
- Baseline metrics report
- Rollback playbook v1

## Phase 1 - Side-by-side Telephony Stack (Week 1-2)

1. Deploy Kamailio + rtpengine + FreeSWITCH in staging.
2. Connect one non-critical SIP trunk.
3. Keep Python backend unchanged.
4. Add health and synthetic call monitors.

Deliverables:
- Staging stack reachable and stable
- Test calls end-to-end with synthetic prompts

## Phase 2 - Python Control Integration (Week 2-3)

1. Add/standardize FreeSWITCH media bridge contract.
2. Implement transfer APIs (blind + attended orchestration).
3. Normalize provider-agnostic fields (`provider_call_id` style).
4. Keep legacy paths available as fallback.

Deliverables:
- Transfer operations tested in staging
- Unified event model for call states/transfers

## Phase 3 - Tenant BYOS SIP (Week 3-5)

1. Add per-tenant trunk/gateway config tables.
2. Build provisioning APIs and validation checks.
3. Add policy checks (codec, CPS, ACL, caller-id rules).
4. Add audit trail for all telephony config changes.

Deliverables:
- Tenant A/B can onboard their own SIP config independently
- End-to-end tenant isolation tests pass

## Phase 4 - Controlled Production Canary (Week 5-6)

1. Route 5% of production calls through new stack.
2. Compare quality, latency, transfer success, drop rate.
3. Increase to 25%, then 50% if SLOs remain green.

Deliverables:
- Canary reports
- Incident/rollback rehearsal logs

## Phase 5 - Cutover and Decommission (Week 6+)

1. Shift 100% traffic to new stack.
2. Keep legacy path hot-standby for 1-2 weeks.
3. Remove deprecated telephony code only after stability window.

Deliverables:
- Final cutover report
- Legacy removal PR plan

## Rollback Rules

Rollback immediately if any is true:
- Call setup success drops below 98.5%
- P95 response start latency exceeds 1.5s for 30+ min
- Transfer success falls below 95%
- Severe tenant routing misassignment is detected

Rollback steps:
1. Route traffic to legacy telephony path.
2. Disable new ingress routes.
3. Keep logs and packet captures for incident RCA.
