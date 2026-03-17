# WS-N Failure Injection and Recovery Report

Date: February 26, 2026  
Workstream: WS-N (Failure Injection and Automated Recovery)  
Status: Complete

---

## 1) Scope

WS-N validates deterministic recovery for controlled failure scenarios on the telephony runtime:
1. N1: OpenSIPS outage.
2. N2: RTPengine degradation/restart.
3. N3: FreeSWITCH backup disruption while primary path remains active.
4. N4: Combined failure replay (optional, operator-controlled).

---

## 2) Implemented Artifacts

Scripts:
1. `telephony/scripts/failure_drill_opensips.sh`
2. `telephony/scripts/failure_drill_rtpengine.sh`
3. `telephony/scripts/failure_drill_freeswitch_backup.sh`
4. `telephony/scripts/failure_drill_combined.sh`
5. `telephony/scripts/verify_ws_n.sh`
6. `telephony/scripts/ws_n_common.sh`

Evidence directory:
1. `telephony/docs/phase_3/evidence/ws_n/`

---

## 3) Verification Command

```bash
bash telephony/scripts/verify_ws_n.sh telephony/deploy/docker/.env.telephony.example
```

Optional combined drill:

```bash
WS_N_RUN_COMBINED=1 bash telephony/scripts/verify_ws_n.sh telephony/deploy/docker/.env.telephony.example
```

Observed verifier outcome:
1. `WS-N verification PASSED.`
2. N1 passed: `recovery_seconds=5`
3. N2 passed: `recovery_seconds=6`
4. N3 passed: `recovery_seconds=6`

---

## 4) Evidence Contract

Each drill writes:
1. `*_timeline.log` — timestamped event timeline.
2. `*_pre.prom` — pre-drill metrics snapshot.
3. `*_post.prom` — post-recovery metrics snapshot.
4. `*_result.json` — machine-readable pass/fail outcome.

Required result files:
1. `n1_opensips_result.json`
2. `n2_rtpengine_result.json`
3. `n3_freeswitch_backup_result.json`

Optional result file:
1. `n4_combined_result.json`

---

## 5) Alert Signal Quality Checks

WS-N verifies baseline alert quality prerequisites from WS-K:
1. Prometheus alert rule markers are present (`TalkyTelephonyMetricsScrapeFailed` and related telephony alerts).
2. Alertmanager route grouping is configured (`group_by`).
3. Alertmanager inhibition is configured (`inhibit_rules`).
4. Team-scoped routing markers are present (`team="telephony"`).

---

## 6) Gate Closure Criteria

WS-N gate is closable only when:
1. N1, N2, and N3 scripts complete with `status=passed`.
2. Result JSON contracts are valid and timelines exist.
3. Recovery thresholds are within configured limits.
4. Evidence files are present under `telephony/docs/phase_3/evidence/ws_n/`.

Gate closure record:
1. WS-N gate checklist items are marked complete in `telephony/docs/phase_3/02_phase_three_gated_checklist.md`.
2. Required evidence files and timeline logs are present and validated by `verify_ws_n.sh`.

---

## 7) Official References

1. OpenSIPS dispatcher module: https://opensips.org/html/docs/modules/3.4.x/dispatcher.html
2. OpenSIPS rtpengine module: https://opensips.org/html/docs/modules/3.4.x/rtpengine.html
3. RTPengine docs: https://rtpengine.readthedocs.io/en/mr13.4/overview.html
4. FreeSWITCH mod_event_socket: https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Modules/mod_event_socket_1048924/
5. FreeSWITCH Event Socket Outbound: https://developer.signalwire.com/freeswitch/FreeSWITCH-Explained/Client-and-Developer-Interfaces/Event-Socket-Library/Event-Socket-Outbound_3375460/
6. Docker Compose startup/readiness: https://docs.docker.com/compose/how-tos/startup-order/
7. Prometheus alerting rules: https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/
8. Alertmanager docs: https://prometheus.io/docs/alerting/latest/alertmanager/
9. RFC 3261: https://www.rfc-editor.org/rfc/rfc3261
10. RFC 4028: https://www.rfc-editor.org/rfc/rfc4028
