# Day 9 Transfer + Tenant Controls Evidence

Date: 2026-03-03
Verifier: `telephony/scripts/verify_day9_transfer_tenant_controls.sh`

## Summary

1. Calls attempted: 6
2. Successful transfer call outcomes: 6
3. Remote BYE confirmations: 0
4. Transfer dispatch count: 2
5. Concurrency reject count: 4

## Acceptance

1. Blind transfer repeated outcomes: PASS
2. Tenant concurrency rejection behavior: PASS
3. Ghost session/bridge leak check: PASS

## Evidence Files

1. `telephony/docs/phase_3/evidence/day9/day9_verifier_output.txt`
2. `telephony/docs/phase_3/evidence/day9/day9_transfer_batch_results.json`
3. `telephony/docs/phase_3/evidence/day9/day9_transfer_probe_summary.json`
4. `telephony/docs/phase_3/evidence/day9/day9_transfer_reason_summary.json`
5. `telephony/docs/phase_3/evidence/day9/day9_concurrency_events.json`
6. `telephony/docs/phase_3/evidence/day9/day9_concurrency_policy_snapshot.json`
7. `telephony/docs/phase_3/evidence/day9/day9_ari_event_trace.log`
8. `telephony/docs/phase_3/evidence/day9/day9_gateway_runtime.log`
9. `telephony/docs/phase_3/evidence/day9/day9_gateway_session_report.json`
10. `telephony/docs/phase_3/evidence/day9/day9_ghost_session_report.json`
