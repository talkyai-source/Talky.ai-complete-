# WS-O Plan: Production Cutover and Sign-off

Date prepared: February 26, 2026  
Workstream: WS-O  
Status: Planned for execution (implementation-ready)

---

## 1) Objective

Execute a controlled, evidence-backed cutover from staged canary to full traffic and complete Phase 3 sign-off.

WS-O outcomes:
1. Stage progression completed through 100%.
2. Stabilization window completed without SLO breach.
3. Legacy path retained as verified hot standby.
4. Sign-off and decommission readiness documented.

---

## 2) Official Basis

1. OpenSIPS dispatcher behavior and destination state handling:  
   https://opensips.org/html/docs/modules/3.4.x/dispatcher.html
2. OpenSIPS RTPengine control model for media stability:  
   https://opensips.org/html/docs/modules/3.4.x/rtpengine.html
3. Docker Compose startup and health-gated dependencies:  
   https://docs.docker.com/compose/how-tos/startup-order/
4. Prometheus alerting rules and SLO gate implementation:  
   https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/
5. Alertmanager routing/grouping/inhibition for signal quality:  
   https://prometheus.io/docs/alerting/latest/alertmanager/
6. SIP core and session-timer standards for continuity expectations:  
   https://www.rfc-editor.org/rfc/rfc3261  
   https://www.rfc-editor.org/rfc/rfc4028

---

## 3) Non-Negotiable Rules

1. No stage advancement without SLO gate pass evidence.
2. No direct jump to 100% unless sequential stages were completed.
3. No cutover acceptance without stabilization-window health checks.
4. No sign-off without validated rollback path and hot-standby readiness.
5. No decommission of legacy stack in WS-O; only readiness confirmation.

---

## 4) Cutover Flow

Cutover sequence:
1. `0%` baseline
2. `5%` smoke
3. `25%` controlled load
4. `50%` parity
5. `100%` full cutover

At each stage:
1. Apply stage through canary controller.
2. Verify SIP UDP/TLS probes.
3. Record stage decision and timeline evidence.

Gate input modes:
1. Production mode: set `WS_O_METRICS_URL` (and optionally `WS_O_METRICS_TOKEN`) to enforce real metrics gates.
2. Verifier mode: if `WS_O_METRICS_URL` is unset, deterministic synthetic metrics are used for local replay.

---

## 5) Stabilization and Safety

Stabilization checks during post-100% window:
1. OpenSIPS health remains green.
2. Asterisk health remains green.
3. RTPengine health remains green.
4. SIP UDP/TLS probes remain successful.

Failure policy:
1. Freeze progression.
2. Trigger rollback command path.
3. Record incident timeline and block sign-off.

---

## 6) Hot-Standby Validation

Legacy path requirement:
1. FreeSWITCH backup profile must start and report healthy status.
2. Backup activation must not impact primary path signaling.
3. Backup runtime command path (`fs_cli`) must be functional.

---

## 7) Deliverables

1. Cutover verifier script (`telephony/scripts/verify_ws_o.sh`).
2. Cutover timeline and summary evidence (`telephony/docs/phase_3/evidence/ws_o/`).
3. WS-O report (`telephony/docs/phase_3/16_ws_o_cutover_report.md`).
4. Decommission readiness checklist (`telephony/docs/phase_3/17_ws_o_decommission_readiness_checklist.md`).
5. Phase 3 sign-off record (`telephony/docs/phase_3/18_phase_three_signoff.md`).
