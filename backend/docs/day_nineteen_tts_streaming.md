# Day Nineteen - Google TTS Streaming Upgrade

**Date:** December 29, 2024  
**Objective:** Upgrade Google Cloud TTS from REST API to Bidirectional Streaming for ultra-low latency voice synthesis

---

## Problem Statement

Our voice pipeline was experiencing **1-3 second TTS latency**, making conversations feel unnatural and slow. This was unacceptable for a real-time voice AI product where low latency is a key feature.

### Measured Latency (Before)
From interactive voice test (`test_voice_interactive.py`):

| Turn | LLM Latency | TTS Latency | Total |
|------|-------------|-------------|-------|
| Introduction | 0ms | **2,152ms** | 2,152ms |
| Turn 1 | 545ms | **994ms** | 1,540ms |
| Turn 2 | 279ms | **1,160ms** | 1,438ms |
| Turn 3 | 497ms | **1,350ms** | 1,846ms |

**Root Cause:** Google Cloud TTS REST API (`text:synthesize`) is non-streaming - it generates the complete audio before returning ANY data.

---

## Solution: Bidirectional Streaming

According to [Google's official documentation](https://cloud.google.com/text-to-speech/docs/create-audio-text-streaming):

> "Bidirectional streaming lets you send text input and receive audio data simultaneously. This reduces latency and enables real-time interactions."

### Key Changes

| Aspect | Before (REST) | After (Streaming) |
|--------|--------------|-------------------|
| **API** | `text:synthesize` | `streaming_synthesize` |
| **Library** | `aiohttp` (HTTP) | `google-cloud-texttospeech` (gRPC) |
| **Auth** | API Key | Service Account JSON |
| **Flow** | Send all → Wait → Get all | Send chunks ↔ Get chunks |
| **Expected Latency** | 1-3 seconds | **100-300ms** |

---

## Files Changed

### 1. `app/infrastructure/tts/google_tts.py`

**What changed:** Complete rewrite to use gRPC streaming

**Before:**
```python
# REST API - waits for complete audio
TTS_API_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

async with self._session.post(url, json=request_payload) as response:
    result = await response.json()  # Blocks until ALL audio ready
    audio_content = base64.b64decode(result["audioContent"])
```

**After:**
```python
# gRPC streaming - audio arrives as text is processed
from google.cloud.texttospeech_v1 import TextToSpeechAsyncClient

async for response in self._client.streaming_synthesize(request_generator()):
    yield AudioChunk(data=response.audio_content)  # Immediate chunks
```

**Why:** The REST API inherently cannot stream. The gRPC `streaming_synthesize` method is the only way to get audio chunks as they're generated.

---

### 2. Authentication Change

**Before:** API Key in `.env`
```bash
GOOGLE_TTS_API_KEY=AIza...
```

**After:** Service Account JSON
```bash
GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\service-account.json
```

**Why:** The gRPC streaming API requires OAuth2 authentication via Service Account credentials. API Keys only work with REST endpoints.

---

### 3. New Dependency

```bash
pip install google-cloud-texttospeech
```

**Why:** The official Google Cloud Python client library includes the gRPC client needed for streaming. The previous `aiohttp` library only supports HTTP REST calls.

---

## Architecture Impact

### WebSocket Compatibility ✅

The gRPC client works alongside WebSockets without conflict:

```
Browser ←→ WebSocket (FastAPI) ←→ VoicePipelineService ←→ gRPC (to Google Cloud)
                 ↑                                              ↑
            Unchanged                                    New streaming
```

- `TextToSpeechAsyncClient` is fully async/await compatible
- Runs on the same asyncio event loop as FastAPI
- gRPC is an internal backend connection, not replacing WebSockets

---

## Known Limitations

From Google's documentation:

| Limitation | Value | Impact |
|------------|-------|--------|
| **Status** | PREVIEW (not GA) | May change without notice |
| **Concurrent sessions** | 100/project | Limits simultaneous calls |
| **Text per request** | 5,000 bytes | Must chunk long responses |
| **Chirp3 requests/min** | 200 | Lower than REST API (1000) |
| **Voice compatibility** | Chirp 3: HD only | ✅ Already using these |

---

## Testing

### Interactive Voice Test
```bash
cd backend
python -m tests.integration.test_voice_interactive
```

This test:
- Captures microphone audio
- Sends to WebSocket
- Displays transcripts, LLM responses in terminal
- Plays TTS audio back
- Shows latency metrics

### Expected Results After Upgrade
- **Before:** TTS ~1000-3000ms
- **After:** TTS ~200-500ms (4-10x improvement)

---

## Service Account Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Select your project (same one with TTS API enabled)
3. Navigate: **IAM & Admin → Service Accounts**
4. Click **"+ CREATE SERVICE ACCOUNT"**
   - Name: `talky-tts-service`
   - Role: **Cloud Text-to-Speech User**
5. Create JSON key: **Keys → ADD KEY → Create new key → JSON**
6. Set environment variable:
   ```bash
   GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\key.json
   ```

---

## Rollback Plan

If issues occur, revert to REST API by:
1. Removing `GOOGLE_APPLICATION_CREDENTIALS`
2. Restoring `GOOGLE_TTS_API_KEY`
3. Using the old `GoogleTTSProvider` implementation (backed up)

---

## Related Files

- `app/infrastructure/tts/google_tts.py` - Main TTS provider
- `tests/integration/test_google_tts.py` - Unit tests
- `tests/integration/test_voice_interactive.py` - Interactive test
- `app/api/v1/endpoints/ai_options_ws.py` - WebSocket endpoint using TTS

---





#### 1. TTS Provider Switch: Google → Cartesia

Switched from Google Cloud TTS (which had permission issues) to **Cartesia Sonic 3** for ultra-low latency TTS.

| Metric | Google TTS (REST) | Google TTS (Streaming) | Cartesia Sonic 3 |
|--------|-------------------|------------------------|------------------|
| **First Audio** | 1-3 seconds | 200-500ms | **~90ms** |
| **Auth** | Service Account | Service Account | API Key |
| **Model** | Chirp 3 HD | Chirp 3 HD | Sonic 3 |

**Implementation:** `app/infrastructure/tts/cartesia.py`
```python
from cartesia import AsyncCartesia

# SSE streaming for lowest latency
sse_stream = await self._client.tts.sse(
    model_id="sonic-3",
    transcript=text,
    voice_id=voice_id,
    stream=True
)
```

**Voice Selection for Agents:**
- **Sophia** (Katie voice) - Professional, warm female
- **Emma** (Aurora voice) - Energetic, friendly female
- **Alex** (Kiefer voice) - Confident, clear male

---

#### 2. Full Voice Pipeline: STT → LLM → TTS

The complete voice pipeline now operates with minimal latency:

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐     ┌─────────────┐
│  Microphone │────►│ Deepgram Flux   │────►│  Groq LLM   │────►│  Cartesia   │
│  (16kHz)    │     │ STT (~200ms)    │     │ (~300-500ms)│     │ TTS (~90ms) │
└─────────────┘     └─────────────────┘     └─────────────┘     └─────────────┘
                          │                        │                    │
                          ▼                        ▼                    ▼
                    Real-time             State-aware          Streaming audio
                    transcripts           responses            chunks
```

**Total End-to-End Latency:** ~500-800ms (down from 2-4 seconds)

---

#### 3. MicroSIP Real Calling Integration

Made the workflow production-ready for real SIP-based calling via MicroSIP softphone.

**Architecture:**
```
┌───────────┐   SIP/RTP    ┌─────────────────┐   Internal    ┌────────────────┐
│ MicroSIP  │◄────────────►│  SIP Bridge     │◄─────────────►│ Voice Pipeline │
│ Softphone │  (G.711)     │  Server         │   (PCM)       │ STT→LLM→TTS    │
└───────────┘              └─────────────────┘               └────────────────┘
```

**Files Implemented:**
| File | Purpose |
|------|---------|
| `app/infrastructure/telephony/sip_bridge_server.py` | SIP signaling & RTP handling |
| `app/infrastructure/telephony/sip_media_gateway.py` | G.711↔PCM audio conversion |
| `app/api/v1/endpoints/sip_bridge.py` | REST/WebSocket API for SIP calls |
| `config/sip_config.yaml` | SIP server configuration |

**MicroSIP Setup:**
```ini
SIP Server: localhost:5060
Username: agent001
Password: <any>
Transport: UDP
Codec: PCMU (G.711 μ-law)
```

**API Endpoints:**
- `POST /api/v1/sip/start` - Start SIP bridge server
- `POST /api/v1/sip/stop` - Stop SIP bridge
- `GET /api/v1/sip/status` - Check bridge status
- `GET /api/v1/sip/calls` - List active calls
- `WS /api/v1/sip/audio/{call_id}` - Audio streaming WebSocket

---

#### 4. Lag Fixes & Optimizations

**Issues Fixed:**

| Issue | Cause | Fix |
|-------|-------|-----|
| Audio jitter | Inconsistent chunk sizes | Standardized to 4096 bytes |
| TTS delay | Waiting for full response | Streaming synthesis with barge-in support |
| G.711 conversion lag | Per-sample processing | Batch conversion with `audioop` |
| Frontend buffer underrun | Insufficient prebuffering | Added 100ms initial buffer |

**Audio Format Handling:**
```python
# SIP Media Gateway: G.711 → PCM conversion
# 8kHz → 16kHz upsampling for STT

# Input: G.711 μ-law (8kHz, mono)
pcm_data = audioop.ulaw2lin(audio_chunk, 2)  # Convert to PCM
# Resample 8kHz → 16kHz for Deepgram STT
upsampled = audioop.ratecv(pcm_data, 2, 1, 8000, 16000, None)[0]
```

**Barge-In Support:**
The pipeline now supports interrupting TTS when the user starts speaking:
```python
# Voice Pipeline handles barge-in signal
if isinstance(transcript, BargeInSignal):
    await self.handle_barge_in(session, websocket)
    # Stops TTS playback immediately
```

---

### Testing Tools

#### Interactive Flux Pipeline Test
```bash
cd backend
python test_flux_pipeline.py
```

Tests the exact STT pipeline used in production with real microphone input.

#### SIP Bridge Test
```bash
# Start the server
uvicorn app.main:app --reload

# Check SIP status
curl http://localhost:8000/api/v1/sip/status

# Start SIP bridge
curl -X POST "http://localhost:8000/api/v1/sip/start"
```

---

### Current Performance Metrics

| Stage | Latency | Notes |
|-------|---------|-------|
| STT (Deepgram Flux) | ~200-300ms | Real-time streaming |
| LLM (Groq) | ~300-500ms | State-aware responses |
| TTS (Cartesia) | ~90ms | First audio chunk |
| **Total Round-Trip** | **~500-800ms** | Human-like response time |

---

### Environment Variables Required

```bash
# Cartesia TTS (Primary)
CARTESIA_API_KEY=your_key_here

# Deepgram STT
DEEPGRAM_API_KEY=your_key_here

# Groq LLM
GROQ_API_KEY=your_key_here

# Optional: Google TTS (backup, commented out)
# GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json
```

---

ge)
