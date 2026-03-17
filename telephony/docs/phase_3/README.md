# Phase 3 Docs Index

Active authority:
1. `19_talk_lee_frozen_integration_plan.md`
2. `20_status_against_frozen_talk_lee_plan.md`

Legacy WS-K to WS-O docs remain as execution evidence and do not replace the frozen day plan.

## Core Documentation

- `00_phase_three_official_reference.md`
  - Official and current reference baseline for Phase 3 decisions.
- `01_phase_three_execution_plan.md`
  - Production-ready Phase 3 implementation plan (WS-K through WS-O).
- `02_phase_three_gated_checklist.md`
  - Sequential gate checklist for Phase 3 execution and sign-off.

## Workstream Completion Records

- `03_ws_k_completion.md`
  - WS-K completion record with SLO metrics surface, recording rules, alert routing, and verifier evidence.
- `04_ws_l_stage_controller_runbook.md`
  - WS-L stage progression, rollback, and evidence runbook.
- `05_ws_l_completion.md`
  - WS-L completion record and gate closure summary.
- `06_ws_l_opensips_migration_plan.md`
  - OpenSIPS switch plan with feature mapping and acceptance criteria.
- `07_ws_m_asterisk_primary_baseline.md`
  - Asterisk-primary media runtime baseline with official do/don't guidance.
- `08_ws_m_media_quality_report.md`
  - WS-M media path validation report (RTPengine kernel/userspace checks and OpenSIPS media hooks).
- `09_ws_m_transfer_success_report.md`
  - WS-M transfer reliability synthetic report (blind + attended transfer scenarios).
- `10_ws_m_long_call_session_timer_report.md`
  - WS-M long-call and session-timer validation report.
- `11_ws_m_completion.md`
  - WS-M completion record and gate closure evidence.
- `ws-m.md`
  - Consolidated WS-M record with implementation, validation, and evidence summary.
- `12_ws_n_failure_injection_recovery_plan.md`
  - Official-reference-backed WS-N plan for failure injection and automated recovery.
- `13_ws_n_failure_recovery_report.md`
  - WS-N implementation and verification report with drill evidence contract.
- `14_ws_n_completion.md`
  - WS-N gate closure record and handoff to WS-O.
- `15_ws_o_production_cutover_plan.md`
  - WS-O production cutover plan with official-reference-backed controls.
- `16_ws_o_cutover_report.md`
  - WS-O execution and cutover evidence summary.
- `17_ws_o_decommission_readiness_checklist.md`
  - Legacy-path decommission readiness checklist.
- `18_phase_three_signoff.md`
  - Final Phase 3 sign-off record (WS-K through WS-O).
- `19_talk_lee_frozen_integration_plan.md`
  - Frozen Talk-Leee 10-day integration plan (single-path architecture, acceptance gates, official reference baseline).
- `20_status_against_frozen_talk_lee_plan.md`
  - Day-by-day status cross-check against the frozen Talk-Leee plan, with current gaps and next execution step.
- `21_phase_definition_map_and_alignment.md`
  - Phase-definition file map and applied alignment record confirming frozen-plan authority.
- `22_day4_cpp_gateway_execution_plan.md`
  - Day 4 execution plan with official-reference baseline, deterministic acceptance gates, and non-workaround constraints for the C++ RTP gateway skeleton.
- `23_day5_asterisk_cpp_e2e_echo_execution_plan.md`
  - Day 5 execution plan for official ARI externalMedia integration between Asterisk and C++ gateway with strict gate criteria.
- `24_day6_media_resilience_execution_plan.md`
  - Day 6 execution plan for C++ media resilience: no-RTP timeouts, bounded jitter buffer policy, and per-call state machine gate.
- `25_day7_stt_streaming_execution_plan.md`
  - Day 7 execution plan for official STT streaming: PCMU->PCM16 normalization, Flux turn-event handling, transcript/talklee_call_id contract, and latency stability gate.
- `26_day8_tts_bargein_execution_plan.md`
  - Day 8 execution plan for official TTS + barge-in: paced RTP playback on the C++ media path, deterministic interruption policy, and controlled reaction-time gate.
- `27_day9_transfer_tenant_controls_execution_plan.md`
  - Day 9 execution plan for official blind transfer + tenant concurrency controls: ARI transfer lifecycle, transactional lease schema, and zero-ghost-session acceptance gate.
- `28_day10_concurrency_soak_validation_execution_plan.md`
  - Day 10 final execution plan for official concurrency ramp, 120-minute soak validation, restart recovery drills, and evidence-based Go/No-Go closure.
- `inventory.md`
  - Day 1 LAN inventory baseline generated from host evidence.
- `ufw_status.md`
  - Day 1 firewall posture capture with root-command evidence notes.
- `day1_lan_closure_actions.md`
  - Remaining admin actions to fully close Day 1 acceptance.
- `runbook_asterisk.md`
  - Day 2 first-call verification runbook.
- `day2_asterisk_call_evidence.md`
  - Day 2 acceptance evidence (10-call + SIP sequence markers).
- `day3_opensips_edge_evidence.md`
  - Day 3 acceptance evidence (OpenSIPS success path + direct-block proof).
- `day4_cpp_gateway_evidence.md`
  - Day 4 acceptance evidence for C++ RTP gateway build, loopback pacing, and API checks.
- `day5_asterisk_cpp_echo_evidence.md`
  - Day 5 acceptance evidence for Asterisk <-> C++ end-to-end RTP echo gate.
- `day6_media_resilience_evidence.md`
  - Day 6 acceptance evidence for no-RTP timeouts, jitter-buffer boundedness, and media-session state machine behavior.
- `day7_stt_streaming_evidence.md`
  - Day 7 acceptance evidence for STT streaming: transcript integrity and latency stability gate.
- `day8_tts_bargein_evidence.md`
  - Day 8 acceptance evidence for TTS playback and deterministic barge-in interruption gate.
- `day9_transfer_tenant_controls_evidence.md`
  - Day 9 acceptance evidence for blind transfer reliability and tenant concurrency guard behavior.
- `day10_concurrency_soak_evidence.md`
  - Day 10 acceptance evidence for concurrency threshold, soak stability, restart recovery, and final Go/No-Go decision.

## Day Reports

- `day3.md`
  - Day 3 execution log: Runtime realignment to OpenSIPS + Asterisk active stack, backup containment for Kamailio + FreeSWITCH, comprehensive architecture deep-dive, WS-K/WS-L/WS-M verification, and full integration pass evidence.
- `day4.md`
  - Day 4 closure report: WS-K through WS-O step-wise execution, failure drill completion, production cutover validation, evidence model, and final phase sign-off status.

## Operational Workflows

- `workflows/01_canary_rollout_workflow.md`
  - Step-by-step canary rollout procedure: Stage 0→1→2→3→4 progression with SLO gates, decision flowcharts, and emergency rollback procedures.
- `workflows/02_failure_injection_drill_workflow.md`
  - Controlled failure injection drills for OpenSIPS, RTPengine, Asterisk, and multi-component failure scenarios with pass/fail criteria and evidence templates.
- `workflows/03_backup_activation_workflow.md`
  - Emergency backup stack activation: FreeSWITCH opt-in via Docker profile, validation checklists, traffic rerouting, and primary stack restoration procedures.
- `workflows/04_health_check_monitoring_workflow.md`
  - Runtime health verification: Quick checks (2 min), full verification (10 min), Prometheus SLO queries, troubleshooting guides, and scheduled monitoring patterns.

## Day Verifiers

- `telephony/scripts/verify_day1_lan_setup.sh`
  - Generates Day 1 inventory and firewall evidence docs.
- `telephony/scripts/complete_day1_root.sh`
  - Root-only Day 1 closure helper (installs required LAN tools and captures definitive UFW evidence).
- `telephony/scripts/verify_day2_asterisk_first_call.sh`
  - Executes Day 2 10-call SIP validation and captures INVITE/200/BYE evidence.
- `telephony/scripts/verify_day3_opensips_edge.sh`
  - Executes Day 3 edge enforcement checks and direct-path block validation.
- `telephony/scripts/sip_invite_call_probe.py`
  - Deterministic SIP INVITE/ACK/BYE probe used by Day 2 and Day 3 verifiers.
- `telephony/scripts/verify_day4_cpp_gateway.sh`
  - Executes Day 4 C++ gateway build/tests, RTP loopback probe, and evidence generation.
- `telephony/scripts/day4_rtp_probe.py`
  - RTP probe utility used by the Day 4 verifier to validate monotonic sequence/timestamp and pacing thresholds.
- `telephony/scripts/verify_day5_asterisk_cpp_echo.sh`
  - Executes Day 5 Asterisk ARI externalMedia integration, 20 SIP+RTP echo calls, and evidence generation.
- `telephony/scripts/day5_ari_external_media_controller.py`
  - ARI controller used by Day 5 verifier for bridge + externalMedia orchestration and deterministic session cleanup.
- `telephony/scripts/day5_sip_rtp_echo_probe.py`
  - SIP+RTP probe that validates per-call echo packet return on the Day 5 path.
- `telephony/scripts/verify_day6_media_resilience.sh`
  - Executes Day 6 media resilience validation (no-RTP timeout paths, jitter-buffer boundedness, and evidence generation).
- `telephony/scripts/day6_media_resilience_probe.py`
  - Fault-injection probe used by the Day 6 verifier to trigger timeout and jitter-buffer scenarios deterministically.
- `telephony/scripts/verify_day7_stt_streaming.sh`
  - Executes Day 7 STT streaming validation (SIP+RTP media capture, Flux transcription, transcript integrity, and latency stability evidence generation).
- `telephony/scripts/day7_stt_stream_probe.py`
  - Day 7 probe that captures echoed PCMU RTP, converts to PCM16/16k, streams to Deepgram Flux, and writes transcript/latency integrity artifacts.
- `telephony/scripts/verify_day8_tts_bargein.sh`
  - Executes Day 8 TTS playback + barge-in validation, enforces reaction-time and cleanup gates, and generates evidence artifacts.
- `telephony/scripts/day8_tts_bargein_probe.py`
  - Day 8 probe that drives TTS playback through the C++ gateway and measures barge-in stop behavior and reaction timing.
- `telephony/scripts/verify_day9_transfer_tenant_controls.sh`
  - Executes Day 9 blind transfer + tenant concurrency validation, enforces transfer outcome and cleanup gates, and generates Day 9 evidence artifacts.
- `telephony/scripts/day9_transfer_tenant_probe.py`
  - Day 9 probe that drives concurrent SIP call batches, validates remote-BYE transfer terminal behavior, and records transfer/concurrency outcomes.
- `telephony/scripts/verify_day10_concurrency_soak.sh`
  - Executes Day 10 final concurrency-ramp + soak validation, runs restart recovery drills, enforces Go/No-Go gates, and generates Day 10 evidence artifacts.
- `telephony/scripts/day10_concurrency_soak_probe.py`
  - Day 10 probe that runs staged concurrent SIP load, computes RFC6076-style setup/disconnect KPIs, and derives safe concurrency threshold + soak stability outputs.
- `telephony/scripts/day10_restart_recovery_drill.sh`
  - Day 10 recovery drill runner that validates OpenSIPS, Asterisk, RTPengine, and C++ gateway restart recovery times against SLA thresholds.

---

Phase 3 starts only after Phase 2 sign-off (`telephony/docs/phase_2/10_phase_two_signoff.md`).
