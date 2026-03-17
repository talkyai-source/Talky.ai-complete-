# Day 4 Execution Plan: C++ Gateway Skeleton + RTP Echo

Date: 2026-03-02  
Plan authority: `telephony/docs/phase_3/19_talk_lee_frozen_integration_plan.md`  
Day scope: Day 4 only (no Day 5 coupling yet)

---

## 1) Objective

Implement a production-grade Day 4 baseline for `services/voice-gateway-cpp` that proves:
1. RTP send/receive correctness.
2. 20 ms pacing accuracy.
3. Stable RTP sequence/timestamp behavior.
4. Deterministic echo mode in loopback/unit validation.
5. Operational observability through `/health` and `/stats`.

Day 4 explicitly does **not** include PBX integration logic (that starts Day 5).

---

## 2) Official Reference Baseline (Latest/Authoritative)

Source validation date: 2026-03-02

RTP/IETF:
1. RFC 3550 (RTP core): https://www.rfc-editor.org/rfc/rfc3550
2. RFC 3551 (AVP profile, static PT mapping): https://www.rfc-editor.org/rfc/rfc3551
3. RFC 4733 (DTMF RTP payload): https://www.rfc-editor.org/rfc/rfc4733

Asterisk official docs:
1. WebSocket channel + external media overview: https://docs.asterisk.org/Configuration/Channel-Drivers/WebSocket/
2. Latest ARI Channels REST API (`/channels/externalMedia`): https://docs.asterisk.org/Latest_API/API_Documentation/Asterisk_REST_Interface/Channels_REST_API/
3. Versioned ARI Channels REST API (Certified 22.8): https://docs.asterisk.org/Certified-Asterisk_22.8_Documentation/API_Documentation/Asterisk_REST_Interface/Channels_REST_API/
4. res_pjsip endpoint/identify behavior: https://docs.asterisk.org/Asterisk_21_Documentation/API_Documentation/Module_Configuration/res_pjsip/

OpenSIPS official docs:
1. OpenSIPS rtpengine module 3.6 (latest stable branch docs): https://opensips.org/html/docs/modules/3.6.x/rtpengine

Build system official docs:
1. CMake presets manual: https://cmake.org/cmake/help/latest/manual/cmake-presets.7.html

Version pinning note:
1. Standards (RFCs) are normative and versionless for this use case.
2. Asterisk project runtime remains pinned to Certified 22.8 compatibility, while API checks are performed against `Latest_API` to avoid drift.
3. OpenSIPS runtime policy remains compatible with 3.4+ syntax; references are anchored on 3.6 docs for current behavior.

---

## 3) Why This Plan Is Professional and Non-Workaround

This plan is deliberately strict:
1. Uses protocol behavior from RTP standards instead of ad-hoc packet timing.
2. Locks codec to PCMU (`PT=0`, 8 kHz, 20 ms frames) as frozen-plan baseline.
3. Separates Day 4 RTP correctness from Day 5 PBX integration to avoid mixed-failure debugging.
4. Enforces measurable acceptance gates (jitter, sequence/timestamp continuity, packet counters).
5. Adds deterministic stats and reason-coded failure paths from day one.
6. Keeps implementation minimal but production-shaped (state machine, monotonic pacing, structured logs).
7. Bans Day 4 anti-patterns:
   - no wall-clock pacing (`system_clock`) in RTP sender;
   - no implicit codec fallback;
   - no silent packet drops without counters/reason.

---

## 4) Day 4 Architecture (Local/Unit Validation Only)

```mermaid
flowchart LR
    A[Test RTP Source] --> B[voice-gateway-cpp RTP RX]
    B --> C[Echo Engine]
    C --> D[voice-gateway-cpp RTP TX]
    D --> E[Test RTP Sink]
    B --> F[/stats]
    B --> G[/health]
```

No Asterisk/OpenSIPS call control coupling in Day 4.

---

## 5) Protocol and Media Contract (Frozen Baseline)

PCMU/ulaw baseline:
1. RTP payload type: `0` (PCMU per RFC 3551 static table).
2. Clock rate: `8000 Hz`.
3. Packetization: `20 ms`.
4. Samples per packet: `160`.
5. Payload bytes per packet: `160` (8-bit G.711 sample).

RTP header behavior:
1. Sequence increments by 1 per sent RTP packet (RFC 3550).
2. Timestamp increments by `160` per 20 ms PCMU frame at 8 kHz.
3. Initial sequence/timestamp values are randomized at session start (RFC 3550 recommendation).
4. SSRC is per-session stable for the lifetime of a session.

---

## 6) Service API Contract (Day 4)

Control API (HTTP/JSON):
1. `POST /v1/sessions/start`
2. `POST /v1/sessions/stop`
3. `GET /v1/sessions/{session_id}/stats`

Ops endpoints:
1. `GET /health`:
   - returns process liveness and dependency readiness (`ok`/`degraded`).
2. `GET /stats`:
   - process-level counters and current active session counts.

StartSession request (minimum):
1. `session_id`
2. `listen_ip`
3. `listen_port`
4. `remote_ip`
5. `remote_port`
6. `codec` (must be `pcmu`)
7. `ptime_ms` (must be `20`)

StopSession request:
1. `session_id`
2. optional `reason`

---

## 7) Internal Design (Day 4)

Core components:
1. `RtpPacket`:
   - parse/serialize RTP headers and payload.
2. `RtpSession`:
   - per-call state, counters, pacing schedule, SSRC/seq/timestamp.
3. `RtpReceiver`:
   - UDP receive path and validation.
4. `RtpTransmitter`:
   - paced UDP send path using monotonic clock (`steady_clock`).
5. `EchoProcessor`:
   - echoes received payload with outgoing RTP header policy.
6. `SessionRegistry`:
   - session lifecycle and lookup by `session_id`.
7. `HttpControlServer`:
   - `/health`, `/stats`, start/stop/stats session endpoints.

Concurrency model:
1. One IO runtime for UDP + HTTP event loops.
2. Session state guarded by mutex/lock-free counters where appropriate.
3. No busy loops; paced send uses deadline scheduling (`sleep_until` style).

Logging model:
1. Structured log lines with fields:
   - `session_id`, `event`, `seq`, `ts`, `ssrc`, `packets_in`, `packets_out`, `reason`.

---

## 8) Production Hardening Included in Day 4 (Minimum)

1. Input validation on all control payloads.
2. Reject unsupported codec/ptime combinations.
3. Socket bind failures return explicit reason codes.
4. Graceful shutdown stops all active sessions cleanly.
5. `/health` returns non-200 when critical IO loop is unhealthy.
6. `/stats` never blocks media path.
7. Control API defaults to loopback bind and explicit port configuration.
8. Session operations are idempotent (`start` duplicate returns conflict-safe response, `stop` on missing returns deterministic "already_stopped").

---

## 9) Test Plan (Day 4)

Unit tests:
1. RTP header encode/decode roundtrip.
2. Sequence progression and rollover handling.
3. Timestamp increment correctness (`+160` at 20 ms PCMU).
4. Invalid packet handling (short header, wrong version).
5. Session start/stop idempotency behavior.

Component tests:
1. UDP loopback echo with synthetic RTP stream.
2. Pacing jitter test (target 20 ms with tolerance bound).
3. Packet loss/reorder simulation and stats accounting.
4. `/health` and `/stats` endpoint response validation.

Acceptance tests (Day 4 gate):
1. RTP loopback passes with no malformed output.
2. Sequence monotonicity passes across sustained run.
3. Timestamp monotonicity passes with expected increments.
4. Pacing drift/jitter within threshold over test window:
   - p95 inter-packet delta in `[19ms, 21ms]`;
   - max inter-packet delta <= `25ms` in non-stress local tests.
5. All stats counters and reason fields are populated.
6. Soak sanity run (30 minutes, single session) shows no unbounded memory growth.

---

## 10) Acceptance Gate Definition (Day 4 -> Day 5 unlock)

Day 4 is `Complete` only when all conditions are true:
1. `services/voice-gateway-cpp` builds via CMake and Dockerfile.
2. Session APIs (`StartSession`, `StopSession`, `Stats`) are implemented and tested.
3. `/health` and `/stats` are implemented and tested.
4. RTP echo loopback test suite passes.
5. Logs demonstrate stable sequence and timestamp progression (no jumps).
6. Evidence artifacts are committed under `telephony/docs/phase_3/evidence/day4/`.
7. Deterministic pass/fail output is produced by a single scripted verifier (`verify_day4_cpp_gateway.sh`).

If any gate fails:
1. Day 4 remains open.
2. Day 5 is blocked.

---

## 11) Evidence Artifacts to Produce

Required artifacts:
1. `telephony/docs/phase_3/evidence/day4/day4_build_output.txt`
2. `telephony/docs/phase_3/evidence/day4/day4_rtp_loopback_results.json`
3. `telephony/docs/phase_3/evidence/day4/day4_pacing_analysis.txt`
4. `telephony/docs/phase_3/evidence/day4/day4_stats_endpoint_sample.json`
5. `telephony/docs/phase_3/evidence/day4/day4_log_excerpt.txt`

Required summary doc:
1. `telephony/docs/phase_3/day4_cpp_gateway_evidence.md`

---

## 12) Implementation Checklist (Execution Order)

1. Create `RtpPacket` model + tests.
2. Create `RtpSession` state machine + counters.
3. Add UDP RX/TX paths.
4. Add paced TX scheduler (20 ms cadence).
5. Add echo mode.
6. Add `/health` and `/stats`.
7. Add session control endpoints.
8. Run full Day 4 test suite.
9. Generate evidence bundle.
10. Mark Day 4 status in frozen tracker.
11. Add explicit "open issues" section (if any) in Day 4 evidence doc; unresolved items block Day 5.

---

## 13) Known Risks and Mitigations

Risk 1: Timing drift under CPU contention.  
Mitigation:
1. monotonic clock scheduling.
2. drift accumulation metric.
3. threshold-based failure in tests.

Risk 2: Packet burst causes queue growth.  
Mitigation:
1. bounded queue per session.
2. explicit dropped-packet counter.
3. health degradation flag if threshold exceeded.

Risk 3: Header math bugs cause audio artifacts later.  
Mitigation:
1. strict unit tests for seq/timestamp increments.
2. decode/encode roundtrip tests.
3. pcap-based validation in Day 5.

Risk 4: Hidden coupling with PBX before Day 4 closure.  
Mitigation:
1. keep Day 4 isolated from PBX integration.
2. only move to Day 5 on full gate pass.

---

## 14) Decision-to-Source Mapping

| Decision | Why | Source |
|---|---|---|
| Sequence increments by 1 per RTP packet | Standards-compliant RTP sender behavior | RFC 3550 |
| Timestamp is sampling-clock based and monotonic | Enables sync and jitter correctness | RFC 3550 |
| PCMU payload baseline with 8 kHz | Frozen codec baseline and static RTP PT mapping | RFC 3551 |
| Keep DTMF payload handling explicit for future | Avoid hidden telephony signaling regressions | RFC 4733 |
| Keep PBX integration out of Day 4 | Isolates RTP math validation from call-control noise | Frozen day plan |
| Preserve future compatibility with Asterisk externalMedia | Official supported path for RTP/websocket media channels | Asterisk ARI/chan_websocket docs |

---

## 15) Exit Criteria for Today

Today is successful if:
1. This Day 4 plan is approved as implementation baseline.
2. Day 4 implementation starts only against this plan.
3. No Day 5 work begins until Day 4 evidence gate is closed.
