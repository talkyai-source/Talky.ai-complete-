# Day 10 Execution Plan: Concurrency + Soak Validation (Final Go/No-Go)

Date: 2026-03-03  
Plan authority: `telephony/docs/phase_3/19_talk_lee_frozen_integration_plan.md`  
Day scope: Day 10 only (final production-readiness gate on top of Day 9)

---

## 1) Objective

Close the frozen 10-day plan with an official, measurable production-readiness gate for the telephony runtime:

`OpenSIPS -> Asterisk -> ARI External Media -> C++ Gateway -> AI Pipeline`

Mandatory Day 10 outcomes:
1. Safe concurrency threshold is measured, documented, and reproducible.
2. Sustained soak run proves runtime stability (no resource leak trend, no control-path collapse).
3. Recovery behavior is validated under controlled service restarts.
4. Final Go/No-Go checklist is generated from objective evidence only.

---

## 2) Scope and Non-Scope

In scope:
1. Load generation and concurrency ramp on the active telephony path only.
2. Soak validation with mixed call behaviors (baseline, barge-in, blind transfer).
3. Restart-based recovery drills during live load.
4. Capacity and headroom report (CPU, memory, network, call metrics).
5. Final Day 10 verifier and evidence package.

Out of scope (explicitly blocked for Day 10):
1. New architecture changes, codec changes, or routing rewrites.
2. New transfer feature development (Day 9 already closed this gate).
3. Provider migration or experimental media paths.
4. Any workaround bypassing OpenSIPS/Asterisk/ARI/C++ gateway ownership.

---

## 3) Official Reference Baseline (Authoritative + Proven OSS)

Source validation date: 2026-03-03

IETF/RFC:
1. SIP core protocol behavior:  
   https://www.rfc-editor.org/rfc/rfc3261
2. SIP session timers and refresh semantics:  
   https://www.rfc-editor.org/rfc/rfc4028
3. SIP service examples for call-flow baselines:  
   https://www.rfc-editor.org/rfc/rfc5359
4. SIP end-to-end performance metrics taxonomy (SRD, SDD, etc.):  
   https://www.rfc-editor.org/rfc/rfc6076

Asterisk official docs:
1. ARI Channels REST API (`externalMedia`, `rtp_statistics`, transfer control):  
   https://docs.asterisk.org/Latest_API/API_Documentation/Asterisk_REST_Interface/Channels_REST_API/
2. `res_pjsip` endpoint/session-timer controls (`timers`, `timers_min_se`, `timers_sess_expires`, `tenantid`):  
   https://docs.asterisk.org/Asterisk_20_Documentation/API_Documentation/Module_Configuration/res_pjsip/
3. Asterisk test framework guidance (unit + functional test strategy):  
   https://docs.asterisk.org/Test-Suite-Documentation/

OpenSIPS official docs:
1. Dispatcher load distribution and destination probing:  
   https://opensips.org/html/docs/modules/3.4.x/dispatcher.html
2. RTPengine integration module (offer/answer/delete/manage):  
   https://opensips.org/html/docs/modules/3.4.x/rtpengine.html
3. Rate limiting controls (`rl_check`, algorithms, distributed counters):  
   https://opensips.org/docs/modules/devel/ratelimit

RTPengine official docs:
1. Runtime behavior and timeout/silent-timeout controls:  
   https://rtpengine.readthedocs.io/en/latest/rtpengine.html

Observability/operations official docs:
1. Prometheus recording rules (capacity trend + rate aggregations):  
   https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/
2. Prometheus alerting rules (`for`, labels, firing semantics):  
   https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/
3. Docker Compose service dependencies and health orchestration:  
   https://docs.docker.com/reference/compose-file/services/
4. Docker Compose restart behavior (drill execution semantics):  
   https://docs.docker.com/reference/cli/docker/compose/restart/

Proven open-source implementation patterns (reference only, no copy/paste):
1. Asterisk official external-media sample app (media/control lifecycle patterns):  
   https://github.com/asterisk/asterisk-external-media
2. SIPp official docs for deterministic SIP load generation and ramp controls:  
   https://sipp.readthedocs.io/en/latest/
3. SIPp call-rate control details (`-r`, `-rp`, `-m`, rate ramp):  
   https://sipp.readthedocs.io/en/v3.6.1/controlling.html

---

## 4) Day 10 Design Principles

1. No architecture drift: Day 10 validates the frozen production path, it does not redesign it.
2. Metrics-before-opinion: all decisions are based on measured outputs and stored artifacts.
3. Standard terminology: report SIP KPIs using RFC 6076-aligned naming.
4. Safety-first thresholding: publish only a threshold with headroom, never the absolute break point as production target.
5. Deterministic cleanup: every run ends with explicit leak checks (ARI channels/bridges, gateway sessions, tenant leases).
6. Controlled failure only: restart and impairment drills are scripted, bounded, and reversible.
7. No workaround policy is mandatory.

---

## 5) Workload Model and Measurement Contract

### 5.1 Load Profiles

Profile mix for Day 10 runs:
1. `P1 Baseline AI Turn` (no transfer): 50%
2. `P2 Barge-In Active` (Day 8 policy path): 30%
3. `P3 Blind Transfer Attempt` (Day 9 path): 20%

Traffic model rules:
1. All calls enter via OpenSIPS edge.
2. Media is forced through ARI externalMedia + C++ gateway path.
3. Transfer and tenant controls stay enabled exactly as Day 9 acceptance baseline.

### 5.2 Ramp and Soak Policy

Ramp phases (default):
1. Stage R1: 10 concurrent calls for 5 minutes
2. Stage R2: 20 concurrent calls for 5 minutes
3. Stage R3: 30 concurrent calls for 5 minutes
4. Stage R4: 40 concurrent calls for 5 minutes
5. Stage R5: 50 concurrent calls for 5 minutes

Threshold policy:
1. The first stage that breaches any hard gate marks `break_stage`.
2. `safe_concurrency_threshold` = previous passing stage.
3. Production recommendation = `safe_concurrency_threshold * 0.8` (20% headroom).

Soak policy:
1. Duration: 120 minutes minimum.
2. Concurrency: recommended production value above.
3. Mixed profile traffic remains enabled for full soak.

### 5.3 KPI Contract

SIP control KPIs (RFC 6076-aligned naming):
1. Session setup success ratio
2. Session Request Delay (`SRD`) p50/p95/p99
3. Session Disconnect Delay (`SDD`) p50/p95/p99
4. Ineffective Session Attempts (`ISA`) percentage

Runtime KPIs:
1. Calls completed per minute
2. Transfer attempt/success/failure/reject counters
3. Barge-in reaction p95
4. Active session and lease counts

System KPIs:
1. CPU utilization (per service + host aggregate)
2. Memory RSS trend (per service + host aggregate)
3. Network throughput and packet drop counters
4. Container restart count and recovery time

Hard gate defaults (Day 10 initial values):
1. Session setup success ratio >= 99.0%
2. `SRD p95 <= 2000 ms`
3. `SDD p95 <= 1500 ms`
4. `ISA <= 1.0%`
5. Transfer success ratio (transfer-attempted subset) >= 95.0%
6. Barge-in reaction `p95 <= 250 ms`
7. Zero leaked ARI external channels/bridges/gateway sessions at run end

---

## 6) Planned Implementation Steps (Day 10)

Step 1: Freeze Day 9 baseline
1. Re-run Day 9 verifier and capture pass artifact hash.
2. Block Day 10 execution if Day 9 cleanup invariants fail.

Step 2: Build Day 10 load harness
1. Add deterministic Day 10 probe orchestration for mixed profiles.
2. Support staged concurrency ramp and timed soak mode.
3. Persist machine-readable per-stage summaries.

Step 3: Add Day 10 verifier
1. Add `telephony/scripts/verify_day10_concurrency_soak.sh`.
2. Enforce hard gates and fail fast on breach.
3. Produce final Go/No-Go JSON + Markdown report.

Step 4: Add restart recovery drills under load
1. Execute controlled restart of OpenSIPS, Asterisk, RTPengine, and C++ gateway.
2. Measure detection and recovery intervals.
3. Ensure post-recovery calls return to pass baseline.

Step 5: Capacity and headroom reporting
1. Publish threshold stage, recommended production concurrency, and evidence traces.
2. Capture bottleneck notes and tuning actions (if any) without changing architecture.

Step 6: Final closure gate
1. Confirm Day 10 acceptance criteria.
2. Produce sign-off-ready artifact pack.

---

## 7) Day 10 Test Strategy and Proper Test Cases

### 7.1 Test Matrix

| ID | Test Case | Method | Pass Criteria | Evidence Output |
|---|---|---|---|---|
| D10-TC-01 | Harness sanity | Run 20 calls at low load to validate probe + verifier wiring. | 20/20 calls complete; artifacts generated; no script error. | `day10_harness_smoke.json`, `day10_verifier_output.txt` |
| D10-TC-02 | Concurrency ramp | Execute R1..R5 stages (5 min each) with mixed profiles. | Stage-level KPIs satisfy hard gates through highest passing stage. | `day10_ramp_stage_results.json` |
| D10-TC-03 | Threshold identification | Continue ramp until first gate breach; compute safe threshold and 20% headroom value. | `safe_concurrency_threshold` and `recommended_concurrency` computed deterministically. | `day10_capacity_threshold_report.json` |
| D10-TC-04 | Soak stability (120 min) | Run mixed traffic at recommended concurrency for 2h. | No hard gate breach for 120 min; no monotonic degradation trend. | `day10_soak_summary.json`, `day10_soak_timeseries.csv` |
| D10-TC-05 | Transfer under load | Keep Day 9 transfer profile active during ramp/soak. | Transfer success ratio >= 95%; no uncontrolled transfer failure burst. | `day10_transfer_load_report.json` |
| D10-TC-06 | Barge-in under load | Keep Day 8 barge-in profile active during ramp/soak. | Barge-in reaction p95 <= 250 ms; no stuck TTS playback state. | `day10_bargein_load_report.json` |
| D10-TC-07 | Tenant isolation fairness | Drive at least 2 tenants concurrently; saturate one tenant intentionally. | Saturated tenant gets deterministic rejects; other tenant maintains >= 98% setup success. | `day10_tenant_fairness_report.json` |
| D10-TC-08 | Asterisk restart recovery | Restart `talky-asterisk` mid-load using scripted drill. | Service recovers within SLA window; call success returns to baseline within 2 minutes. | `day10_recovery_asterisk.json`, `day10_recovery_timeline.log` |
| D10-TC-09 | OpenSIPS restart recovery | Restart `talky-opensips` mid-load. | SIP ingress recovers within SLA window; no persistent call-setup collapse. | `day10_recovery_opensips.json` |
| D10-TC-10 | RTPengine restart recovery | Restart `talky-rtpengine` mid-load. | Media path recovers; no persistent one-way-audio spike after recovery window. | `day10_recovery_rtpengine.json` |
| D10-TC-11 | C++ gateway restart recovery | Restart gateway process/container mid-load. | New calls recover to baseline; stale sessions are cleaned; no ghost sessions. | `day10_recovery_gateway.json` |
| D10-TC-12 | Long-call session timer integrity | Keep long-duration calls active during soak and monitor refresh behavior. | Session timer behavior consistent with configured policy; no premature teardown burst. | `day10_session_timer_report.json` |
| D10-TC-13 | End-state leak audit | Compare baseline vs post-run ARI channels/bridges, gateway sessions, leases. | Zero leaked external channels, bridges, gateway sessions, and active transfer leases. | `day10_leak_audit_report.json` |
| D10-TC-14 | Go/No-Go checklist generation | Generate final decision file from measured outputs only. | Checklist complete; decision is deterministic (`go` or `no-go`) with reasons. | `day10_go_no_go_checklist.md`, `day10_go_no_go.json` |

### 7.2 Restart Recovery SLA Targets (Initial)

1. OpenSIPS signaling recovery: <= 60 seconds
2. Asterisk control-plane recovery: <= 90 seconds
3. RTPengine media recovery: <= 90 seconds
4. C++ gateway recovery: <= 60 seconds
5. Return-to-baseline call KPIs after each drill: <= 120 seconds

If any SLA target fails:
1. Day 10 remains open.
2. Final sign-off is blocked.

---

## 8) Acceptance Criteria (Day 10 Complete)

Day 10 is complete only when all conditions pass:
1. Safe concurrency threshold is measured and documented with 20% production headroom.
2. 120-minute soak run passes without hard-gate breach.
3. Restart recovery drills pass SLA targets for all required services.
4. No ghost sessions/bridges/channels/leases remain after run.
5. Final Go/No-Go report is generated with objective evidence.
6. Day 10 verifier returns deterministic pass/fail.

If any condition fails:
1. Day 10 is `Not Complete`.
2. Production sign-off remains blocked.

---

## 9) Planned Day 10 Evidence Artifacts

1. `telephony/docs/phase_3/evidence/day10/day10_verifier_output.txt`
2. `telephony/docs/phase_3/evidence/day10/day10_harness_smoke.json`
3. `telephony/docs/phase_3/evidence/day10/day10_ramp_stage_results.json`
4. `telephony/docs/phase_3/evidence/day10/day10_capacity_threshold_report.json`
5. `telephony/docs/phase_3/evidence/day10/day10_soak_summary.json`
6. `telephony/docs/phase_3/evidence/day10/day10_soak_timeseries.csv`
7. `telephony/docs/phase_3/evidence/day10/day10_transfer_load_report.json`
8. `telephony/docs/phase_3/evidence/day10/day10_bargein_load_report.json`
9. `telephony/docs/phase_3/evidence/day10/day10_tenant_fairness_report.json`
10. `telephony/docs/phase_3/evidence/day10/day10_recovery_asterisk.json`
11. `telephony/docs/phase_3/evidence/day10/day10_recovery_opensips.json`
12. `telephony/docs/phase_3/evidence/day10/day10_recovery_rtpengine.json`
13. `telephony/docs/phase_3/evidence/day10/day10_recovery_gateway.json`
14. `telephony/docs/phase_3/evidence/day10/day10_session_timer_report.json`
15. `telephony/docs/phase_3/evidence/day10/day10_leak_audit_report.json`
16. `telephony/docs/phase_3/evidence/day10/day10_recovery_timeline.log`
17. `telephony/docs/phase_3/evidence/day10/day10_go_no_go.json`
18. `telephony/docs/phase_3/day10_concurrency_soak_evidence.md`

Verifier/probe targets:
1. `telephony/scripts/verify_day10_concurrency_soak.sh`
2. `telephony/scripts/day10_concurrency_soak_probe.py`
3. `telephony/scripts/day10_restart_recovery_drill.sh`

---

## 10) Final-Day Execution Rule

1. Day 10 runs only after Day 9 acceptance is closed.
2. No tuning changes are applied during the official measurement window unless a full rerun is performed.
3. Go/No-Go is evidence-driven only; no subjective override.
