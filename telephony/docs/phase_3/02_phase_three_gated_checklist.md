# Phase 3 Gated Checklist

Date: February 25, 2026  
Status: In Progress (planning baseline created)  
Rule: Workstreams execute in strict order, one closure at a time.

---

## Global Preconditions

- [x] Phase 2 sign-off confirmed (`telephony/docs/phase_2/10_phase_two_signoff.md`).
- [x] Official reference baseline created (`telephony/docs/phase_3/00_phase_three_official_reference.md`).
- [x] Execution plan approved for WS-K through WS-O (`telephony/docs/phase_3/01_phase_three_execution_plan.md`).

---

## WS-K Gate: SLO Contract and Telemetry Hardening

- [x] Telemetry schema (metrics + labels) finalized.
- [x] Recording rules for canary-vs-baseline validated.
- [x] Alert routes/grouping/inhibition validated.
- [x] SLO dashboard reviewed with operations owner.

Exit evidence:
- [x] Metrics contract doc committed (`telephony/docs/phase_3/03_ws_k_completion.md`).
- [x] Prometheus rule validation output (`telephony/scripts/verify_ws_k.sh`).
- [x] Alertmanager config validation output (`telephony/scripts/verify_ws_k.sh`).

---

## WS-L Gate: SIP Edge Canary Orchestration

- [x] Stage controller implemented (5/25/50/100 progression).
- [x] Drain/freeze rollback command validated.
- [x] Destination probing and state transitions validated.
- [x] Stage decision records created for dry run.

Exit evidence:
- [x] Canary stage script run log.
- [x] Rollback timing evidence.
- [x] Routing distribution evidence by stage.

---

## WS-M Gate: Media and Transfer Reliability

- [x] Asterisk primary runtime baseline implemented; FreeSWITCH retained as backup profile.
- [x] RTP path validated for kernel and userspace modes.
- [x] Long-call synthetic scenarios pass target.
- [x] Blind transfer synthetic scenarios pass target.
- [x] Attended transfer synthetic scenarios pass target.
- [x] `mod_xml_curl` timeout and retry limits validated.

Exit evidence:
- [x] Asterisk baseline doc (`telephony/docs/phase_3/07_ws_m_asterisk_primary_baseline.md`).
- [x] Media quality report (`telephony/docs/phase_3/08_ws_m_media_quality_report.md`).
- [x] Transfer success report (`telephony/docs/phase_3/09_ws_m_transfer_success_report.md`).
- [x] Long-call/session-timer report (`telephony/docs/phase_3/10_ws_m_long_call_session_timer_report.md`).
- [x] WS-M completion record (`telephony/docs/phase_3/11_ws_m_completion.md`).

---

## WS-N Gate: Failure Injection and Automated Recovery

- [x] WS-N implementation plan finalized (`telephony/docs/phase_3/12_ws_n_failure_injection_recovery_plan.md`).
- [x] OpenSIPS failure drill completed.
- [x] rtpengine degradation drill completed.
- [x] FreeSWITCH disruption drill completed.
- [x] Recovery and rollback commands validated.
- [x] Alert quality evaluated during fault windows.

Exit evidence:
- [x] Drill replay logs (`telephony/docs/phase_3/evidence/ws_n/`).
- [x] Recovery timeline records (`telephony/docs/phase_3/evidence/ws_n/*_timeline.log`).
- [x] Updated operational runbook/report (`telephony/docs/phase_3/13_ws_n_failure_recovery_report.md`).

---

## WS-O Gate: Production Cutover and Sign-off

- [x] Canary progression completed to 100% traffic.
- [x] Stabilization window completed without SLO breach.
- [x] Legacy path hot-standby readiness confirmed.
- [x] Final sign-off doc created and approved.
- [x] Legacy decommission readiness checklist prepared.

Exit evidence:
- [x] Cutover stage report (`telephony/docs/phase_3/16_ws_o_cutover_report.md`).
- [x] Final phase sign-off document (`telephony/docs/phase_3/18_phase_three_signoff.md`).
- [x] Decommission readiness checklist (`telephony/docs/phase_3/17_ws_o_decommission_readiness_checklist.md`).

---

## Phase 3 Exit Gate

- [x] All WS-K through WS-O gates complete.
- [x] No open P0/P1 defects in rollout scope.
- [x] Rollback procedure validated in production-like conditions.
- [x] Operational handoff and ownership complete.

If any item fails:
1. Freeze stage progression.
2. Execute rollback procedure.
3. Record incident and corrective action before retry.
