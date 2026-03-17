# WS-N Plan: Failure Injection and Automated Recovery

Date prepared: February 26, 2026  
Workstream: WS-N  
Status: Planned (ready for implementation)

---

## 1) Objective

Execute controlled failure drills and prove deterministic recovery/rollback for the active stack:
1. OpenSIPS outage.
2. RTPengine degradation.
3. FreeSWITCH backup disruption (while backup role is retained).
4. Optional combined outage replay.

This workstream must produce RCA-grade evidence and keep rollback one-command and repeatable.

---

## 2) Official Basis (Evidence-Backed)

The plan is anchored only on official vendor/standards guidance:
1. OpenSIPS dispatcher module (probing and destination state control):  
   https://opensips.org/html/docs/modules/3.4.x/dispatcher.html
2. OpenSIPS rtpengine module (offer/answer/delete flow ownership):  
   https://opensips.org/html/docs/modules/3.4.x/rtpengine.html
3. RTPengine architecture and operation (kernel/userspace behavior):  
   https://rtpengine.readthedocs.io/en/mr13.4/overview.html  
   https://rtpengine.readthedocs.io/en/mr13.4/usage.html
4. FreeSWITCH event socket hardening and outbound behavior:  
   https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_event_socket_1048924/  
   https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Client-and-Developer-Interfaces/Event-Socket-Library/Event-Socket-Outbound_3375460/
5. Docker Compose startup/readiness gating (`depends_on` + `service_healthy`):  
   https://docs.docker.com/compose/how-tos/startup-order/
6. Prometheus alerting and Alertmanager routing/grouping/inhibition:  
   https://prometheus.io/docs/alerting/latest/alertmanager/  
   https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/
7. SIP failure semantics and session continuity standards:  
   https://www.rfc-editor.org/rfc/rfc3261  
   https://www.rfc-editor.org/rfc/rfc4028

---

## 3) Non-Negotiable Engineering Rules

1. No unbounded failure actions (no random kill/restart loops).
2. No hidden state mutation; all drill actions must be scripted and logged.
3. No stage progression after drill failure; rollback first, then RCA.
4. No silent recovery claims; each drill must emit objective timestamps.
5. No widening of active runtime ownership; OpenSIPS + Asterisk remain primary.
6. FreeSWITCH remains backup-only during WS-N.

---

## 4) WS-N Deliverables

Planned new artifacts:
1. `telephony/scripts/failure_drill_opensips.sh`
2. `telephony/scripts/failure_drill_rtpengine.sh`
3. `telephony/scripts/failure_drill_freeswitch_backup.sh`
4. `telephony/scripts/failure_drill_combined.sh`
5. `telephony/scripts/verify_ws_n.sh`
6. `telephony/docs/phase_3/13_ws_n_failure_recovery_report.md`
7. `telephony/docs/phase_3/evidence/ws_n/` (drill logs and metrics snapshots)

Planned updates:
1. `telephony/tests/test_telephony_stack.py` with WS-N static + integration checks.
2. `telephony/docs/phase_3/02_phase_three_gated_checklist.md` WS-N gate closure.
3. `telephony/docs/phase_3/README.md` index updates.

---

## 5) Drill Matrix

## Drill N1: OpenSIPS Outage

Injection:
1. Stop `talky-opensips` for controlled window.

Required observations:
1. Alert fires for SIP edge unavailability.
2. SIP probe fails during outage and recovers after restart.
3. Recovery is health-gated before traffic acceptance.

Acceptance:
1. Recovery path deterministic and repeatable.
2. Post-recovery SIP OPTIONS returns 200.
3. No unresolved degraded state after drill.

## Drill N2: RTPengine Degradation

Injection:
1. Restart/degrade `talky-rtpengine`.

Required observations:
1. Control socket returns healthy state post-restart.
2. Signaling path remains available when media path is recovering.
3. Media-related alerts are actionable and deduplicated.

Acceptance:
1. Service recovers within defined runtime target.
2. No persistent RTP relay state corruption.

## Drill N3: FreeSWITCH Backup Disruption

Injection:
1. Start backup profile, then disrupt FreeSWITCH process/container.

Required observations:
1. Primary stack unaffected (OpenSIPS + Asterisk still stable).
2. Backup monitor alerting is correct (not escalated as primary outage).
3. Backup restart path works and status returns to healthy.

Acceptance:
1. Correct severity and routing of alerts.
2. No impact on active path call routing.

## Drill N4: Combined Controlled Failure (Optional but recommended)

Injection:
1. Sequentially interrupt two components in bounded window.

Required observations:
1. Runbook is sufficient for deterministic recovery.
2. Alert storms are suppressed by grouping/inhibition.

Acceptance:
1. Full-stack return to known-good state.
2. All health probes green and verifier passes.

---

## 6) Implementation Sequence (Strict Order)

1. Build a WS-N preflight helper that captures:
   - service health snapshot
   - baseline metrics snapshot
   - drill metadata (operator, timestamp, target, stage)
2. Implement N1 script and verifier checks.
3. Implement N2 script and verifier checks.
4. Implement N3 script and verifier checks.
5. Add optional N4 combined drill.
6. Integrate `verify_ws_n.sh` into telephony test harness.
7. Generate WS-N report and close gate.

No parallel gate closure; each drill closes only after its own pass criteria are met.

---

## 7) Observability and Evidence Contract

Each drill must write:
1. `*_pre.prom` baseline metrics snapshot.
2. `*_post.prom` recovery metrics snapshot.
3. `*_timeline.log` with UTC timestamps for:
   - injection start
   - first alert observed
   - restart initiated
   - healthy confirmation
   - final probe pass
4. `*_result.json` with machine-readable pass/fail fields.

WS-N gate is not closable if any drill lacks full evidence set.

---

## 8) Security and Safety Controls

1. Failure scripts run only against compose service names in allowlist.
2. No direct destructive host commands outside bounded container controls.
3. No credential dumps in logs; redact environment output.
4. Drill scripts fail fast on missing health checks or missing evidence directory.
5. Recovery step always runs in `trap` cleanup handler where feasible.

---

## 9) Definition of Done (WS-N)

WS-N is complete only when:
1. All required drills pass (`N1`, `N2`, `N3`).
2. Optional `N4` is either passed or formally deferred with rationale.
3. `verify_ws_n.sh` passes locally and in test harness.
4. Gated checklist WS-N section is fully checked.
5. WS-N report and evidence files are committed.

---

## 10) Immediate Next Action

Execution will start with N1 (OpenSIPS outage drill) and preflight evidence scaffolding, then proceed in sequence to N2 and N3.

