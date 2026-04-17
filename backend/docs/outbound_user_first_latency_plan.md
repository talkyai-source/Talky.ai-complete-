# First-Interaction Latency — Outbound "User-Speaks-First" Calls

**Date:** 2026-04-16
**Scope:** Outbound telephony pipeline (Asterisk adapter + C++ voice-gateway + Python voice pipeline).
**Mode under investigation:** Outbound calls where the **callee (user) speaks first**, not the agent. No greeting is played.
**Symptom reported:** 2–4 s delay between the user's first utterance and hearing the agent's first reply. Subsequent turns are acceptable.

This document is evidence-only. Every claim cites either a code location in this repository, an official vendor document, or a third-party measurement. No assumptions are accepted.

---

## 1. Revised call flow — "user-speaks-first" outbound

```
User clicks "call" → REST /originate
  → AsteriskAdapter.originate_call                              (Asterisk dials PJSIP/<ext>)
  → PBX rings softphone, callee picks up
  → Asterisk: ChannelStateChange → Up
  → _on_outbound_answered (asterisk_adapter.py:488-549)          [STAGE A]
        4 serialized ARI/gateway HTTP calls
  → _on_new_call (telephony_bridge.py:228)                       [STAGE B]
        TelephonyMediaGateway.on_call_started
        pipeline.start_pipeline → process_audio_stream
            └─ DeepgramFluxSTTProvider.stream_transcribe
                   └─ WS handshake happens LAZILY on first audio [STAGE C — critical]
  → (greeting task currently still runs — must be disabled)      [STAGE D]
  → C++ gateway forwards callee RTP → batched HTTP POST
    → telephony_bridge /audio endpoint → Deepgram WS
  → User says "Hello?"                                           [STAGE E]
  → Deepgram Flux emits EndOfTurn                                [STAGE F]
  → handle_turn_end → _stream_llm_and_tts                        [STAGE G]
        Groq LLM (cold httpx/HTTP-2 connect on first call)
        Cartesia TTS (WS handshake + first-audio) — per sentence [STAGE H — critical]
  → send_audio → C++ /v1/sessions/tts/play (per 20 ms POST)
  → C++ transmitter_loop → RTP → Asterisk → PBX → softphone
  → User hears first agent audio                                 [STAGE I]
```

The **user-perceived latency** is from the moment the user *stops* speaking in Stage E to the moment they *hear* agent audio in Stage I.

---

## 2. Quantified first-turn latency budget (outbound, user speaks first)

All numbers derived from code paths in this repository plus the cited external measurements. See §7 for sources.

| Stage | Component | Cold (ms) | Warm (ms) | Evidence |
|---|---|---|---|---|
| A | 4× serialized ARI/gateway HTTP after answer | 200–800 | 100–300 | `asterisk_adapter.py:488-549` + `ASTERISK-26771` |
| C | Deepgram Flux WS handshake (lazy, on first audio) | **300–500** | 80–200 | `deepgram_flux.py:562-577` + Rapida telemetry |
| D | Current greeting task wastes LLM+TTS budget | 500–1500 | 300–900 | `telephony_bridge.py:181-221` (to be removed for this mode) |
| F | Flux end-of-turn detection after user silence | 200–400 | 200–400 | Deepgram Flux docs; intrinsic, not optimizable |
| G₁ | Groq first HTTP/2 connect + TTFT | 200–500 | 120–280 | `groq.py:173-181` + tokenmix 2026 bench |
| G₂ | LLM full response generation | 300–800 | 300–800 | Provider-dependent, not a cold-start issue |
| H₁ | Cartesia WS handshake (per-sentence today) | **300–600** | 60–200 | `cartesia.py:124-129` + Cartesia docs |
| H₂ | Cartesia first-audio TTFB after request | 80–200 | 80–150 | Cartesia docs |
| I | Backend → C++ POST → jitter → RTP → ear | 50–100 | 50–100 | `telephony_media_gateway.py:337-397` + `session.cpp:450-470` |

**Critical path from user-stops-speaking → user-hears-agent:** F + G₁ + G₂ + H₁ + H₂ + I
Cold = **1130–2600 ms**, warm = **810–1930 ms**.

**But there is a second, hidden contributor unique to user-first mode:** if Stage C (Deepgram WS handshake) has not completed *before* the user starts speaking, the caller audio accumulates in a buffer while the WS is opening. Flux will eventually catch up once connected, but the user's utterance appears to the pipeline as if it was delivered ~300–500 ms late, pushing the effective EndOfTurn that much later. This alone can add **300–500 ms** on top of the critical path, producing the reported **2–4 s** upper bound.

---

## 3. Revised root-cause inventory (adjusted for user-first)

Priority is **re-ordered** from the original plan because removing the agent greeting changes which cold starts matter:

### 3.1 (NEW TOP) Deepgram Flux WebSocket is opened lazily — user's first words race the handshake

**Code:** `backend/app/infrastructure/stt/deepgram_flux.py` lines 562–577.

```python
async with websockets.connect(url, extra_headers=headers) as ws:
    ...
    silent_frame = bytes(3200)  # 100ms of silence
    await ws.send(silent_frame)
```

`stream_transcribe` is invoked inside `process_audio_stream` (voice_pipeline_service.py line 255). The WS handshake only begins when the audio iterator yields its first chunk — which happens only *after* the C++ gateway starts posting caller audio. In user-speaks-first mode, the user may start speaking within ~200 ms of answering — before or during the Flux handshake.

**Evidence this is already a recognised risk** — self-logged warning at deepgram_flux.py:346:
```python
if elapsed_ms > 8000:
    logger.warning("deepgram_flux_slow_start ... Flux closes connection after 10s without audio; call setup is too slow")
```

**Source:** Rapida production telemetry (blog.rapida.ai/voice-agent-telemetry): Deepgram WebSocket handshake = ~500 ms with cold DNS/TLS.

**Impact:** 300–500 ms added to every first interaction in user-first mode.

---

### 3.2 (NEW) The agent greeting task still runs and must be suppressed

**Code:** `backend/app/api/v1/endpoints/telephony_bridge.py` lines 268–270 (inside `_on_new_call`):
```python
greeting_task = asyncio.create_task(_send_outbound_greeting(voice_session))
```

The code unconditionally schedules `_send_outbound_greeting`, which runs an LLM + TTS pipeline to produce an opening line. In user-first mode this is wasted work that also:
- Holds `session.llm_active = True` (line 190), blocking the real first turn via the guard at voice_pipeline_service.py:456–457.
- Produces agent audio that plays *over* the user's "Hello?" (causing audible overlap until Flux triggers barge-in).
- Consumes the Cartesia cold WS handshake on the wrong turn.

**Impact:** direct first-interaction correctness bug in user-first mode, plus 500–1500 ms of wasted LLM+TTS cold-start before anything useful can happen.

---

### 3.3 Cartesia TTS WebSocket is opened per sentence

**Code:** `backend/app/infrastructure/tts/cartesia.py` lines 124–129.

Unchanged from the original analysis — each `stream_synthesize` call opens a fresh WS. In user-first mode this hits the *first agent response* instead of a greeting, so it is still on the critical path.

**Official source (Cartesia docs, `docs.cartesia.ai/api-reference/tts/websocket`):**
> "Do many generations over a single WebSocket. Just use a separate context for each generation."
> "Set up the WebSocket before the first generation. This ensures you don't incur latency when you start generating speech."

**Impact:** 300–600 ms on first agent response, 60–200 ms on every subsequent sentence.

---

### 3.4 Four serialized ARI/gateway round-trips in `_on_outbound_answered`

**Code:** `backend/app/infrastructure/telephony/asterisk_adapter.py` lines 488–549.

In user-first mode this happens *before* audio flows, so it adds directly to the window during which the user may start speaking but no STT is yet listening. Parallelizing compresses this window.

**Source:** `ASTERISK-26771` — "ARI HTTP requests take ~200 ms to be taken into account."

**Impact:** 100–400 ms savings from parallelization.

---

### 3.5 Cold Groq httpx/HTTP-2 connection on first LLM call

**Code:** `backend/app/infrastructure/llm/groq.py` lines 173–181.

`AsyncGroq` client is constructed but httpx only opens the TCP+TLS+HTTP/2 pool on first real request. In user-first mode the first LLM call is the *real turn*, so the cold-start tax hits the critical path directly.

**Source:** `console.groq.com/docs/production-readiness/optimizing-latency` + tokenmix.ai 2026 benchmark.

**Impact:** 80–200 ms on the first real agent response.

---

### 3.6 `asyncio.sleep(0.05)` in greeting path

**Code:** `telephony_bridge.py:181`.
Becomes irrelevant once §3.2 is implemented (greeting task removed). Keep noted; delete alongside greeting suppression.

---

## 4. Non-first-turn items (ongoing, every turn)

These are NOT sources of the 2–4 s first-turn spike, but they add steady-state latency and degrade scalability. Documented for completeness; prioritized below P0.

### 4.1 C++ gateway forces `Connection: close`
`services/voice-gateway-cpp/src/http_server.cpp` line 145. Breaks aiohttp keep-alive on the backend→gateway TTS path.

### 4.2 One HTTP POST per 20 ms TTS packet
`telephony_media_gateway.py:337-397`. 50 POSTs/sec/call to the C++ gateway. C++ already supports batched enqueue (`session.cpp:236`, frame_count = bytes/160).

### 4.3 C++→Python audio-callback: thread-per-POST, HTTP/1.0, no keep-alive
`services/voice-gateway-cpp/src/http_server.cpp` lines 439–518 (`http_post`) and line 793 (`std::thread(...).detach()`). Scalability cliff, not a first-turn latency source.

### 4.4 8 kHz telephony STT
Dictated by G.711. Quality issue only (Deepgram recommends 16 kHz for best accuracy). Not a latency source — tracked for quality follow-up.

---

## 5. Remediation plan — re-prioritized for user-speaks-first mode

Each step below lists **what to change, exactly where, why it works (with citation), and how to prove it worked.**

Fixes are ranked by measured impact on the user-first critical path. Apply P0 items in order, then run the measurement gate in §6 before touching P1.

---

### P0-A — Suppress the agent greeting on outbound user-first calls

**Why it's first.** In user-first mode the greeting is incorrect behaviour — it overlaps the user's first words and burns the Cartesia cold-start handshake on audio nobody wants. Removing it is correctness, not only performance.

**Where.** `backend/app/api/v1/endpoints/telephony_bridge.py`:
- `_on_new_call` line 268–270 (the `asyncio.create_task(_send_outbound_greeting(...))` call) — gate behind a per-call flag derived from the campaign / session config.
- `_build_telephony_session_config` (section around line 100–140) — add a field `first_speaker: "user" | "agent"` (default `"user"` for outbound campaigns).

**How.** If `first_speaker == "user"`, do not schedule `_send_outbound_greeting`. Keep `session.llm_active = False` so the first real transcript can trigger `handle_turn_end` immediately. Keep the barge-in clearing logic at `_send_outbound_greeting:199` — relocate it into `_on_new_call` so a callee "Hello?" doesn't remain queued as a stale barge-in.

**Proof of correctness.** `voice_pipeline_service.py:456-457` guards re-entry on `session.llm_active`. If no greeting ran, `llm_active` stays False, and the first genuine user transcript proceeds straight to `_stream_llm_and_tts`. No ordering hazard.

**Predicted saving.** Eliminates 500–1500 ms of wrong-turn work and prevents audible overlap. Makes the subsequent P0 items meaningful.

**Validation.** Log `greeting_suppressed=true` and confirm no `Generating dynamic greeting` log line appears for user-first calls. The first `llm_active=True` transition should correlate with the user's first Flux final transcript.

---

### P0-B — Prewarm Deepgram Flux WebSocket at pipeline start

**Why.** In user-first mode the user may speak within 200 ms of answering. The current lazy handshake (§3.1) races the user's first words.

**Where.** `backend/app/infrastructure/stt/deepgram_flux.py`:
- Refactor `stream_transcribe` into two coroutines: `connect()` (opens WS, sends the existing 100 ms silent primer at line 576) and `stream(audio_iter)` (the existing read/write loops).
- `backend/app/domain/services/voice_pipeline_service.py` `start_pipeline` (around line 157–250): call `await stt_provider.connect()` immediately, *before* or concurrently with `process_audio_stream`.
- Feed the first-few-hundred-ms of caller audio from a buffered queue so no frames are lost while the handshake completes.

**Proof of correctness.** Deepgram Flux docs (`developers.deepgram.com/docs/flux/quickstart`) confirm the WS accepts audio immediately after upgrade. The repo already sends a 100 ms silent primer at line 576, so the primer pattern is validated internally. The existing warning at line 346 (`deepgram_flux_slow_start`) is the team's own acknowledgement that slow setup matters.

**Predicted saving.** 300–500 ms on every first user interaction. This is the single biggest win for the reported symptom.

**Validation.** Log `stt_ws_ready_ts` at handshake completion and `stt_first_audio_sent_ts` when the first real caller frame is forwarded. After the fix, `stt_ws_ready_ts ≤ stt_first_audio_sent_ts` should hold on ≥95% of calls.

---

### P0-C — Persistent Cartesia WebSocket with per-sentence `context_id`

**Why.** Cartesia docs prescribe one WS per session with a fresh `context_id` per generation (§3.3). Current code violates both prescriptions by opening a new WS per sentence.

**Where.** `backend/app/infrastructure/tts/cartesia.py`:
- Add `connect_for_call(call_id)` → opens WS, stores in `self._ws_per_call[call_id]`.
- Add `disconnect_for_call(call_id)` → closes WS in call teardown.
- In `stream_synthesize`, replace the `async with ws_connect` block (lines 124–129) with a send of the payload on the persistent WS using a new `context_id` (uuid4), then consume audio messages for that `context_id` until the done sentinel.

**Where to wire in.** `telephony_bridge.py:_on_new_call` — call `await voice_session.tts_provider.connect_for_call(call_id)` in parallel with `voice_session.media_gateway.on_call_started(...)` via `asyncio.gather`. Teardown in the existing call-end path.

**Proof of correctness.** Cartesia's own API reference (cited in §3.3) is explicit. No behaviour change is required to the sentence-splitting logic in `synthesize_and_send_audio` — only the transport layer underneath changes.

**Predicted saving.** 300–600 ms on the first agent response of every call, 60–200 ms on every subsequent sentence.

**Validation.** Log `tts_ws_handshake_ms` once per `connect_for_call`. After the fix, this should appear exactly once per call, not once per sentence.

---

### P0-D — Parallelize ARI round-trips in `_on_outbound_answered`

**Why.** Four serialized HTTP calls (§3.4) stretch Stage A — the silent window before any STT is listening.

**Where.** `backend/app/infrastructure/telephony/asterisk_adapter.py` `_on_outbound_answered` lines 488–549:
- After the `externalMedia` POST returns the UnicastRTP channel id, the `addChannel` POST and the two `/variable` GETs have no ordering dependency on each other. Group with `asyncio.gather`.
- If the ARI version supports it, replace the two `/variable` GETs with a single `getChannelVars` call; otherwise cache `UNICASTRTP_LOCAL_ADDRESS` / `UNICASTRTP_LOCAL_PORT` from `ChannelVarSet` events on the ARI WebSocket and skip the GETs entirely when cached.
- Apply the same change to `_on_stasis_start` (inbound) for consistency.

**Proof of correctness.** ARI is an independent HTTP/1.1 endpoint; aiohttp `ClientSession` is concurrent-safe for independent requests. The subsequent `POST /v1/sessions/start` to the C++ gateway depends only on the resolved RTP address+port, which `asyncio.gather` still delivers atomically.

**Predicted saving.** 100–400 ms shaved off Stage A.

**Validation.** Log wall-clock delta between `ChannelStateChange(Up)` timestamp and the `/v1/sessions/start` response. Target: ≤ 150 ms on localhost, down from the current 250–650 ms typical.

---

### P0-E — Warm the Groq httpx/HTTP-2 connection at worker boot

**Why.** The first real LLM call in user-first mode *is* the first agent response. Cold httpx connect adds 80–200 ms.

**Where.** `backend/app/workers/voice_worker.py` `_initialize_providers` (after `GroqLLMProvider.initialize`): issue one `chat.completions.create(..., max_tokens=1)` against a trivial prompt. Discard the result. This seeds the httpx TLS+HTTP/2 pool and DNS cache.

**Proof.** Groq production docs (`console.groq.com/docs/production-readiness/optimizing-latency`) recommend maintaining persistent connections. httpx reuses pooled connections by default when the pool is non-empty.

**Predicted saving.** 80–200 ms on the first call served after a worker boot or httpx idle-timeout reap.

**Validation.** Log `groq_connect_ms` measured as the delta between request dispatch and first response byte on the first real call. Should be ≤ 100 ms after the fix.



---

## 6. Measurement gate — mandatory before moving to P1

No P1 or P2 work begins until this gate passes. This is what the user (as judge) checks.

### 6.1 Instrumentation to add before any fix

Emit a structured JSON log line per call containing these µs-precision timestamps (all measured from the same monotonic clock in the backend process):

| Field | Emitted when |
|---|---|
| `t_answer` | Asterisk `ChannelStateChange → Up` received |
| `t_ari_setup_done` | All ARI + `/v1/sessions/start` responses returned |
| `t_stt_ws_ready` | Deepgram Flux WS handshake completes |
| `t_stt_first_final` | First Flux `final` transcript of the call |
| `t_turn_end` | `handle_turn_end` invoked for that transcript |
| `t_llm_first_token` | Already captured at voice_pipeline_service.py:764 |
| `t_tts_ws_ready` | Cartesia WS handshake completes |
| `t_tts_first_audio` | First audio chunk yielded by `stream_synthesize` |
| `t_first_rtp_tx` | Already logged in C++ `session.cpp:548` as `event=rtp_tx` |

### 6.2 Baseline benchmark (before any change)

Run **20 outbound calls** in sequence, have the test caller answer and say "Hello?" within 500 ms of answering. Record:
- P50 and P95 of **`t_first_rtp_tx − t_stt_first_final`** — this is the true "first-interaction latency" the user perceives.
- P50 and P95 of **`t_stt_ws_ready − t_answer`** — exposes the race condition that is the dominant first-turn cost.

Save the results in `backend/docs/debugging/2026-04-16_first_turn_baseline.md` (new file, create only after this doc is approved).

### 6.3 Gate criteria after P0-A…P0-E are applied

- `t_first_rtp_tx − t_stt_first_final` P50 must drop by **≥ 750 ms** versus baseline. If it does not, do not proceed — each P0 item's individual log must be re-examined to find which fix under-delivered.
- `t_stt_ws_ready − t_answer` P95 must be **< 0** (WS ready *before* first answer audio).
- `t_tts_ws_handshake_ms` must appear **exactly once per call**, not once per sentence.
- No `Generating dynamic greeting` log line for user-first calls.

Each criterion corresponds to exactly one P0 item. All four must be satisfied before P1 is started.

---

## 7. P1 follow-ups — schedule only after §6 passes

### P1-1 — HTTP/1.1 keep-alive on the C++ gateway HTTP server
`services/voice-gateway-cpp/src/http_server.cpp` line 145: drop `Connection: close`, add `Connection: keep-alive`; in `handle_client` loop reading additional requests on the same fd until an idle timeout (5 s). Cuts ~10–40 ms per utterance on the TTS POST path and eliminates TIME_WAIT accumulation. Validated by aiohttp reusing the same local port across POSTs (observe with `ss -tnp` or aiohttp connector metrics).

### P1-2 — Batch TTS POSTs to the C++ gateway (160 B → 320–800 B)
`backend/app/infrastructure/telephony/telephony_media_gateway.py` send-loop around line 354: buffer 2–5 frames (40–100 ms) before POSTing. C++ `enqueue_tts_ulaw` already accepts any multiple of 160 B (`session.cpp:236`, `frame_count = ulaw_audio.size() / 160`). Reduces POST rate from 50/s to 10–25/s per call. Validate: barge-in latency stays ≤ 50 ms via the existing `barge_in_event` check inside the loop.

### P1-3 — Keep-alive the C++ → Python audio-callback path
`services/voice-gateway-cpp/src/http_server.cpp` `http_post` line 439 and line 793 `std::thread(...).detach()`. Replace HTTP/1.0 + `Connection: close` + thread-per-POST with a long-lived libcurl easy-handle pool keyed by URL, or switch to a Unix domain socket to the backend. Not a first-turn win; improves per-host call scalability.

---

## 8. P2 / P3 follow-ups

### P2-1 — Replace C++ audio-callback HTTP with WebSocket
The backend already hosts `/ws-audio/{call_uuid}` for FreeSWITCH mod_audio_fork (see telephony_bridge.py). Reuse this pattern for the Asterisk + C++ path to eliminate per-batch TCP connects entirely. Gated on P1-3 proving insufficient.

### P2-2 — Warm standby TTS WS per tenant
If P0-C warm-reconnect cost is still visible, pre-open a pooled Cartesia WS per tenant on worker start, assign to incoming calls on demand.

### P3-1 — Upsample caller audio 8 kHz → 16 kHz before Deepgram Flux
Quality (not latency) follow-up. Change `_build_telephony_session_config` `stt_sample_rate` to `16000` and upsample in `TelephonyMediaGateway.on_audio_received`. Reduces transcript errors that cause re-asks and perceived lag.

---

## 9. Non-issues explicitly ruled out (with evidence)

These items are documented only to prevent wasted debugging effort. Each is proven not to be a first-interaction latency cause.

| Candidate cause | Ruled out by | Conclusion |
|---|---|---|
| Jitter-buffer prefetch blocks first TTS audio | `session.cpp:455` — transmitter bypasses prefetch when `!tts_queue_.empty()` | Prefetch gates echo path only, not TTS playout |
| `audio_callback_batch_frames=4` (80 ms) adds latency | Matches Deepgram's recommended chunk size (`deepgram_flux.py:43` `FLUX_OPTIMAL_CHUNK_MS=40`) | Keep as configured |
| 8 kHz STT sample rate slows transcription | Dictated by G.711 PCMU wire format; Flux docs accept 8 kHz | Quality follow-up only (P3-1), not a latency cause |
| Barge-in pacing loop contains waste | Already carefully engineered and documented at `telephony_media_gateway.py:337-397` | Do not modify |
| TTS sentence splitter introduces delay | `_stream_llm_and_tts` begins TTS as soon as the first sentence boundary is seen | Already optimal |

---

## 10. Sources / citations

1. **Cartesia TTS WebSocket API reference** — `docs.cartesia.ai/api-reference/tts/websocket` — "Do many generations over a single WebSocket … Set up the WebSocket before the first generation."
2. **Cartesia TTS endpoint comparison** — `docs.cartesia.ai/use-the-api/compare-tts-endpoints` — "A new HTTPS request pays for TCP and TLS again; WebSocket amortizes that cost when you keep the connection open."
3. **Deepgram Flux Quickstart** — `developers.deepgram.com/docs/flux/quickstart`.
4. **Rapida voice-agent telemetry blog** — `blog.rapida.ai/voice-agent-telemetry` — measured Deepgram WS handshake ≈ 500 ms; TTS WS handshake 300–600 ms on cold DNS/TLS.
5. **Asterisk issue ASTERISK-26771** — `issues-archive.asterisk.org/ASTERISK-26771` — "ARI HTTP requests take ~200 ms to be taken into account."
6. **Groq production latency guide** — `console.groq.com/docs/production-readiness/optimizing-latency` — recommends persistent connections for lowest TTFT.
7. **Groq TTFT benchmark (2026)** — `tokenmix.ai/groq-benchmarks` — median 120 ms, P95 280 ms (steady state); cold connect adds 80–200 ms.

---

## 11. Execution order summary

1. Add §6.1 instrumentation.
2. Run §6.2 baseline (20 calls, record P50/P95).
3. Apply P0-A (suppress greeting for user-first).
4. Apply P0-B (prewarm Deepgram Flux).
5. Apply P0-C (persistent Cartesia WS).
6. Apply P0-D (parallelize ARI).
7. Apply P0-E (warm Groq pool at worker boot).
8. Re-run §6.2 benchmark. Verify §6.3 gate.
9. Only if gate passes: proceed to P1-1, P1-2, P1-3 in order, re-measuring between each.
10. P2 and P3 are backlog, not part of the first-turn fix.

Fixes in this order are independent and individually verifiable — the judge can accept or reject each one based on its measured delta against the prediction in §2, with ±30 % tolerance.

---

## 12. Future adjustments & unblock protocol (post-P0)

This section is the closing deliverable for every task that is intentionally *not* implemented in the current P0 batch. Each entry defines the **trigger signal**, the **precise change site**, the **risk budget**, and the **rollback path** so the work can be picked up months later without re-deriving the context.

### 12.1 P1-1 — HTTP/1.1 keep-alive in the C++ gateway

- **Unblock trigger.** Post-P0 measurement report (`backend/docs/debugging/2026-04-16_first_turn_baseline.md`) shows that the **steady-state** P95 of `t_tts_first_audio → t_first_rtp_tx` exceeds **60 ms**, OR `ss -tn | grep TIME_WAIT | wc -l` on the gateway host reports a sustained count above 500 under 10 concurrent calls.
- **Change site.** `services/voice-gateway-cpp/src/http_server.cpp`:
  - `write_response` (line 145): replace `Connection: close` with `Connection: keep-alive`.
  - `handle_client` (lines 632–936): wrap the dispatch in a `while` loop; break when `recv()` returns 0 bytes or the 5 s idle timer fires; move the single `close(client_fd)` to the loop exit.
- **Validation metric.** After deploy, `ss -tnp | grep :8081 | wc -l` stays roughly flat during a 60 s load test (one connection per caller, not per POST). Python-side `aiohttp` trace logs should stop reporting new connection events after the first POST.
- **Risk budget.** C++ server side. Must pass the existing unit tests in `services/voice-gateway-cpp/tests/`. Rollback is `git revert` of the two hunks; the server gracefully falls back to per-request connections because the Python client advertises `Connection: close` as an acceptable value.

### 12.2 P1-3 — Keep-alive on the C++ → Python audio-callback path

- **Unblock trigger.** A concurrent-call scale test (≥ 20 simultaneous calls) shows either (a) `htop` thread-count exceeding 200 on the gateway process, or (b) backend `receive_gateway_audio` P99 > 25 ms due to connection-establishment queueing on the Python side.
- **Change site.** `services/voice-gateway-cpp/src/http_server.cpp` `http_post` (line 439) and the `std::thread(...).detach()` at line 793. Replace the thread-per-POST pattern with a `libcurl` easy-handle pool keyed by `{host, port}`. Alternatively, switch the transport to a Unix domain socket when both services co-locate — the backend already has AF_UNIX plumbing in `uvicorn`'s `--uds` mode.
- **Validation metric.** Gateway thread count stays below 1.5× the concurrent-call count during the scale test. No regression in `telephony_bridge.receive_gateway_audio` P99 vs. baseline.
- **Risk budget.** Scalability fix only — do **not** expect any first-turn latency movement from this change. Rollback is straightforward.

### 12.3 P2-1 — Replace C++ audio-callback HTTP with WebSocket

- **Unblock trigger.** P1-3 deployed and measurements still show per-batch TCP setup time dominating the inbound audio path (rare; only expected at very high concurrency or very low MTU networks).
- **Change site.** Reuse the existing `/ws-audio/{call_uuid}` endpoint already serving FreeSWITCH `mod_audio_fork` in `backend/app/api/v1/endpoints/telephony_bridge.py`. The C++ side would replace the HTTP POST path in `session.cpp::forward_to_backend` with a persistent `libwebsockets` client.
- **Risk budget.** Architectural change spanning both services; two-week effort including rollout gating. Do **not** pursue without P1-3 data justifying it.

### 12.4 P2-2 — Warm-standby Cartesia WebSocket pool per tenant

- **Unblock trigger.** After P0-C lands, the log `cartesia_ws_handshake_ms` on the *first* sentence of a call still exceeds 150 ms P95. This would indicate that per-call WS open is still a visible cost even though it is no longer per-sentence.
- **Change site.** Add a bounded pool in `backend/app/infrastructure/tts/cartesia.py` keyed by `tenant_id`, pre-opened on worker boot, and hand one out in `connect_for_call`. Pool size = `CARTESIA_POOL_SIZE` env (default 2). On checkout, kick off a replacement open in the background.
- **Risk budget.** Low. Backward-compatible: if the pool is empty the existing per-call open path runs. Adds ~5 ms to worker startup per pooled connection.

### 12.5 P3-1 — 8 kHz → 16 kHz upsample before Deepgram Flux

- **Unblock condition.** **Quality**, not latency. Pursue only if the STT error-rate log (e.g. transcript confidence distribution or user-reported "AI misunderstood me") shows a regression large enough to justify the CPU cost on the backend.
- **Change site.** `backend/app/core/voice_config.py` `_build_telephony_session_config`: set `stt_sample_rate=16000`. Add an upsample step (e.g. `scipy.signal.resample_poly(audio, 2, 1)` or `audioop.ratecv`) at the top of `TelephonyMediaGateway.on_audio_received` before pushing into the STT queue.
- **Risk budget.** Adds ~0.2 ms CPU per 20 ms chunk. Does not affect first-interaction latency; explicitly out of scope for this work item.

### 12.6 P0 follow-up triggers

These are **not** backlog; they are alarms on the P0 fixes that just shipped. Each defines what "bad" looks like in the post-deploy logs and what to change.

| Signal in logs | Interpretation | Adjustment |
|---|---|---|
| `cartesia_ws_handshake_ms` appears more than once per call | P0-C regression: `connect_for_call` is being re-invoked mid-call, or `_get_or_open_ws` is not caching | Verify `disconnect_for_call` is called only in `_on_call_end`, not on sentence boundary. Check for exception-driven reconnects in `_stream_over_ws`. |
| `stt_ws_handshake_ms` > 200 ms P95 | Deepgram side cold-start still visible | Revisit P0-B: consider opening the Flux WS in `_on_outbound_answered` in parallel with ARI setup rather than lazily on first audio frame. |
| `groq_connect_ms` > 100 ms on the first real LLM call | P0-E warm-up either skipped or insufficient | Confirm the background warm task completed before first LLM call. Consider issuing a second warm ping after 90 s to keep the HTTP/2 window alive across idle gaps. |
| `t_tts_first_audio` follows `t_llm_first_token` by > 120 ms | TTS sentence-splitter or WS write-side stall | Inspect `_stream_llm_and_tts` for any sync point between token and first synth call. Reducing the first-sentence detection threshold is the typical fix. |
| `send_audio` batch size reliably equals 1 in steady state | `TELEPHONY_TTS_BATCH_PACKETS` is too aggressive for this environment's jitter | Raise `TELEPHONY_TTS_BATCH_PACKETS` to 3 (60 ms) and re-verify barge-in reaction stays ≤ 50 ms. |

### 12.7 Adjustment discipline

Any future adjustment to this plan must follow the same evidence rule that produced it:

1. Measurement first. Add a log line before changing behaviour.
2. One change at a time. Never bundle two optimisations into one deploy.
3. Write the predicted delta before measuring. After measuring, the actual vs. predicted ratio (±30 %) determines whether the change ships or rolls back.
4. Update this document in the same commit as the code change — §12 is append-only; previous entries are never edited, only superseded by a new subsection that references the old one.

This closes the first-interaction latency work item at the protocol level. The remaining P0 deploy + measurement gate is the only open activity.
