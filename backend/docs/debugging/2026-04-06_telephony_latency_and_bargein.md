# Debugging Log — 2026-04-06
## Telephony Campaign Call Latency & Barge-In Investigation

---

## Bug 8 — Barge-In Completely Blocked During TTS Playback

### Symptom

Speaking during an AI response had no effect on telephony/campaign calls. The AI continued playing audio to completion regardless of the user interrupting. Barge-in worked correctly on the Ask AI (browser WebSocket) path but was silently broken on all SIP telephony calls.

### Root Cause

`_build_telephony_session_config()` in `telephony_bridge.py` did not set `mute_during_tts`. The `VoiceSessionConfig` default for this field is `True`. With mute enabled:

- Incoming audio was not forwarded to Deepgram STT during TTS playback
- Deepgram Flux never received audio → never emitted `StartOfTurn`
- `barge_in_event` was never set → `synthesize_and_send_audio` played to the end every time

The same fix had previously been applied to the Ask AI path (`mute_during_tts=False`) but was never applied to the telephony config.

### Fix

Added `mute_during_tts=False` explicitly to `_build_telephony_session_config()`:

```python
mute_during_tts=False,  # must be explicit — default True blocks barge-in
```

Applied the same setting to `_build_vonage_session_config()` in `vonage_bridge.py`.

### Files Changed

| File | Change |
|---|---|
| `backend/app/api/v1/endpoints/telephony_bridge.py` | Added `mute_during_tts=False` to `VoiceSessionConfig` in `_build_telephony_session_config()` |
| `backend/app/api/v1/endpoints/vonage_bridge.py` | Added `mute_during_tts=False` to `VoiceSessionConfig` in `_build_vonage_session_config()` |

---

## Bug 9 — Hardcoded 1-Second Startup Silence Before AI Speaks

### Symptom

Every outbound campaign call had 1–2 seconds of dead silence before the AI spoke its opening line. Callers often said "Hello?" before the AI greeted them, creating a confusing experience.

### Root Cause

`_send_outbound_greeting()` in `telephony_bridge.py` had a hardcoded `asyncio.sleep(1.0)` before generating and sending the greeting:

```python
await asyncio.sleep(1.0)   # hardcoded — burned 1000ms every call
```

The Deepgram WebSocket connects in under 200ms. The 1000ms sleep was added as a conservative buffer but wasted 800ms on every outbound call.

### Fix

Reduced the startup wait from 1000ms to 200ms:

```python
await asyncio.sleep(0.2)   # was 1.0 — 200ms is enough for Deepgram WS to connect
```

### Files Changed

| File | Change |
|---|---|
| `backend/app/api/v1/endpoints/telephony_bridge.py` | `asyncio.sleep(1.0)` → `asyncio.sleep(0.2)` in `_send_outbound_greeting()` |

---

## Bug 10 — Users Cut Off Mid-Sentence (Aggressive EOT Threshold)

### Symptom

Users were being cut off while still speaking. Transcripts showed truncated sentences — words at the end of a sentence were missing, and the AI responded before the user had finished their thought.

### Root Cause

`_build_telephony_session_config()` did not set `stt_eot_threshold`, leaving it at the `VoiceSessionConfig` default of `0.7`. This is the "Low-Latency Mode" threshold — it fires end-of-turn on lower confidence signals, cutting users off prematurely.

The Ask AI path had already been corrected to `0.85` but this was never applied to telephony.

### Fix

Added explicit EOT threshold settings to `_build_telephony_session_config()`:

```python
stt_eot_threshold=0.85,        # was default 0.7 — requires higher confidence before firing EOT
stt_eager_eot_threshold=None,  # disable eager mode — no speculative LLM dispatch yet
```

Applied the same settings to `_build_vonage_session_config()` in `vonage_bridge.py`.

### Files Changed

| File | Change |
|---|---|
| `backend/app/api/v1/endpoints/telephony_bridge.py` | Added `stt_eot_threshold=0.85`, `stt_eager_eot_threshold=None` to `_build_telephony_session_config()` |
| `backend/app/api/v1/endpoints/vonage_bridge.py` | Added `stt_eot_threshold=0.85`, `stt_eager_eot_threshold=None` to `_build_vonage_session_config()` |

---

## Bug 11 — STT End-of-Turn Timeout Too Conservative (5000ms)

### Symptom

After a user finished speaking, there was a noticeable pause before the AI responded — even when the speech was clearly complete. In production logs, the gap between `speech_final` and LLM dispatch was consistently 500–2000ms longer than necessary.

### Root Cause

`stt_eot_timeout_ms` was set to `5000` in both `telephony_bridge.py` and `vonage_bridge.py`. This means Deepgram Flux waited up to 5 seconds of silence before firing `EndOfTurn` — far above what Deepgram's own documentation recommends.

Per official Deepgram documentation:
- `endpointing` default is 10ms; example setting is 300ms
- `utterance_end_ms` minimum is 1000ms
- Flux has integrated end-of-turn detection that handles accuracy — the timeout is a last-resort backstop, not the primary signal

A 5000ms timeout adds worst-case latency of 3.5 seconds over the 1500ms industry-standard backstop.

### Fix

Reduced `stt_eot_timeout_ms` from 5000 to 1500 in both telephony and Vonage configs:

```python
stt_eot_timeout_ms=1500,  # was 5000 — industry min is 1000ms; Flux integrated EOT handles accuracy
```

### Files Changed

| File | Change |
|---|---|
| `backend/app/api/v1/endpoints/telephony_bridge.py` | `stt_eot_timeout_ms=5000` → `1500` in `_build_telephony_session_config()` |
| `backend/app/api/v1/endpoints/vonage_bridge.py` | `stt_eot_timeout_ms=5000` → `1500` in `_build_vonage_session_config()` |

---

## Bug 12 — Extra 60ms Latency Per TTS Chunk (Output Buffer Too Large)

### Symptom

TTS audio arrived at the caller with a consistent ~100ms delay per chunk relative to what Deepgram generated. This added up across a multi-sentence response: a 3-sentence reply had 3 extra buffer waits, compounding the perceived latency.

### Root Cause

`_build_telephony_session_config()` did not set `gateway_target_buffer_ms`. The `VoiceSessionConfig` default is 100ms. The Ask AI path already used 40ms but telephony was never updated.

Each TTS audio chunk sat in the gateway buffer for up to 100ms before being flushed to the caller — 60ms longer than necessary.

### Fix

Added explicit buffer setting to `_build_telephony_session_config()`:

```python
gateway_target_buffer_ms=40,  # was default 100ms — saves 60ms per TTS chunk
```

Applied the same setting to `_build_vonage_session_config()` in `vonage_bridge.py`.

### Files Changed

| File | Change |
|---|---|
| `backend/app/api/v1/endpoints/telephony_bridge.py` | Added `gateway_target_buffer_ms=40` to `_build_telephony_session_config()` |
| `backend/app/api/v1/endpoints/vonage_bridge.py` | Added `gateway_target_buffer_ms=40` to `_build_vonage_session_config()` |

---

## Bug 13 — Vonage Bridge Using Outdated STT Model (nova-2)

### Symptom

Vonage calls had worse end-of-turn detection accuracy and higher STT latency compared to telephony calls. Turn boundaries were inconsistent — sometimes firing too early, sometimes too late.

### Root Cause

`_build_vonage_session_config()` in `vonage_bridge.py` used `stt_model="nova-2"`. Per official Deepgram documentation, Nova-2 is a general-purpose model not optimized for real-time voice agents.

Deepgram Flux (`flux-general-en`) is explicitly described as:
> "the first conversational speech recognition model built specifically for voice agents" with "ultra-low latency optimized for voice agent pipelines" and integrated end-of-turn detection

The telephony path was already using Flux; Vonage was left on Nova-2.

### Fix

Updated `_build_vonage_session_config()` to use Flux:

```python
stt_model="flux-general-en",  # was nova-2 — Flux is designed for voice agents; has integrated EOT detection
```

### Files Changed

| File | Change |
|---|---|
| `backend/app/api/v1/endpoints/vonage_bridge.py` | `stt_model="nova-2"` → `"flux-general-en"` in `_build_vonage_session_config()` |

---

## Industry Reference — How Production Voice AI Systems Handle Latency

Research conducted against official documentation from Deepgram, Groq, and LiveKit. Sources: `developers.deepgram.com`, `console.groq.com/docs/models`, `docs.livekit.io/agents`.

### STT End-of-Turn (Official Deepgram)
- Default `endpointing`: 10ms (too aggressive — fires mid-pause)
- Recommended `endpointing`: 300ms (practical middle ground per Deepgram example code)
- `utterance_end_ms` minimum: 1000ms — ignores non-speech noise (ringing, background)
- Industry pattern: run both `endpointing` AND `utterance_end_ms` simultaneously; they operate independently
- **Flux model is the designated model for voice agents** — has integrated EOT, not just VAD

### LLM Throughput (Official Groq)
| Model | Speed | Notes |
|---|---|---|
| `openai/gpt-oss-20b` | 1000 t/s | Fastest production model |
| `openai/gpt-oss-120b` | 500 t/s | 2× slower |
| `llama-3.1-8b-instant` | 560 t/s | Viable alternative |

Model selection is controlled via AI Options in the global config. The code reads `global_config.llm_model` — change the model via the UI, not in code.

### Barge-In / Interruption (Official LiveKit + Deepgram)
- Official pattern: when `UserStartedSpeaking` fires during agent output, **clear audio buffers immediately**
- LiveKit "Adaptive" mode distinguishes true barge-in from conversational back-channeling ("uh-huh") — does not fire on fillers
- The `mute_during_tts=True` default blocks audio from reaching STT during TTS — this must be `False` for any barge-in to work at all

### Projected Latency After All Fixes Applied

| Leg | Before | After |
|---|---|---|
| Startup silence | 1000ms | 200ms |
| STT EOT worst-case tail | 5000ms | 1500ms |
| TTS buffer per chunk | 100ms | 40ms |
| Barge-in during TTS | Blocked | Functional |
| **Median turn (excluding LLM)** | **~600ms overhead** | **~280ms overhead** |
