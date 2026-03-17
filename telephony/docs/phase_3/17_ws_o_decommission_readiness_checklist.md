# WS-O Legacy Decommission Readiness Checklist

Date: February 26, 2026  
Scope: Legacy telephony path decommission readiness (not decommission execution)

---

## 1) Preconditions

- [x] WS-K completed (telemetry and alerts stable).
- [x] WS-L completed (canary orchestration and rollback validated).
- [x] WS-M completed (media + transfer reliability validated).
- [x] WS-N completed (failure and recovery drills passed).
- [x] WS-O cutover verifier passed.

---

## 2) Traffic and Stability

- [x] 100% cutover stage reached with evidence.
- [x] Stabilization window completed without critical SLO breach.
- [x] No unresolved P1 issues in telephony rollout scope.

---

## 3) Rollback and Recovery

- [x] Rollback command path remains available.
- [x] Recovery timelines are documented from WS-N drills.
- [x] On-call runbook contains failure-drill and rollback references.

---

## 4) Backup and Standby Posture

- [x] Legacy FreeSWITCH backup profile can be started on demand.
- [x] Backup runtime health and CLI checks are operational.
- [x] Backup activation does not disturb active OpenSIPS + Asterisk path.

---

## 5) Decommission Readiness Decision

Decision: **Ready for controlled decommission planning in next phase**, with these constraints:
1. Keep legacy path available as standby until formal decommission window is approved.
2. Require change window + rollback checkpoint before any destructive decommission action.
3. Capture final stakeholder approval before disabling backup profile permanently.

