# Voice Gateway C++ (Day 4 Baseline)

Status: Scaffold created for frozen Talk-Leee day plan execution.  
Plan reference: `telephony/docs/phase_3/19_talk_lee_frozen_integration_plan.md`

## Purpose

This service is the planned RTP/media gateway layer between:
1. Asterisk media flow
2. AI pipeline services (STT/LLM/TTS)

## Day 4 Scope (Baseline)

1. RTP send/recv baseline with 20 ms pacing.
2. Echo mode for deterministic media validation.
3. `/health` and `/stats` endpoints.
4. Session control APIs: `StartSession`, `StopSession`, `Stats`.

## Implemented in Day 4/Day 6

1. RTP packet parser/serializer and sequencer.
2. Session runtime with UDP receive + paced echo transmit (20 ms cadence).
3. Control API:
   - `POST /v1/sessions/start`
   - `POST /v1/sessions/stop`
   - `GET /v1/sessions/{session_id}/stats`
4. Operational API:
   - `GET /health`
   - `GET /ready`
   - `GET /stats`
5. Day 6 media resilience:
   - no-RTP watchdog timeout reasons (`start_timeout`, `no_rtp_timeout`, `no_rtp_timeout_hold`, `final_timeout`)
   - bounded jitter buffer (capacity + prefetch controls)
   - per-session state machine (`created -> starting -> buffering -> active/degraded -> stopped`)
5. CTest unit suite (`voice_gateway_tests`).
6. Day 4 verifier and evidence generation:
   - `telephony/scripts/verify_day4_cpp_gateway.sh`
   - `telephony/scripts/day4_rtp_probe.py`
7. Day 6 verifier and evidence generation:
   - `telephony/scripts/verify_day6_media_resilience.sh`
   - `telephony/scripts/day6_media_resilience_probe.py`

## Environment

The production executable requires all of these before it opens a listener:

- `INTERNAL_SERVICE_TOKEN`: sent as `X-Internal-Service-Token` on every
  caller-audio callback. It must match the backend value.
- `VOICE_GATEWAY_AUTH_TOKEN`: a distinct bearer secret required on session and
  control endpoints. The Asterisk adapter sends the matching value.
- `VOICE_GATEWAY_CALLBACK_HOST`: one numeric loopback IPv4 address and the only host
  permitted in `audio_callback_url`. It must match the host in the backend's
  `BACKEND_INTERNAL_URL` (normally `127.0.0.1`).
- `BACKEND_INTERNAL_URL`: the exact plain-HTTP loopback origin, including an
  explicit port (normally `http://127.0.0.1:8000`). Credentials, an implicit or
  non-canonical port, query, fragment, trailing slash, and base path are rejected.

Both secrets must contain 32-512 valid characters and must be distinct. Unset,
blank, overlong, short, reused, or control-character-bearing secrets refuse
gateway startup. A callback is accepted only when its scheme, numeric host, and
port exactly match `BACKEND_INTERNAL_URL`, its host also exactly matches
`VOICE_GATEWAY_CALLBACK_HOST`, and its path is
`/api/v1/sip/telephony/audio/<safe-session-id>`. The sender repeats that full
check immediately before attaching `INTERNAL_SERVICE_TOKEN`, so a different
loopback port cannot receive the token or caller audio. `GET /health` and aggregate
`GET /ready`/`GET /stats` remain unauthenticated read-only probes on the loopback listener;
all session/control routes require the gateway bearer token.

The current control/callback protocol is version 2 and advertises PCMU only.
Session creation is idempotent only when the repeated `session_id` carries the
same non-empty SHA-256 configuration digest; a different digest is HTTP 409.
Caller-audio callbacks carry monotonic sequence and frame metadata, are drained
in FIFO order by one bounded worker per session, and reuse one HTTP/1.1
connection after consuming each complete response. Failed delivery reconnects
with bounded retries and age; queue overflow drops the oldest audio and emits a
sequence-visible operator event rather than growing without limit.

Deploy only through `deploy_to_server.sh`: it requires a durable traffic freeze
and zero-call evidence, builds and tests the exact SHA, publishes the gateway
atomically, restarts it first, and starts the matching backend only after the
gateway is healthy. There is no legacy unauthenticated audio mode.

## Notes

1. Codec is intentionally locked to `pcmu` and `ptime_ms=20` for frozen-plan compliance.
2. Do not begin Day 7 coupling before Day 6 verifier remains green.
