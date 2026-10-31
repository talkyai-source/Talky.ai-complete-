# Production acceptance resume — 2026-09-18

## Outcome

Release preparation advanced; production deployment and call acceptance are **not complete**.
No live service restart, Asterisk configuration replacement, migration, DID reassignment,
or test call was performed in this resumed session. Existing checkout edits were preserved.

## User choices

- Target inbound tenant: AllStateEstimation (`allestateestimation@gmail.com`).
- Tenant ID: `1845a165-08aa-4554-bcec-2d31ac523662`.
- Intended DID: `+442046132300`; reassignment must use the audited admin workflow.
- Operator participation: user confirmed available; interactive sudo is still required.
- Proposed test recording notice: “This call is recorded for quality and training purposes.”
- Successful-transfer and outbound destination: not selected. Await a user-controlled
  answering number or confirmed answering PBX extension. Carrier SIP account `150001`
  must not be assumed to be an answering device.
- Failed transfer: use a controlled rejecting test endpoint, not an arbitrary public number.
  This endpoint has not been provisioned or exercised.

## Fresh evidence

### Production baseline

- `/opt/talky` HEAD: `2f34c72ecef26827768180019a97b989e4537269`.
- Production checkout status was clean.
- API, gateway and Asterisk were active, each with zero systemd restarts reported.
- API readiness: `ready=true`, `draining=false`, `active_sessions=0`.
- Deep health: `ready=true`, database and Redis `ok`.
- Zero sessions is only a snapshot; it does not prove incoming traffic is frozen.
- Gateway `/ready`: `ready=true`, protocol `2`, codec `pcmu`.
- Running gateway build identity: `f9fccd893fa72290e87381293531d6affb76dd98`.
- Running gateway SHA-256:
  `96a7fdfcd3c2adb9633fe87e50ef215a15b37da10e764d5b6326b287a71a2ced`.
- Non-interactive sudo returned: `sudo: a password is required`.

### Isolated candidate build

Frozen candidate on origin/main:
`db8b1381863b3a414741fc5a3d5e07cd33ef9e20`.

Only committed gateway source and its supported release builder were archived from
that exact Git object. Dirty working-tree files were not included.

- Remote artifact directory: `/home/admins/talky-resume-db8b1381.cdLpJ1`.
- Source archive SHA-256, verified locally and remotely:
  `035a10cd8fa26664c61d7e8c09ca02f7eb2e9e73da69bfc77055a3c1dc87d331`.
- Builder: `backend/scripts/build_voice_gateway_release.sh`.
- Explicit `VOICE_GATEWAY_BUILD_SHA` set to the frozen candidate above.
- Output: `/home/admins/talky-resume-db8b1381.cdLpJ1/voice_gateway`.
- Build log: `/home/admins/talky-resume-db8b1381.cdLpJ1/build.log`.
- Candidate binary SHA-256:
  `df141dae75663845551d01b491c3d634216b326c7c40c51bbfd47ba4ce913911`.
- Builder exited `0`. Its missing-token startup check requires exit `2` before
  publishing the output binary.

Actual test output:

```text
1/4 Test #1: voice_gateway_tests ................. Passed    0.00 sec
2/4 Test #2: voice_gateway_concurrency_tests ..... Passed   53.61 sec
3/4 Test #3: voice_gateway_fixes_tests ........... Passed   84.23 sec
4/4 Test #4: voice_gateway_http_shutdown_tests ... Passed    0.20 sec
100% tests passed, 0 tests failed out of 4
Total Test time (real) = 138.05 sec
```

This is candidate build evidence, not a completed deployment or end-to-end call proof.
The supported deployment must perform its own gates and install/restart sequence.

### Partial rollback preparation

Copied the existing runtime binary without overwriting an existing backup to:
`/home/admins/talky-resume-db8b1381.cdLpJ1/voice_gateway.rollback`.

Its SHA-256 matches the running binary hash listed above. The live gateway remained
on its original build after the copy. This is **not yet a complete immediately usable
rollback package**: protected configuration capture, code/schema compatibility and
the approved traffic-disable procedure remain to be verified.

## Acceptance board

| Requirement | Current result | Remaining proof |
| --- | --- | --- |
| Corrected Asterisk reconciliation | Not run in this session | Operator-assisted official check, reviewed diff and supported apply |
| SIP registrations | Not freshly verified | Read actual Asterisk registrations before and after deployment |
| Latest gateway | Isolated build and 4 suites passed | Supported installation, restart and matching live `/ready` identity |
| Inbound route to AllStateEstimation | User choice confirmed; no mutation | Target configuration and audited reassignment with independent approval |
| Configured greeting | Not tested | Real inbound call plus audible recording/transcript evidence |
| Successful transfer | Not tested | Confirmed answering device and supported transfer readiness |
| Failed transfer | Not tested | Controlled failure and proven caller recovery/teardown |
| Recording/transcript/summary/billing | Not tested | Correlated artifacts and duration/charge proof for new test calls |
| Outbound regression | Not tested | Approved user-controlled destination and complete call evidence |
| Exact release/config freeze | Candidate and live binary identified | Protected config snapshot and deployed identity verification |
| Immediate rollback | Binary preserved only | Complete compatible package and approved traffic-disable procedure |

## Operator handoff

First, run these read-only checks in an operator SSH terminal, entering sudo locally:

```bash
sudo bash /opt/talky/backend/scripts/reconcile_asterisk_release.sh --check-only
sudo asterisk -rx 'pjsip show registrations'
```

Share the check results with credentials redacted. Do not hand-edit configuration.

Before a release, the supported script requires a short-lived, candidate-bound drain
manifest and its SHA-256. It must attest frozen carrier ingress and outbound origination,
zero active counts, evidence references, and two independent approvals. A willingness
to assist, or an idle gateway snapshot, does not satisfy this gate. Do not fabricate
approvers, approve for another person, or bypass the manifest to deploy.

## Not run / not changed

- Full backend, Talk-Leee and Admin canonical suites were not rerun this session.
- No application source fixes, commit, push, or production deployment occurred.
- No production environment values or credentials were copied into this report.
- No recording was enabled and no customer was called.
- Existing uncommitted QA/PBX work is not included in the frozen main candidate.

## Premortem controls

- Restart during a new call: require durable ingress/origination freeze, not only zero sessions.
- Wrong tenant receives calls: use audited reassignment, retain independent approval.
- Transfer loop or unsolicited customer call: require a confirmed answering test endpoint.
- Broken restoration after deploy: preserve compatible code, binary, configuration and schema evidence.
- False completion from unit tests: require real call IDs, audible greeting and stored artifacts.
