# Runbook: Day 2 Asterisk First Call Validation

## Objective

Validate baseline Asterisk call flow for extension `700` with repeated call setup and teardown.

## Command

```bash
bash telephony/scripts/verify_day2_asterisk_first_call.sh telephony/deploy/docker/.env.telephony.example
```

## What It Verifies

1. Asterisk is listening on configured internal SIP port (`ASTERISK_SIP_PORT`, frozen default `5070`).
2. Ten SIP INVITE calls to extension `700` complete with BYE.
3. Asterisk SIP logs contain the expected sequence markers:
   - `INVITE sip:`
   - `SIP/2.0 200 OK`
   - `BYE sip:`

## Evidence Files

1. `telephony/docs/phase_3/evidence/day2/day2_call_summary.json`
2. `telephony/docs/phase_3/evidence/day2/day2_asterisk_sip_log_excerpt.log`

