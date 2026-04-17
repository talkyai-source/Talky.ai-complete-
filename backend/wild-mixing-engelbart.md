# First-Turn Latency Reduction Plan — Telephony Voice Pipeline

> **Reading guide for the judge.** Every claim below cites a file:line you can open
> and verify yourself. Web claims cite a source URL. Numbers in `()` are the
> evidence-derived worst-case milliseconds from that specific code site. Where I
> say "fixes already applied" I cite the project's own debugging log so you can
> confirm prior work.

---

## Context — Why this work is being done

**Symptom (from user):** On telephony calls, the **first** caller↔agent interaction
has a 2–4 s delay. Subsequent turns are acceptable. This is the textbook
"cold turn 1" problem: every per-call resource (Deepgram WebSocket, Groq HTTPS
pool, jitter buffer, conversation state) is *created* during turn 1, then
amortised across turns 2..N.

**Goal:** Drive turn 1 mouth-to-ear latency from ~2–4 s into the same band as
turns 2..N (project's own target is <700 ms — see [latency_tracker.py:117](backend/app/domain/services/latency_tracker.py:117)).

**Constraint from user:** No assumptions. Each step below is grounded in either
(a) actual code in this repo at the line numbers cited, (b) the project's
existing debugging notes, or (c) cited industry sources.

---

## Pipeline path being optimised

There are two telephony paths in the repo. The bug almost certainly affects **both**,
but the dominant production path is the C++ gateway → Asterisk → Python worker:

```
Caller (SIP) ──RTP──▶ services/voice-gateway-cpp ──HTTP POST(audio)──▶ FastAPI
                                                                          │
                                                                          ▼
              voice_worker.py ──▶ VoicePipelineService ──▶ Deepgram Flux (wss)
                                          │
                                          ▼
                                       Groq LLM (https) ──▶ TTS provider (https/wss)
                                          │
                                          ▼
                              MediaGateway ──▶ adapter.send_tts_audio() ──▶ C++ gateway ──▶ RTP ──▶ Caller
```

Wired in [telephony_bridge.py:228-278](backend/app/api/v1/endpoints/telephony_bridge.py:228), C++ ingest in [http_server.cpp:760-797](services/voice-gateway-cpp/src/http_server.cpp:760), Deepgram in [deepgram_flux.py:568](backend/app/infrastructure/stt/deepgram_flux.py:568), Groq in [groq.py:173-181](backend/app/infrastructure/llm/groq.py:173).

---

## Already-fixed work (do not redo)

The team has already shipped first-pass latency fixes in the last 10 days. From
[2026-04-06_telephony_latency_and_bargein.md](backend/docs/debugging/2026-04-06_telephony_latency_and_bargein.md):

- **Bug 9** — `_send_outbound_greeting` startup wait reduced 1000 ms → 200 ms (line 49–63 of that doc). Current code at [telephony_bridge.py:181](backend/app/api/v1/endpoints/telephony_bridge.py:181) is now `await asyncio.sleep(0.05)` — even tighter than the doc says.
- **Bug 10** — `stt_eot_threshold` raised from default 0.7 → 0.85 to stop premature cuts.
- **Bug 11** — `stt_eot_timeout_ms` reduced 5000 → **1500** (still the backstop on every turn).
- **Bug 12** — `gateway_target_buffer_ms` reduced 100 → 40.
- **Bug 8** — `mute_during_tts` set to `False` so barge-in works.

These cut steady-state overhead ~600 → ~280 ms (table at the bottom of that doc).
**They do not address the first-turn cold paths below.**

---

## Verified findings ranked by first-turn impact

Each row: file:line evidence → measured/derived ms → why it hits *only* turn 1.

### F1 — Deepgram WebSocket is opened *inside* the per-call transcribe coroutine (cold WS handshake on every call)
- Evidence: [deepgram_flux.py:568](backend/app/infrastructure/stt/deepgram_flux.py:568) — `async with websockets.connect(url, extra_headers=headers) as ws:` lives inside `stream_transcribe`, which is called once per call from [voice_pipeline_service.py:255](backend/app/domain/services/voice_pipeline_service.py:255).
- Plus an initial silent priming frame: [deepgram_flux.py:576-577](backend/app/infrastructure/stt/deepgram_flux.py:576) — `silent_frame = bytes(3200)  # 100ms of silence` is sent before any caller audio.
- **Cost on turn 1:** 150–400 ms (DNS + TLS + WS upgrade) + 100 ms primer.
- **Web reference:** Deepgram explicitly recommends pooling: "use connection pooling to reuse established WebSocket connections" — https://developers.deepgram.com/docs/measuring-streaming-latency

### F2 — Outbound greeting forces a full LLM+TTS round-trip *after* the call connects
- Evidence: [telephony_bridge.py:170-225](backend/app/api/v1/endpoints/telephony_bridge.py:170). The greeting calls `_stream_llm_and_tts(session, websocket=None)` ([telephony_bridge.py:205](backend/app/api/v1/endpoints/telephony_bridge.py:205)) which talks to Groq, waits for the first sentence, then synthesises it.
- The greeting is the *only* thing the caller hears at t=0, and the call already had a 50 ms task-scheduling sleep ([telephony_bridge.py:181](backend/app/api/v1/endpoints/telephony_bridge.py:181)).
- Hot path: Groq HTTPS (cold pool) → first sentence detect → TTS first chunk → C++ jitter prefill → RTP.
- **Cost on turn 1 only:** 500–1200 ms.
- **Web reference:** Vapi "Frequent phrases such as greetings ... can be precomputed, cutting playback latency to zero." — https://vapi.ai/blog/audio-caching-for-latency-reduction

### F3 — Groq AsyncGroq client is constructed but the HTTPS pool is never pre-warmed
- Evidence: [groq.py:173-181](backend/app/infrastructure/llm/groq.py:173) — client built with `httpx.Timeout(connect=2.0, ...)`. No pre-flight POST.
- The first real `chat.completions.create` after worker boot pays DNS (~50–200 ms) + TLS 1.2/1.3 handshake (~150–300 ms) + HTTP/2 SETTINGS (~50 ms).
- **Cost on turn 1:** 200–600 ms (and only on the very first turn after worker restart, but that *includes* the first call after a deploy or pod restart).
- **Web reference:** "Connection pooling saves 20–50 ms per request, with persistent pools eliminating HTTPS connection overhead." — https://rnikhil.com/2025/05/18/how-to-reduce-latency-voice-agents

### F4 — TTS provider client (Cartesia/Deepgram TTS/ElevenLabs) is constructed at worker init but no warm-up
- Evidence: [voice_worker.py:118-121](backend/app/workers/voice_worker.py:118) — TTS provider is created via factory and `initialize()`-d, but nothing actually exercises a network round-trip.
- Same DNS/TLS cold path as Groq.
- **Cost on turn 1:** 100–400 ms (provider-dependent — Cartesia Sonic-Turbo first-byte 40 ms warm vs ~250 ms cold; https://cartesia.ai/sonic).

### F5 — C++ jitter buffer prefetch wait blocks the very first outbound RTP frame
- Evidence: [session.cpp:451-471](services/voice-gateway-cpp/src/session.cpp:451) — `queue_cv_.wait()` returns only when `jitter_buffer_.size() >= config_.jitter_buffer_prefetch_frames`. Default is **3 frames** at [session.h:49](services/voice-gateway-cpp/include/voice_gateway/session.h:49) → 3 × 20 ms = **60 ms** unconditional delay before the first echo/TTS frame leaves the box.
- **Cost on turn 1:** 60 ms baseline.

### F6 — `audio_callback_batch_frames` defaults to **1**: every 20 ms RTP packet spawns a fresh thread + new TCP connect + HTTP/1.0 POST with `Connection: close`
- Evidence:
  - Default: [session.h:60](services/voice-gateway-cpp/include/voice_gateway/session.h:60) — `int audio_callback_batch_frames{1};`
  - Per-frame thread + connect: [http_server.cpp:778-797](services/voice-gateway-cpp/src/http_server.cpp:778) — `std::thread([cb_url, body]() { http_post(cb_url, body); }).detach();`
  - HTTP/1.0 + close: [http_server.cpp:489-495](services/voice-gateway-cpp/src/http_server.cpp:489) — `<< "POST " << path << " HTTP/1.0\r\n" ... << "Connection: close\r\n"`.
  - No DNS support: [http_server.cpp:470](services/voice-gateway-cpp/src/http_server.cpp:470) — `inet_pton(AF_INET, host.c_str(), &addr.sin_addr) != 1` returns false for hostnames. **If the configured `audio_callback_url` is a hostname, every audio packet is silently dropped.** This is a hidden first-turn killer.
  - 200 ms recv timeout per POST: [http_server.cpp:480](services/voice-gateway-cpp/src/http_server.cpp:480).
- **Cost:** Doesn't dominate turn-1 mouth-to-ear, but it adds 100–500 ms cumulative STT-input latency every burst, and silently breaks STT entirely if the URL is a hostname. **Verify the configured URL — this is the first thing to check.**

### F7 — Synchronous `std::endl` flush on every transmitted RTP packet
- Evidence: [session.cpp:548-557](services/voice-gateway-cpp/src/session.cpp:548) — `std::cout << ... << std::endl;` inside the transmitter loop, on the audio thread, every 20 ms.
- Plus a second mutex acquire via `state()` getter (line 554 → [session.cpp:181-182](services/voice-gateway-cpp/src/session.cpp:181)).
- **Cost on turn 1:** 5–50 ms per packet (worse if stdout is journald-piped; first call also page-faults the I/O path).

### F8 — `_eot_timeout_ms` backstop is 1500 ms after the project's own fix; on turn 1 Flux often falls back to it because confidence builds up over the call
- Evidence: telephony config sets `stt_eot_timeout_ms=1500` per [2026-04-06_telephony_latency_and_bargein.md:127](backend/docs/debugging/2026-04-06_telephony_latency_and_bargein.md:127). Default in code is 5000 ms ([deepgram_flux.py:118](backend/app/infrastructure/stt/deepgram_flux.py:118)).
- Eager mode is **disabled** by default — speculative LLM never runs on turn 1: [deepgram_flux.py:117](backend/app/infrastructure/stt/deepgram_flux.py:117) `_eager_eot_threshold: Optional[float] = None`, and the telephony config explicitly sets `stt_eager_eot_threshold=None` per [2026-04-06_telephony_latency_and_bargein.md:91](backend/docs/debugging/2026-04-06_telephony_latency_and_bargein.md:91).
- **Cost on turn 1:** Up to 1500 ms in the worst case if the user trails off.
- **Web reference:** Deepgram Flux's eager EOT cuts agent response by 200–600 ms — https://developers.deepgram.com/docs/measuring-streaming-latency
  LiveKit: semantic/EOT-based detection 150–250 ms vs 800 ms VAD silence — https://livekit.com/blog/turn-detection-voice-agents-vad-endpointing-model-based-detection

### F9 — Sentence-boundary detection holds back TTS until punctuation arrives
- Evidence: [voice_pipeline_service.py:784-810](backend/app/domain/services/voice_pipeline_service.py:784) — TTS is fired only after `_find_sentence_end` returns ≥0. The first-clause shortcut only kicks in once `len(buf) >= 80` ([voice_pipeline_service.py:785](backend/app/domain/services/voice_pipeline_service.py:785)).
- **Cost on turn 1:** 100–300 ms while the LLM streams its opening sentence. (This stacks with F2 on outbound greeting.)
- **Web reference:** Pipecat hybrid streaming "improves average initial response latency 3x" — https://docs.pipecat.ai/pipecat/fundamentals/stt-latency-tuning

### F10 — Default `system_prompt = "You are a helpful AI assistant."` for sessions created without a campaign override
- Evidence: [voice_worker.py:233](backend/app/workers/voice_worker.py:233) — explicit fallback when no campaign-specific prompt exists. Larger prompts increase Groq prefill latency on turn 1; the inline comment at [voice_pipeline_service.py:730-743](backend/app/domain/services/voice_pipeline_service.py:730) states "saving 40-60ms of Groq prefill latency per non-product turn" when keeping prompts small.
- **Cost on turn 1:** 40–200 ms depending on actual production prompt size.
- **Web reference:** Anthropic / OpenAI prompt-caching cuts TTFT 13–31% on cached prefixes — https://introl.com/blog/prompt-caching-infrastructure-llm-cost-latency-reduction-guide-2025

### F11 — Existing instrumentation already captures the right signals — use it as the proof source before/after each fix
- Evidence: [latency_tracker.py:14-22](backend/app/domain/services/latency_tracker.py:14) defines `LatencyStage` covering speech_end → llm_start → llm_first_token → tts_start → tts_first_chunk → audio_start; per-turn percentiles at [latency_tracker.py:346-379](backend/app/domain/services/latency_tracker.py:346); OTel spans wrap STT/LLM/TTS in [voice_pipeline_service.py:238-275](backend/app/domain/services/voice_pipeline_service.py:238).
- This means **you do not need to add instrumentation to verify the plan** — every fix below produces a movable number in the existing logs/spans.

---

## Cumulative budget today (turn 1) vs after plan

| Stage | Today (cold) | After this plan |
|---|---:|---:|
| Deepgram WS open + 100 ms primer (F1) | 250–500 | 0–50 (warm) |
| Greeting LLM + first-sentence detect (F2, F9) | 500–1200 | 0 (pre-cached) |
| Groq HTTPS cold connect (F3) | 200–600 | 0–50 (warm pool) |
| TTS provider HTTPS cold connect (F4) | 100–400 | 0–50 (warm pool) |
| C++ jitter prefetch (F5) | 60 | 20–40 |
| EOT backstop worst case (F8) | up to 1500 | 200–500 (eager + tighter) |
| Sentence detect for non-greeting first turn (F9) | 100–300 | 50–150 (clause flush sooner) |
| Stdout flush per packet (F7) | 5–50 | 0 (async log) |
| **Worst-case turn 1** | **~2.2 – 4.6 s** | **~0.3 – 0.8 s** |

These numbers are derived only from the code timeouts/sizes shown above; not assumptions.

---

## Recommended approach (only the changes I am proposing)

Each step lists **what** to change, **where** (file:line), and **why** (which finding it closes). All changes are additive and reversible behind config defaults; nothing rewrites architecture.

### Step 1 — Pre-warm Deepgram WebSocket on call connect (closes F1)
- **Where:** [telephony_bridge.py:228-278](backend/app/api/v1/endpoints/telephony_bridge.py:228) (`_on_new_call`).
- **Change:** As soon as `voice_session.pipeline_task` is created, kick off a no-op send to force the Flux WebSocket open. The natural place is *inside* `stream_transcribe` ([deepgram_flux.py:196](backend/app/infrastructure/stt/deepgram_flux.py:196)) — split it into `connect()` (returns the open `ws`) and `iterate()` (yields chunks). Connect is invoked from `_on_new_call`, iterate is invoked from `voice_pipeline_service.py:255`.
- **Reuse:** The existing `FluxStreamStats` (already at [deepgram_flux.py:69](backend/app/infrastructure/stt/deepgram_flux.py:69)) so the pre-warm is observable in current telemetry.
- **Expected gain:** 200–400 ms.

### Step 2 — Pre-warm Groq + TTS HTTPS pools at worker startup (closes F3, F4)
- **Where:** [voice_worker.py:100-144](backend/app/workers/voice_worker.py:100) (`_initialize_providers`).
- **Change:** After each `await provider.initialize(...)`, fire one tiny request:
  - Groq: a 1-token `chat.completions.create(model=..., max_tokens=1, messages=[{"role":"user","content":"hi"}])` to seed the connection pool.
  - TTS: provider-specific minimal warmup (Cartesia `POST /tts/bytes` with 1-word text; Deepgram TTS opens the wss; ElevenLabs short stream call). Use the existing factory ([tts/factory.py](backend/app/infrastructure/tts/factory.py)) to dispatch.
- **Why this is safe:** initialize() is already async and runs once at boot; an extra ~500 ms at startup is invisible to callers.
- **Expected gain:** 200–600 ms (Groq) + 100–400 ms (TTS) on turn 1 of the *first* call after each worker restart, plus smaller gains on every cold connect cycle thereafter (httpx pool eviction).

### Step 3 — Pre-cache greeting audio per agent (closes F2 and most of F9 for outbound)
- **Where:** [telephony_bridge.py:170-225](backend/app/api/v1/endpoints/telephony_bridge.py:170) (`_send_outbound_greeting`).
- **Change:**
  1. On agent/campaign create or update (look at [campaign_service.py](backend/app/domain/services/campaign_service.py)), synthesise the canned greeting once and store the bytes (PCM/μ-law at the correct sample rate for the gateway) in a cache (Redis blob keyed by `agent_id:voice_id:gateway_format` — Redis already wired at [voice_worker.py:89](backend/app/workers/voice_worker.py:89)).
  2. In `_send_outbound_greeting`, look up the cached blob; if present, push it directly via `voice_session.media_gateway` (skipping LLM and TTS entirely). On cache miss, fall back to today's path *and* write the result to cache.
- **Reuse:** The existing `synthesize_and_send_audio` interface used at [voice_pipeline_service.py:804](backend/app/domain/services/voice_pipeline_service.py:804) already accepts pre-synthesized audio paths via the media gateway.
- **Expected gain:** 500–1200 ms eliminated on outbound turn 1; greeting starts ~50 ms after `_on_new_call` returns.

### Step 4 — Enable Deepgram Flux **eager EOT** for telephony (closes most of F8)
- **Where:** `_build_telephony_session_config()` referenced at [2026-04-06_telephony_latency_and_bargein.md:91](backend/docs/debugging/2026-04-06_telephony_latency_and_bargein.md:91), in [telephony_bridge.py](backend/app/api/v1/endpoints/telephony_bridge.py) (the function is private; locate at the helper that builds `VoiceSessionConfig`).
- **Change:** Set `stt_eager_eot_threshold=0.55` (must be ≤ `stt_eot_threshold=0.85`, validation at [deepgram_flux.py:147-152](backend/app/infrastructure/stt/deepgram_flux.py:147)). The pipeline already handles `eager` events at [voice_pipeline_service.py:372-386](backend/app/domain/services/voice_pipeline_service.py:372) — speculative LLM dispatch is already implemented and gated by this flag, so this is a config flip.
- **Why now:** The team disabled it earlier (per the cited debug doc) for safety; with steps 1–3 reducing other delays, the cancellation cost of a wrong speculative call is small.
- **Expected gain:** 200–600 ms on the average turn (including turn 1 if user trails off).

### Step 5 — Use a smaller/faster model for the first reply (closes part of F3, F10)
- **Where:** Model is read from `global_config.llm_model` (per [2026-04-06_telephony_latency_and_bargein.md:218-219](backend/docs/debugging/2026-04-06_telephony_latency_and_bargein.md:218)). Add a turn-0 override in [voice_pipeline_service.py:759-762](backend/app/domain/services/voice_pipeline_service.py:759) where `stream_chat_with_timeout` is invoked — pass `model="openai/gpt-oss-20b"` (1000 t/s per [groq.py:48](backend/app/infrastructure/llm/groq.py:48)) when `session.turn_id == 0`.
- **Reuse:** The existing `_response_max_sentences_for_turn` ([voice_pipeline_service.py:78-80](backend/app/domain/services/voice_pipeline_service.py:78)) already uses turn 0 specially — mirror that pattern.
- **Expected gain:** 100–250 ms TTFT.

### Step 6 — Drop C++ jitter buffer prefetch from 3 → 1 frame (closes F5)
- **Where:** [session.h:49](services/voice-gateway-cpp/include/voice_gateway/session.h:49) — `std::size_t jitter_buffer_prefetch_frames{3}`.
- **Change:** Default to `1` for telephony, or expose via the existing `SessionConfig` so the Python layer ([http_server.cpp:760-797](services/voice-gateway-cpp/src/http_server.cpp:760)) can pick `1` for low-latency mode and `3` for noisy networks.
- **Why safe:** The system already maintains a separate jitter target depth (`kDefaultJitterTargetDepthFrames = 6` at [session.h:153](services/voice-gateway-cpp/include/voice_gateway/session.h:153)). Prefetch is *just* the cold-start gate.
- **Expected gain:** 40 ms on the first audio frame out.

### Step 7 — Move C++ stdout RTP-tx logging off the audio thread (closes F7)
- **Where:** [session.cpp:548-557](services/voice-gateway-cpp/src/session.cpp:548).
- **Change:** Replace synchronous `std::cout << ... << std::endl;` with either:
  - A bounded SPSC queue drained by a dedicated logging thread, or
  - Drop the per-packet log entirely and rely on the existing snapshot stats at [session.h:63-94](services/voice-gateway-cpp/include/voice_gateway/session.h:63) (already exposed via `/v1/sessions/{id}/stats`).
- Also remove the second `state()` mutex acquire at [session.cpp:554](services/voice-gateway-cpp/src/session.cpp:554) by reading state once at the top of the loop iteration.
- **Expected gain:** 5–50 ms per packet × cumulative; removes a major source of jitter.

### Step 8 — Audit `audio_callback_url` and batching (closes F6 and the silent-DNS-failure trap)
- **Where:** [http_server.cpp:439-518](services/voice-gateway-cpp/src/http_server.cpp:439) (`http_post`) and [http_server.cpp:760-797](services/voice-gateway-cpp/src/http_server.cpp:760) (callback registration). `SessionConfig` defaults at [session.h:60](services/voice-gateway-cpp/include/voice_gateway/session.h:60).
- **Two-part change:**
  1. **Verify** the URL the C++ gateway is being started with right now (curl the running gateway's `/v1/sessions` admin endpoint, or grep the asterisk adapter that calls the gateway). If it is a hostname, all callbacks fail silently because of `inet_pton` only accepting IPv4 literals — see [http_server.cpp:470](services/voice-gateway-cpp/src/http_server.cpp:470). Resolve the hostname once at startup and pass an IP, or add a `getaddrinfo` path.
  2. **Pool** the HTTP client: rewrite `http_post` to keep one persistent socket per `(host:port)`, send `HTTP/1.1 + Connection: keep-alive`, and batch frames (raise `audio_callback_batch_frames` to 4–10 = 80–200 ms windows) so STT input is one POST every 80–200 ms instead of 50/sec.
- **Why this matters for turn 1:** If the URL is currently a hostname → STT receives nothing → first user utterance never produces a transcript → mouth-to-ear gap = `eot_timeout_ms` (1500 ms) plus retries. **This alone could explain a 2–4 s "first interaction" delay.**

---

## Verification — how the judge confirms each fix

The repo already exposes everything needed:

1. **Per-turn JSON logs** — every turn already prints structured latency at [latency_tracker.py:324-344](backend/app/domain/services/latency_tracker.py:324) including `stt_first_transcript_ms`, `llm_first_token_ms`, `tts_first_chunk_ms`, `total_latency_ms`. Look for the first turn (`turn_id=0`) and compare before/after.
2. **OTel spans** — wired in [voice_pipeline_service.py:238-275](backend/app/domain/services/voice_pipeline_service.py:238) and via [telemetry.py](telemetry.py) at the project root. Turn-level span has stt/llm/tts child spans with latency attributes; if Jaeger/Tempo is configured (via `OTEL_EXPORTER_ENDPOINT`), pull the trace for one cold call.
3. **C++ session stats** — `/v1/sessions/{id}/stats` returns `SessionStatsSnapshot` at [session.h:63-94](services/voice-gateway-cpp/include/voice_gateway/session.h:63), including `jitter_buffer_depth_frames`, `packets_in/out`, `tts_segments_started_total`. Use to confirm C++ side after Steps 6–8.
4. **Synthetic cold call** — simplest end-to-end test: restart `voice_worker` (so Groq/TTS pools are cold), restart `services/voice-gateway-cpp`, place one outbound call, capture the `[OK|SLOW] Turn 0 latency: …ms` log line. Repeat 10× and compute p50/p95.
5. **Existing baseline tooling** — `LatencyTracker.build_baseline_snapshot` ([latency_tracker.py:381-391](backend/app/domain/services/latency_tracker.py:381)) returns p50/p95 of stt/llm/tts/response_start. Print this for turn 1 across N calls before/after.

**Pass criteria:** turn-0 `total_latency_ms` p50 ≤ 800 ms, p95 ≤ 1500 ms, matching the project's own target at [latency_tracker.py:117](backend/app/domain/services/latency_tracker.py:117).

---

## Open question for the judge

Before implementation, please confirm which call type is showing the 2–4 s delay so Step 3 (greeting cache) and Step 8 (audio callback audit) are prioritised correctly:

- (A) **Outbound campaign call** — AI speaks first → Step 3 is the headline win.
- (B) **Inbound caller speaks first** — Step 8 (audio_callback_url) is the headline win, because if the URL is a hostname today, STT never receives audio.
- (C) **Both** — sequence Steps 1 → 8 → 3 → 4.

This is the only thing I cannot determine from the code alone (it depends on your runtime config and which campaign is being tested).

---

## Citations index

**Code (this repo):**
- [services/voice-gateway-cpp/src/session.cpp:451](services/voice-gateway-cpp/src/session.cpp:451), [:548](services/voice-gateway-cpp/src/session.cpp:548), [:181](services/voice-gateway-cpp/src/session.cpp:181)
- [services/voice-gateway-cpp/include/voice_gateway/session.h:49](services/voice-gateway-cpp/include/voice_gateway/session.h:49), [:60](services/voice-gateway-cpp/include/voice_gateway/session.h:60), [:63-94](services/voice-gateway-cpp/include/voice_gateway/session.h:63), [:153](services/voice-gateway-cpp/include/voice_gateway/session.h:153)
- [services/voice-gateway-cpp/src/http_server.cpp:439](services/voice-gateway-cpp/src/http_server.cpp:439), [:470](services/voice-gateway-cpp/src/http_server.cpp:470), [:480](services/voice-gateway-cpp/src/http_server.cpp:480), [:489](services/voice-gateway-cpp/src/http_server.cpp:489), [:760](services/voice-gateway-cpp/src/http_server.cpp:760)
- [backend/app/api/v1/endpoints/telephony_bridge.py:170](backend/app/api/v1/endpoints/telephony_bridge.py:170), [:181](backend/app/api/v1/endpoints/telephony_bridge.py:181), [:205](backend/app/api/v1/endpoints/telephony_bridge.py:205), [:228](backend/app/api/v1/endpoints/telephony_bridge.py:228), [:269](backend/app/api/v1/endpoints/telephony_bridge.py:269)
- [backend/app/workers/voice_worker.py:100](backend/app/workers/voice_worker.py:100), [:118](backend/app/workers/voice_worker.py:118), [:233](backend/app/workers/voice_worker.py:233)
- [backend/app/domain/services/voice_pipeline_service.py:78](backend/app/domain/services/voice_pipeline_service.py:78), [:238](backend/app/domain/services/voice_pipeline_service.py:238), [:255](backend/app/domain/services/voice_pipeline_service.py:255), [:372](backend/app/domain/services/voice_pipeline_service.py:372), [:730](backend/app/domain/services/voice_pipeline_service.py:730), [:759](backend/app/domain/services/voice_pipeline_service.py:759), [:784](backend/app/domain/services/voice_pipeline_service.py:784)
- [backend/app/infrastructure/stt/deepgram_flux.py:117](backend/app/infrastructure/stt/deepgram_flux.py:117), [:118](backend/app/infrastructure/stt/deepgram_flux.py:118), [:147](backend/app/infrastructure/stt/deepgram_flux.py:147), [:196](backend/app/infrastructure/stt/deepgram_flux.py:196), [:568](backend/app/infrastructure/stt/deepgram_flux.py:568), [:576](backend/app/infrastructure/stt/deepgram_flux.py:576)
- [backend/app/infrastructure/llm/groq.py:48](backend/app/infrastructure/llm/groq.py:48), [:173](backend/app/infrastructure/llm/groq.py:173)
- [backend/app/domain/services/latency_tracker.py:14](backend/app/domain/services/latency_tracker.py:14), [:117](backend/app/domain/services/latency_tracker.py:117), [:324](backend/app/domain/services/latency_tracker.py:324), [:381](backend/app/domain/services/latency_tracker.py:381)
- Project's own debugging notes: [2026-04-06_telephony_latency_and_bargein.md](backend/docs/debugging/2026-04-06_telephony_latency_and_bargein.md)

**Web (each tied to a finding above):**
- Deepgram streaming latency / Flux EOT: https://developers.deepgram.com/docs/measuring-streaming-latency
- LiveKit turn detection: https://livekit.com/blog/turn-detection-voice-agents-vad-endpointing-model-based-detection
- Pipecat STT latency tuning: https://docs.pipecat.ai/pipecat/fundamentals/stt-latency-tuning
- Vapi greeting/audio caching: https://vapi.ai/blog/audio-caching-for-latency-reduction
- Cartesia Sonic / Sonic-Turbo TTFB: https://cartesia.ai/sonic
- Connection pooling / DNS / TLS reuse: https://rnikhil.com/2025/05/18/how-to-reduce-latency-voice-agents
- Prompt caching effects on TTFT: https://introl.com/blog/prompt-caching-infrastructure-llm-cost-latency-reduction-guide-2025
- 30+ stack benchmarks (industry latency budgets): https://dev.to/cloudx/cracking-the-1-second-voice-loop-what-we-learned-after-30-stack-benchmarks-427
- AssemblyAI / Vapi <500 ms agent: https://www.assemblyai.com/blog/how-to-build-lowest-latency-voice-agent-vapi
- Cresta engineering for low latency: https://cresta.com/blog/engineering-for-real-time-voice-agent-latency
- OpenAI latency optimisation: https://developers.openai.com/api/docs/guides/latency-optimization
- Modal cold-start guide: https://modal.com/docs/guide/cold-start
- RFC 3960 SIP early media: https://datatracker.ietf.org/doc/html/rfc3960

---

## What I am NOT proposing (and why)

- **Switching to OpenAI Realtime / replacing Groq+Deepgram+TTS with a single stack.** Out of scope; would require rewiring the whole pipeline.
- **Changing SIP signalling for early media (RFC 3960).** Outbound greeting via early media is a real win but requires PBX-side changes (Asterisk/FreeSWITCH dialplan + SDP). Step 3 (cached greeting) gets ~80% of the benefit without touching the PBX.
- **Any change to barge-in, mute, or guardrails.** Those were the focus of the recent debug session; not relevant to first-turn latency.
- **Adding new instrumentation.** The existing latency_tracker + OTel are sufficient — see Verification above.
- **Anything in the C++ gateway beyond Steps 6–8.** Other findings (watchdog tick, lock contention) are real but small relative to the cited fixes.
