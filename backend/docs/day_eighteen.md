# Day 18: MicroSIP Integration

Day 18 focused on implementing SIP/RTP integration to connect MicroSIP softphone to the Talky.ai voice agent for local testing without external telephony costs.

---

## Summary

| Component | Files Created/Modified | Lines |
|-----------|----------------------|-------|
| SIP Bridge Server | `sip_bridge_server.py` | 470 |
| SIP Media Gateway | `sip_media_gateway.py` | 280 |
| SIP API Endpoints | `sip_bridge.py` | 250 |
| Configuration | `sip_config.yaml` | 45 |
| Factory Update | `factory.py` | +10 |
| Router Registration | `routes.py` | +3 |

---

## Architecture

```
┌───────────────┐   SIP/RTP      ┌───────────────────┐   Internal    ┌─────────────────────┐
│   MicroSIP    │◄──────────────►│  SIPBridgeServer  │◄────────────►│  VoicePipelineService│
│  (Softphone)  │   (G.711/8kHz) │  Port: 5060       │  (PCM/16kHz) │   (STT→LLM→TTS)     │
└───────────────┘                └───────────────────┘              └─────────────────────┘
        │                                │
        │                                ▼
        │                        ┌───────────────────┐
        │                        │  SIPMediaGateway  │
        │                        │  (Audio Conversion)│
        │                        │  G.711 ↔ PCM      │
        │                        └───────────────────┘
        │                                │
        └─── RTP Audio ──────────────────┘
             (UDP, ports 10000+)
```

---

## 1. SIP Bridge Server

### Problem
MicroSIP uses SIP signaling and RTP for audio transport, which differs from the WebSocket-based approach used by the browser dummy call feature.

### Solution
Created a pure Python SIP/RTP server that handles:
- **SIP REGISTER** - Phone registration
- **SIP INVITE** - Incoming call handling
- **SIP ACK** - Call confirmation
- **SIP BYE** - Call termination
- **RTP streaming** - Real-time audio transport

**Key features:**
```python
class SIPBridgeServer:
    def __init__(self, host="0.0.0.0", sip_port=5060, rtp_port_start=10000):
        # Handles SIP signaling on UDP port 5060
        # Allocates RTP ports dynamically for each call
        
    async def _handle_invite(self, message, addr):
        # Auto-answer calls with SDP response
        # Start RTP listener for audio
        
    async def _rtp_listener(self, call_id, local_port):
        # Receive G.711 audio from MicroSIP
        # Convert to PCM 16kHz for STT
        # Forward to voice pipeline
```

---

## 2. Audio Format Conversion

### G.711 μ-law to PCM Conversion
MicroSIP sends audio in G.711 μ-law (PCMU) codec at 8kHz. The STT requires PCM at 16kHz.

```python
# Decode G.711 μ-law → PCM 16-bit (8kHz)
pcm_8k = audioop.ulaw2lin(audio_payload, 2)

# Resample 8kHz → 16kHz for STT
pcm_16k, resample_state = audioop.ratecv(
    pcm_8k, 2, 1, 8000, 16000, resample_state
)
```

### PCM to G.711 Conversion (TTS Response)
```python
# Resample 16kHz → 8kHz
pcm_8k, _ = audioop.ratecv(audio_data, 2, 1, 16000, 8000, None)

# Encode PCM → G.711 μ-law
ulaw_data = audioop.lin2ulaw(pcm_8k, 2)
```

---

## 3. SIP Media Gateway

Implements the `MediaGateway` interface to integrate with existing `VoicePipelineService`:

```python
class SIPMediaGateway(MediaGateway):
    async def on_audio_received(self, call_id: str, audio_chunk: bytes):
        # Convert G.711 → PCM 16kHz
        # Queue for STT pipeline
        
    async def send_audio(self, call_id: str, audio_chunk: bytes):
        # Convert PCM 16kHz → G.711
        # Send via RTP
```

---

## 4. REST API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/sip/status` | GET | Get SIP bridge status |
| `/sip/start` | POST | Start SIP bridge server |
| `/sip/stop` | POST | Stop SIP bridge server |
| `/sip/calls` | GET | List active SIP calls |
| `/sip/audio/{call_id}` | WS | Stream call audio via WebSocket |

---

## 5. MicroSIP Configuration

### Settings
```
SIP Server: localhost:5060
Username: agent001 (any)
Password: (any)
Transport: UDP
```

### Codec Settings
```
1. PCMU (G.711 μ-law) - 8kHz ✓
2. PCMA (G.711 A-law) - 8kHz ✓
```

---

## Files Created/Modified

| File | Action | Description |
|------|--------|-------------|
| `app/infrastructure/telephony/sip_bridge_server.py` | NEW | SIP/RTP server (470 lines) |
| `app/infrastructure/telephony/sip_media_gateway.py` | NEW | Media gateway (280 lines) |
| `app/api/v1/endpoints/sip_bridge.py` | NEW | REST API (250 lines) |
| `config/sip_config.yaml` | NEW | Configuration (45 lines) |
| `app/infrastructure/telephony/factory.py` | MODIFIED | Added 'sip' gateway type |
| `app/api/v1/routes.py` | MODIFIED | Registered SIP router |
| `tests/unit/test_sip_media_gateway.py` | NEW | Gateway tests (280 lines) |
| `tests/unit/test_sip_bridge_server.py` | NEW | Server tests (320 lines) |
| `tests/unit/test_sip_bridge_api.py` | NEW | API tests (160 lines) |

---

## Test Results

```
tests/unit/test_sip_media_gateway.py - 20 tests PASSED
tests/unit/test_sip_bridge_server.py - 18 tests PASSED
tests/unit/test_sip_bridge_api.py - 4 tests PASSED
-----------------------------------------
Total: 42 tests PASSED in 1.45s
```

### Test Coverage
| Component | Tests | Coverage |
|-----------|-------|----------|
| SIPSession | 3 | Session creation, codec config |
| SIPMediaGateway | 12 | Audio conversion, queue, recording |
| Audio Conversion | 5 | μ-law ↔ PCM, resampling |
| SIPBridgeServer | 10 | Header parsing, SDP, RTP |
| SIPCall/RTPSession | 4 | Call state management |
| API Endpoints | 4 | Status, factory integration |
| Integration | 4 | Full audio pipeline, concurrency |

---

## Usage

### Start the SIP Bridge
```bash
# Option 1: Via API
curl -X POST "http://localhost:8000/api/v1/sip/start?port=5060"

# Option 2: The bridge can be auto-started on app startup
```

### Configure MicroSIP
1. Open MicroSIP → Settings → Account
2. Set SIP Server: `localhost:5060`
3. Set Username: `agent001`
4. Save and register

### Make a Test Call
1. In MicroSIP, dial any number (e.g., `100`)
2. Call auto-answers
3. Speak to the AI agent
4. End call normally

---

## Next Steps

1. [ ] Add auto-start on application startup
2. [ ] Integrate with VoicePipelineService for full STT→LLM→TTS flow
3. [ ] Add call recording storage
4. [ ] Add call metrics to database
5. [ ] Support multiple simultaneous calls

---

## Frontend Progress - Ask AI Voice Demo

### Voice Demo UI/UX Implementation

Created an interactive voice demo integrated into the hero section with a 3D helix animation that transforms into a sound waveform when active.

#### Features Implemented

| Feature | Description |
|---------|-------------|
| **3D Helix Animation** | Rotating helix made of torus rings with blue/purple gradient |
| **Sound Waveform Pattern** | Helix transforms into vertical bars facing camera when active |
| **Voice Reactivity** | Bars pulse with real audio levels (mic input AND AI speech output) |
| **Voice Selection UI** | Carousel with 3 voice agents (Sophia, Emma, Alex) |
| **Two-Phase Flow** | Browse voices → Select voice for conversation |
| **Barge-in Visual** | Audio level tracking for instant feedback |

#### Sound Waveform Specifics

```
When Active:
- Bars face camera (XY plane)
- Taller bars in center, shorter on edges (natural waveform shape)
- Only reacts to REAL audio (no random pulsation)
- Rotation stops (static pattern)
- Position stays fixed (right side of screen)
```

#### Button Positioning

```tsx
// Button always at center of waveform (right side)
transform: 'translate(calc(-50% + 22.5vw), -50%)'
```

### Backend Latency Optimizations

#### TTS Streaming (Instant First Audio)

| Setting | Value | Purpose |
|---------|-------|---------|
| First Chunk | 12,800 bytes (~200ms) | Fast first audio without jitter |
| Regular Chunks | 32,000 bytes (~500ms) | Smooth continuous playback |

#### Barge-in Detection

| Setting | Before | After |
|---------|--------|-------|
| Sample Size | 512 bytes | 256 bytes |
| Energy Threshold | 500 | 300 |

#### LLM Response Time

| Setting | Before | After |
|---------|--------|-------|
| max_tokens | 150 | 80 |
| Temperature | 0.7 | 0.6 |

#### Python Compatibility Fix

Fixed `asyncio.timeout` (Python 3.11+) to use manual timeout tracking for Python 3.10 compatibility in `groq.py`.

### Files Modified

| File | Changes |
|------|---------|
| `frontend/src/components/ui/helix-hero.tsx` | Sound waveform animation, voice selection UI, audio visualization |
| `backend/app/api/v1/endpoints/ai_options_ws.py` | TTS chunk optimization, barge-in thresholds |
| `backend/app/infrastructure/llm/groq.py` | asyncio.timeout fix, reduced max_tokens |
| `backend/app/domain/services/voice_pipeline_service.py` | Reduced max_tokens for faster response |

### Known Issues (For Tomorrow)

1. **State Transition Delay**: `No transition found for state=greeting, intent=unknown` causing pipeline delays
2. **Transcript Flush Error**: `'Client' object is not an iterator` - non-blocking but needs fix
3. **Voice Still Slightly Delayed**: Need to investigate STT→LLM→TTS workflow timing
