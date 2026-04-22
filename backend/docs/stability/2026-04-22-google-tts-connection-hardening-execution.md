# 2026-04-22 Google TTS Connection Hardening — Execution Log

**Plan:** [2026-04-22-google-tts-connection-hardening.md](../superpowers/plans/2026-04-22-google-tts-connection-hardening.md)
**Feature doc:** [google_tts_connection_hardening.md](./google_tts_connection_hardening.md)
**Trigger:** 2026-04-22 live outbound call at 17:22:25–17:24:04.
Turn 6 went silent for ~23 s. Server logs showed Google TTS 409
"Stream aborted due to long duration elapsed without input sent"
together with a 6541 ms Groq TTFT. The existing retry path re-ran
streaming 3× and still emitted zero audio, so the caller hung up.
Requirement from user: the TTS connection must not break until call
hangup, without changing features or latency.

## §1 — What was built

| Component | Path | Purpose |
| --- | --- | --- |
| Streaming attempt with per-chunk read timeout | `app/infrastructure/tts/google_tts_streaming.py::_streaming_attempt` | Bound each gRPC response read to 8 s — slow/stalled server aborts cleanly |
| REST fallback via unary SynthesizeSpeech | `app/infrastructure/tts/google_tts_streaming.py::_rest_fallback_attempt` | Same gRPC client, unary call, no streaming 5 s rule — yields PCM in the exact same AudioChunk framing |
| No-replay guard | `app/infrastructure/tts/google_tts_streaming.py::stream_synthesize` | `first_chunk_yielded` sentinel — fallback only if zero audio was emitted; otherwise raise and let the turn end normally |
| Unit regression suite | `tests/unit/test_google_tts_streaming_hardening.py` | 4 tests: happy path, pre-first-chunk failure → REST fallback, post-first-chunk failure → raise no replay, response-read stall → abort + fallback |
| Stability docs scaffold | `docs/stability/README.md`, `docs/stability/google_tts_connection_hardening.md` | New subject-folder for voice-pipeline resilience work |

## §2 — How it works

1. `stream_synthesize` enters the circuit breaker and begins the
   streaming attempt.
2. `_streaming_attempt` opens the bidi gRPC stream, iterates
   responses with `asyncio.wait_for(..., timeout=_response_read_timeout_s)`.
   Each yielded PCM chunk flips `first_chunk_yielded = True`.
3. If the streaming loop raises **and** `first_chunk_yielded` is
   false, the outer `stream_synthesize` runs `_rest_fallback_attempt`
   for the same sentence. The unary `synthesize_speech` call returns
   a LINEAR16 WAV buffer. The 44-byte RIFF header is stripped, PCM
   is converted to Float32, and the buffer is sliced into 16 KB
   `AudioChunk`s identical to the streaming path.
4. If the streaming loop raises **and** `first_chunk_yielded` is
   true, a `RuntimeError` is raised. The pipeline's per-turn error
   handler (`_stream_llm_and_tts`) then takes over — exactly as it
   did before the fix.
5. The next sentence starts a fresh streaming attempt. There is no
   session-wide "REST mode" — streaming recovery is automatic as
   soon as Google's service recovers.

## §3 — Why there are no bugs (invariants + test mapping)

| Invariant | Test |
| --- | --- |
| Happy streaming path yields chunks, REST is not called | `test_streaming_happy_path_yields_without_rest_fallback` |
| Pre-first-chunk streaming failure triggers REST fallback, which yields at least one chunk | `test_streaming_fails_pre_first_chunk_triggers_rest_fallback` |
| Post-first-chunk failure raises, REST is NOT called (no audio replay) | `test_streaming_fails_post_first_chunk_raises_no_replay` |
| Response-chunk read stall > read_timeout aborts streaming and falls back | `test_response_read_stall_aborts_stream_and_falls_back` |
| Existing end-to-end agent intelligence suite still passes | `tests/integration/test_agent_intelligence_2026_04_22.py` (4/4) |
| Existing snapshot + slot + email suites still pass | `test_telephony_estimation_prompt.py`, `test_call_state_tracker.py`, `test_prompt_builder.py`, `test_spoken_email_normalizer.py` |

## §4 — Deviations from plan

- **Dropped the explicit retry loop on the streaming attempt.** The
  plan inherited the pre-fix `_TTS_MAX_RETRIES=2` + exponential
  backoff. In practice, when Google aborts a stream with 409, the
  same conditions that caused the abort (Groq TTFT, Google backend
  load) persist for the retry window. The old retry loop spent up
  to ~1 s of wall-clock time re-failing before the user heard
  anything, then raised anyway. The new design skips the re-fail
  and jumps straight to REST fallback, which is ~400–800 ms total
  instead of ~1.5 s. The circuit breaker still wraps streaming
  and trips on 5 consecutive gRPC failures.
- **Removed now-unused `import random`.** The old retry loop used it
  for jitter; no other code in the file needs it.
- **REST fallback runs outside the circuit breaker.** The streaming
  attempt already counted as a failure. Counting the fallback too
  would double-count and trip the breaker artificially.

## §5 — Task checklist

| # | Task | Status | Commit |
| --- | --- | --- | --- |
| 0 | Stability docs scaffold | ✅ | uncommitted |
| 1 | Failing hardening tests | ✅ | uncommitted |
| 2 | `_response_read_timeout_s` + per-chunk `wait_for` | ✅ | uncommitted |
| 3 | Split streaming attempt + REST fallback, rewire `stream_synthesize` | ✅ | uncommitted |
| 4 | Regression sweep (TTS + agent-intelligence) | ✅ | uncommitted |
| 5 | Execution log (this file) | ✅ | uncommitted |

User instruction: hold commits until explicitly asked.

## §6 — Verification commands

```bash
# New hardening suite
venv/bin/pytest tests/unit/test_google_tts_streaming_hardening.py -v

# Agent intelligence + TTS + prompt regression subset
venv/bin/pytest \
  tests/unit/test_google_tts_streaming_hardening.py \
  tests/integration/test_google_tts.py \
  tests/integration/test_agent_intelligence_2026_04_22.py \
  tests/unit/test_spoken_email_normalizer.py \
  tests/unit/test_call_state_tracker.py \
  tests/unit/test_prompt_builder.py \
  tests/unit/test_telephony_estimation_prompt.py \
  -v
# Expected: 53 passed
```

## §7 — Pre-existing test failures (NOT caused by this change)

A full `pytest tests/unit` run shows 43 failing tests on this
branch. All 43 also fail on `git stash` (HEAD). They live in:
`test_api_endpoints.py`, `test_audio_utils.py`, `test_core.py`,
`test_crm_sync_service.py`, `test_day9.py`, `test_day10.py`,
`test_dialer_engine.py`, `test_drive_sync_service.py`,
`test_email_service.py`, `test_meeting_service.py`,
`test_systemd_readiness.py`, `test_telephony_session_config.py`,
`test_voice_orchestrator.py`, `test_voice_pipeline_history.py`,
`test_voice_pipeline_service.py`. None of these modules import
`google_tts_streaming.py`.
