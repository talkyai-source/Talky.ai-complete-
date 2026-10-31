# Voice Gateway / Inbound Go-Live Evidence

Date: 2026-08-30
Scope: backend ↔ Asterisk ↔ C++ RTP gateway correctness and release safety.

## Engineering outcome

The repository implementation is hardened through the P0 boundary. It is not
honest to call production inbound "100% achieved" until the Linux C++ gate,
staging carrier calls, concurrency soak, canary, monitoring, and rollback drill
have produced evidence for the exact release SHA.

Implemented:

- Strict callback authentication, schema, size, base64, codec, frame, and
  path/body identity validation.
- Callback protocol v2 with monotonic sequence, frame count, ptime, payload
  length, backend deduplication, and sequence-gap metrics.
- One bounded FIFO sender per session, drop-oldest overflow policy, bounded
  freshness/retries, complete-response parsing, and persistent HTTP/1.1 reuse.
- Random gateway session IDs and canonical SHA-256 configuration sealing.
  Same-ID/same-digest retry is idempotent; different config is HTTP 409.
- Gateway health/capability contract and admission-aware readiness. Backend
  health requires ARI plus gateway protocol v2, callback v2, and PCMU.
- Exact Asterisk-provided RTP source enforcement (never first-packet learning).
- Correct non-echo state activation and terminal Failed evidence.
- Typed TTS delivery failures propagate immediately; generic transient failures
  retain their bounded compatibility breaker.
- One-second dead-media inventory with two-miss debounce, safe no-op on gateway
  query failure, and confirmed call teardown for vanished RTP sessions.
- Prometheus counters for callback outcomes, missing batches, and dead-media
  reconciliation outcomes.
- Cross-boundary backend/C++ CI triggers, CMake/CTest, warnings, TSan,
  ASan/UBSan, and shutdown smoke gates.
- CI restore proof for the PostgreSQL 16.14 historical snapshot from its proven
  `0008` floor, plus a separate historic false-`0021` forward-repair proof.
- Exact-SHA deployment, zero-session proofs, atomic gateway publication,
  protocol/readiness validation before backend restart, and documented rollback.

## Verification completed on this workstation

| Gate | Result |
|---|---:|
| Full backend unit + security suite | 7,450 passed, 7 skipped |
| Broad Asterisk/telephony unit selection | 659 passed |
| Focused release/protocol/callback/reconcile selection | 57 passed |
| Probe/controller and release-safety selection | 50 passed, 14 skipped |
| Migration bootstrap CI contract | Passed |
| Ruff undefined-name/import gate on touched Python | Passed |
| Git diff whitespace/error gate | Passed |
| GitHub workflow YAML parse | Passed |
| Bash syntax for deploy/build/gateway gate scripts | Passed |

## Evidence that cannot be produced on this workstation

This Windows host has CMake but no GCC, Clang, MSVC toolchain, Docker engine, or
installed WSL distribution. Therefore the changed C++ executable has not been
compiled locally. The required authoritative proof is the
`voice-gateway-cpp` GitHub workflow for the exact commit:

```text
cmake build + CTest
tests/run_gate.sh
  - C++20 warnings gate
  - ThreadSanitizer
  - AddressSanitizer + UndefinedBehaviorSanitizer
  - graceful shutdown/listener smoke
```

Failure or absence of that check is a release blocker, not a warning.

## Premortem and release blockers

Assume the release failed. The most likely causes and mandatory prevention are:

1. **C++ did not compile or has a race.** Require both C++ workflow jobs green
   for the exact SHA. Never deploy from a locally copied binary.
2. **Backend/gateway versions drifted.** Deploy one frozen SHA. The start ack and
   `/ready` protocol/codec checks must pass before backend restart; 409 is never
   interpreted as success.
3. **Gateway restarts under live calls.** Freeze ingress/origination and prove
   gateway plus PBX active legs are zero. The deploy script blocks otherwise.
4. **Callback stalls or loses ordering.** Alert on delivery failures, queue
   overflow, missing-batch counter growth, callback 5xx, and dead-media
   detections. Exercise delayed, closed, 4xx/5xx, and lost-ack faults in staging.
5. **A spoofed RTP datagram captures media.** Exact source address/port is
   mandatory. Prove a rogue-first packet is rejected and real Asterisk RTP still
   activates the session.
6. **A gateway crash leaves a billed silent call.** Kill the gateway during a
   staging call and prove call teardown within the configured two-miss window.
7. **Keep-alive response framing corrupts later callbacks.** Run the C++ sink
   reuse regression and staging callback soak; prove many batches use one TCP
   connection and reconnect cleanly after an idle close.
8. **Capacity saturation appears healthy.** Fill the configured test capacity;
   `/health` must remain live while `/ready` returns 503, then recover after a
   full teardown.
9. **Codec work reduces quality.** Keep PCMU for PSTN. Do not enable Opus until
   the negotiated wideband adapter gates in `docs/VOICE_GATEWAY_CODEC_DECISION.md`
   pass; transcoding narrowband audio cannot create wideband quality.
10. **Rollback kills or strands calls.** Perform a real staging freeze,
    zero-leg proof, exact rollback-SHA gateway rebuild, readiness validation,
    backend restart, reconciliation, and controlled test call. A rollback binary
    without protocol v2 is incompatible and must be rejected.

## Mandatory staging sequence

1. Push a reviewed commit; require backend full suite and both C++ jobs green.
2. Build the exact SHA with `backend/scripts/build_voice_gateway_release.sh`.
3. Deploy to staging with production-equivalent secrets and loopback topology.
4. Verify `/health`, `/ready`, `/stats`, backend shallow/deep readiness, and
   protocol/capability fields.
5. Place controlled inbound and outbound calls covering agent-first,
   caller-first, silence, DTMF, barge-in, long utterance, TTS interruption,
   carrier hangup, backend restart, gateway restart, and lost callback ack.
6. Run the concurrency/restart soak. Record CPU, RSS, file descriptors, threads,
   TIME_WAIT sockets, event-loop p95/p99, callback queue/gaps, packet loss,
   first-turn latency, STT accuracy, and final disposition/settlement.
7. Run the rollback drill while ingress is frozen and confirm the old proven
   release can make one controlled call before any canary.
8. Start with a dedicated canary DID/tenant and immutable evidence identity.
   Increase traffic only when sample floors and SLO gates pass; freeze on any
   media loss, identity mismatch, settlement anomaly, or monitor blind spot.

## Production decision

Code review and local backend evidence: **pass**.
C++ Linux build/sanitizer evidence: **pending CI**.
Production-like carrier/load/rollback evidence: **pending staging**.
Permission to simply push and treat live traffic as the test environment:
**denied by the release design**.
