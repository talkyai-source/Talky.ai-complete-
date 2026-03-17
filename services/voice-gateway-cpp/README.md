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

## Notes

1. Codec is intentionally locked to `pcmu` and `ptime_ms=20` for frozen-plan compliance.
2. Do not begin Day 7 coupling before Day 6 verifier remains green.
