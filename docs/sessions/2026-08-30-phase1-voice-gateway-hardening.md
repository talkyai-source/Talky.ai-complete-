# Phase 1 Implementation & Hardening Record — 2026-08-30

**Scope:** Telephony P0 Correctness, TTS Delivery Error Propagation, Inbound Callback Validation, and Probe Separation
**Standard:** Engineering Rules ([rules.md](../../rules.md)), Multi-agent safety ([CLAUDE.md](../../CLAUDE.md))

---

## 1. Summary of Changes

### A. TTS Delivery Correctness & Circuit Breaker
- **File:** `backend/app/infrastructure/telephony/telephony_media_gateway.py`
- **Root Cause:** `TelephonyMediaGateway.send_audio` was previously swallowing all `send_tts_audio` exceptions in a generic `try/except` block and logging a warning. If the C++ gateway was unreachable or disconnected, the audio stream loop continued indefinitely, causing silent dead air for the caller while reporting false delivery progress.
- **Fix:**
  - Added `consecutive_tts_failures: int = 0` and `last_tts_error_warn_at: float = 0.0` to `TelephonySession`.
  - Reset `consecutive_tts_failures = 0` on every successful chunk delivery.
  - Rate-limited warnings on intermittent failures.
  - Implemented circuit breaker: upon 5 consecutive packet delivery failures, `send_audio` raises `TtsDeliveryError`, allowing upper-layer orchestrators to trigger prompt fallback or hangup cleanly.

### B. Gateway Audio Callback Rejection & Validation
- **File:** `backend/app/api/v1/endpoints/telephony_bridge.py` (`receive_gateway_audio`)
- **Root Cause:** The `/audio/{session_id}` endpoint previously caught all exceptions and returned `JSONResponse({"status": "ok"})` on invalid JSON, empty bodies, and base64 decode failures, masking data corruption and routing anomalies.
- **Fix:**
  - Rejects invalid/malformed JSON with `HTTP 400 Bad Request`.
  - Rejects non-object/empty payloads with `HTTP 400 Bad Request`.
  - Enforces path vs. body `session_id` identity match (rejects mismatches with `HTTP 400`).
  - Validates the 8 kHz mu-law contract (`pcmu`, `ulaw`, `audio/pcmu`) and rejects linear PCM/PCM16 rather than mis-decoding it as mu-law.
  - Validates Base64 encoding (rejects corrupted Base64 strings with `HTTP 400`).
  - Enforces 20ms PCMU packet frame length multiples (160 bytes; rejects invalid frame lengths with `HTTP 400`).

### C. C++ Voice Gateway Probe Separation (`/ready` vs `/health`)
- **File:** `services/voice-gateway-cpp/src/http_server.cpp`
- **Root Cause:** Deployment orchestrators and load balancers require distinct liveness (`/health`) and readiness (`/ready`) endpoints.
- **Fix:**
  - Added `/ready` to unauthenticated public probe routes.
  - Implemented `GET /ready` as an admission signal: it returns 503 when the listener is stopping or session capacity is exhausted.
  - Health/readiness advertise protocol v2 and PCMU capability; the backend refuses incompatible gateway acknowledgements.

### D. Session and callback protocol correctness
- Start requests carry a canonical SHA-256 configuration digest. Same-ID/same-digest retries are idempotent; same-ID/different-config requests return 409.
- Callback protocol v2 includes sequence, frame count, ptime, and payload length. The backend rejects inconsistent metadata and deduplicates retries before STT.
- The per-session callback sender is FIFO and bounded, retries only within a one-second freshness window, and reuses a fully-drained HTTP/1.1 connection.
- RTP source enforcement compares every packet with the exact Asterisk-provided address and port; it no longer trusts whichever datagram arrives first.
- Non-echo AI sessions become Active on their first valid RTP packet, and Failed remains terminal evidence instead of being overwritten by Stopped.

### E. Dead-media detection and release safety
- A one-second media watchdog compares backend ownership with the gateway's live session inventory.
- Two consecutive explicit misses end the SIP call in roughly two seconds; an unreachable gateway advances no counters, preventing mass teardown on a probe outage.
- Deployment validates the restarted gateway's readiness, protocol, codec, and zero active sessions before restarting the backend.

---

## 2. Verification & Test Evidence

### Python Unit Test Suites
```bash
backend/.venv/Scripts/python -m pytest backend/tests/unit/test_telephony_gateway_audio_validation.py backend/tests/unit/test_telephony_bridge_auth.py backend/tests/unit/test_telephony_media_gateway.py -q
```
**Result:**
```
57 passed in the release/protocol/callback/reconcile gate (latest focused run)
```

### Full Telephony Unit Suite
```bash
backend/.venv/Scripts/python -m pytest backend/tests/unit -k telephony -q
```
**Result:**
```
659 passed, 6067 deselected, 78 warnings in 44.47s
```

### Code Style & Linter
```bash
backend/.venv/Scripts/python -m ruff check backend/app/ --select F --extend-ignore F401,F841
```
**Result:**
```
All checks passed!
```

---

## 3. Unverified / Host-Dependent Items

- C++ gateway binary compilation and CTest execution require Linux/GCC/Clang toolchain (host is Windows development environment).
- Canonical Linux gate commands:
  ```bash
  cd services/voice-gateway-cpp && ./tests/run_gate.sh
  ```
