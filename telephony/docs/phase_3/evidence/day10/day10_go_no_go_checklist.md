# Day 10 Historical SIP-Harness Checklist (Invalidated)

Generated (UTC): 2026-03-04T11:26:36.018745+00:00
Invalidated: 2026-08-29
Scoped decision: INVALIDATED
Production release verdict: NO-GO

This historical harness did not send or receive RTP. It therefore could not
measure two-way audio or barge-in, and its unavailable barge-in result was
incorrectly counted as a pass. It also predates the transfer-disabled
controller/trace contract and has no candidate-bound external live-media
manifest. Do not use it for release approval.

## Blocking Failures

- FAIL: barge-in not measured
- FAIL: external live-media evidence missing
- FAIL: transfer-disabled scope not proven by the corrected verifier
