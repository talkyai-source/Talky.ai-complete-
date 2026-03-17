# WS-M Long-Call and Session-Timer Report

Date: February 26, 2026  
Workstream: WS-M (Media and Transfer Reliability)  
Status: Passed

---

## Scope

This report validates WS-M long-call continuity and session-timer baseline:
1. Synthetic long-call scenario executes successfully.
2. Observed duration meets gate threshold.
3. Asterisk PJSIP session timer controls are explicitly configured.

---

## Validation Method

Primary command:

```bash
bash telephony/scripts/verify_ws_m.sh telephony/deploy/docker/.env.telephony.example
```

Long-call checks performed:
1. Synthetic call to `Local/longcall@wsm-synthetic`.
2. Duration marker parsed from `/tmp/ws_m_results.log`.
3. Gate threshold enforced: `>= 10s` (CI-safe deterministic threshold).

Session-timer checks performed:
1. `telephony/asterisk/conf/pjsip.conf` contains:
   - `timers=yes`
   - `timers_min_se=90`
   - `timers_sess_expires=1800`

---

## Results

1. Synthetic long-call execution: PASS
2. Long-call duration threshold: PASS
3. Session timer baseline markers: PASS

Evidence files:
1. `telephony/docs/phase_3/evidence/ws_m_longcall_check.txt`
2. `telephony/docs/phase_3/evidence/ws_m_synthetic_results.log`

---

## Operational Notes

1. The 10-second gate threshold is intentionally optimized for repeatable CI/runtime verification.
2. Production canary windows continue to use longer observation durations via workflow gates.
3. Explicit session timer configuration reduces long-call drift and interoperability risk.

---

## Exit Statement

WS-M long-call and session-timer validation is complete and meets current phase gate requirements.
