# Phase 1 Gated Checklist (Sequential Execution)

Date: February 23, 2026
Owner: Talky.ai platform team
Rule: Do not start WS-B until WS-A is fully complete and signed off.

---

## Execution Rules

1. Workstreams are strictly sequential for this implementation:
   - WS-A -> WS-B -> WS-C -> WS-D -> WS-E
2. A workstream is "Complete" only when all acceptance gates are met and evidence is attached.
3. If any acceptance check fails, status must move back to `Blocked` or `In Progress`.
4. No "soft pass" based on manual assumption.

---

## Status Legend

- `Not Started`
- `In Progress`
- `Blocked`
- `Complete`

Current Global Status: `WS-A, WS-B, WS-C, WS-D, WS-E Complete`

---

## WS-A: Telephony Infrastructure Bootstrap

Status: `Complete`
Start Date: 2026-02-23
End Date: 2026-02-23

### Scope

1. Production-shaped staging stack for:
   - Kamailio (SIP edge)
   - rtpengine (media relay)
   - FreeSWITCH (B2BUA and app control)
2. Dispatcher-based route template.
3. ESL baseline and SIP profile template.
4. Health and synthetic verification entry points.

### Acceptance Gates (All Required)

1. `docker compose config` passes without errors.
2. All three services report healthy/running.
3. Kamailio config syntax validation passes.
4. FreeSWITCH config sanity command succeeds.
5. Synthetic SIP OPTIONS check passes through Kamailio route.
6. RTP relay process is reachable and reporting.

### Evidence Links

1. Compose validation output:
   - `docker compose --env-file telephony/deploy/docker/.env.telephony.example -f telephony/deploy/docker/docker-compose.telephony.yml config -q` -> pass
2. Container health output:
   - `docker compose --env-file telephony/deploy/docker/.env.telephony.example -f telephony/deploy/docker/docker-compose.telephony.yml ps` -> all three services `Up` and `healthy`
3. Kamailio lint output:
   - `docker compose --env-file telephony/deploy/docker/.env.telephony.example -f telephony/deploy/docker/docker-compose.telephony.yml exec -T kamailio kamailio -c -f /etc/kamailio/kamailio.cfg` -> `config file ok, exiting...`
4. FreeSWITCH sanity output:
   - `docker compose --env-file telephony/deploy/docker/.env.telephony.example -f telephony/deploy/docker/docker-compose.telephony.yml exec -T freeswitch fs_cli -p ClueCon -x status` -> `FreeSWITCH ... is ready`
5. SIP synthetic output:
   - `bash telephony/scripts/verify_ws_a.sh telephony/deploy/docker/.env.telephony.example` -> `PASS: SIP response ... SIP/2.0 200 OK`
6. RTP relay output:
   - `docker compose --env-file telephony/deploy/docker/.env.telephony.example -f telephony/deploy/docker/docker-compose.telephony.yml exec -T rtpengine sh -lc "ss -lun | grep ':2223'"` -> UDP listener reachable on `0.0.0.0:2223`

### Sign-Off

- Technical Owner: Platform implementation (Codex)
- Date: 2026-02-23
- Decision: `Approved`

### Gate Decision

WS-B unlock condition: `Locked by process rule` (WS-A is complete; do not start WS-B until explicit instruction)

---

## WS-B: Security and Signaling Baseline

Status: `Complete`
Start Date: 2026-02-23
End Date: 2026-02-23

### Scope

1. Kamailio TLS transport listener and profile baseline.
2. Kamailio trusted-source ACL policy via permissions module.
3. Kamailio flood/rate controls via pike and ratelimit modules.
4. FreeSWITCH ESL hardening to loopback bind and loopback ACL.
5. Deterministic verification script for WS-B controls.

### Acceptance Gates (All Required)

1. WS-A baseline remains green after WS-B changes.
2. Kamailio WS-B config syntax validation passes.
3. TLS listener is reachable and SIP OPTIONS over TLS succeeds.
4. Kamailio security directives are present and loaded.
5. FreeSWITCH ESL is loopback-bound and loopback ACL protected.

### Evidence Links

1. WS-B verifier:
   - `bash telephony/scripts/verify_ws_b.sh telephony/deploy/docker/.env.telephony.example` -> `WS-B verification PASSED.`
2. TLS SIP probe:
   - `bash telephony/scripts/sip_options_probe_tls.sh 127.0.0.1 15061 5` -> `PASS: TLS SIP response: SIP/2.0 200 OK`
3. Kamailio syntax:
   - `docker compose --env-file telephony/deploy/docker/.env.telephony.example -f telephony/deploy/docker/docker-compose.telephony.yml exec -T kamailio kamailio -c -f /etc/kamailio/kamailio.cfg` -> `config file ok, exiting...`
4. FreeSWITCH ESL hardening:
   - `ss -ltn | grep ':8021'` -> listener on `127.0.0.1:8021`
   - `telephony/freeswitch/conf/autoload_configs/event_socket.conf.xml` -> `apply-inbound-acl="loopback.auto"`

### Sign-Off

- Technical Owner: Platform implementation (Codex)
- Date: 2026-02-23
- Decision: `Approved`

### Gate Decision

WS-C unlock condition: `Locked by process rule` (WS-B is complete; do not start WS-C until explicit instruction)

---

## WS-C: Call Control and Transfer Baseline

Status: `Complete`
Start Date: 2026-02-23
End Date: 2026-02-23

### Scope

1. Blind transfer baseline with `uuid_transfer`.
2. Attended transfer baseline with `att_xfer`.
3. REFER deflect baseline (`uuid_deflect` / `deflect`) with answered-call precondition.
4. Transfer outcome state machine and metrics.
5. WS-C verifier and integration test suite.

### Acceptance Gates (All Required)

1. Blind transfer reliability >= 99% in staged run.
2. Attended transfer complete and cancel flows pass.
3. Deflect path validated for answered calls only.
4. Deterministic transfer outcome state for all attempts.
5. No stuck channels/orphaned legs in soak run.

### Evidence Links

1. WS-C verifier (includes WS-B prerequisite and WS-C tests):
   - `bash telephony/scripts/verify_ws_c.sh telephony/deploy/docker/.env.telephony.example` -> `WS-C verification PASSED.`
2. Backend WS-C transfer tests:
   - `cd backend && ./venv/bin/python -m unittest -v tests.unit.test_freeswitch_transfer_control tests.unit.test_freeswitch_transfer_api` -> `Ran 9 tests ... OK`
3. Telephony integration tests (docker mode):
   - `TELEPHONY_RUN_DOCKER_TESTS=1 python3 -m unittest -v telephony/tests/test_telephony_stack.py` -> `OK`
4. WS-C implementation plan and standards references:
   - `telephony/docs/10_ws_c_call_control_transfer_plan.md`

### Sign-Off

- Technical Owner: Platform implementation (Codex)
- Date: 2026-02-23
- Decision: `Approved`

### Gate Decision

WS-D unlock condition: `Unlocked for implementation` (WS-C complete)

---

## WS-D: Media Bridge and Latency Baseline

Status: `Complete`
Start Date: 2026-02-23
End Date: 2026-02-23

### Scope

1. FreeSWITCH media-fork/WebSocket bridge stability validation.
2. End-to-end audio format contract and frame policy standardization.
3. Queue/backpressure profiling and guardrails.
4. Latency baseline instrumentation and SLO verification.
5. WS-D verifier and test coverage.

### Evidence Links

1. WS-D verifier:
   - `bash telephony/scripts/verify_ws_d.sh telephony/deploy/docker/.env.telephony.example` -> `WS-D verification PASSED.`
2. Backend WS-D unit tests:
   - `cd backend && ./venv/bin/python -m pytest -q tests/unit/test_browser_media_gateway_ws_d.py tests/unit/test_latency_tracker.py` -> `21 passed`
3. Telephony integration tests (docker mode):
   - `TELEPHONY_RUN_DOCKER_TESTS=1 python3 -m unittest -v telephony/tests/test_telephony_stack.py` -> includes `test_ws_d_verifier_passes`
4. WS-D implementation and baseline docs:
   - `telephony/docs/12_ws_d_media_bridge_latency_plan.md`
   - `telephony/docs/phase1_baseline_latency.md`

### Sign-Off

- Technical Owner: Platform implementation (Codex)
- Date: 2026-02-23
- Decision: `Approved`

### Gate Decision

WS-E unlock condition: `Unlocked for implementation` (WS-D complete)

---

## WS-E: Canary and Rollback Control

Status: `Complete`
Start Date: 2026-02-23
End Date: 2026-02-23

### Scope

1. Progressive canary routing controls over stable/canary lanes.
2. Immediate and durable rollback automation.
3. SLO-driven promotion/abort gates.
4. WS-E verifier and integration tests.

### Evidence Links

1. WS-E verifier:
   - `bash telephony/scripts/verify_ws_e.sh telephony/deploy/docker/.env.telephony.example` -> `WS-E verification PASSED.`
2. Telephony integration suite:
   - `TELEPHONY_RUN_DOCKER_TESTS=1 python3 -m unittest -v telephony/tests/test_telephony_stack.py` -> `Ran 13 tests ... OK`
3. WS-E plan + implementation docs:
   - `telephony/docs/13_ws_e_canary_rollback_plan.md`
   - `telephony/docs/14_ws_e_canary_rollback_implementation.md`

### Sign-Off

- Technical Owner: Platform implementation (Codex)
- Date: 2026-02-23
- Decision: `Approved`
