# Day 20: AI Options, Flux Optimization & MicroSIP Activation

**Date:** December 30, 2025  
**Objective:** Finalize AI Options configurability, optimize Deepgram Flux for minimal latency, and activate the MicroSIP SIP bridge for local telephony testing.

---

## Summary

| Component | Status | Impact |
|-----------|--------|--------|
| Google TTS Integration | ✅ Functional | 8 Chirp 3 HD voices available |
| AI Options - LLM Selection | ✅ Functional | Dynamic model switching |
| AI Options - TTS Voice Selection | ✅ Functional | 18 voices (Cartesia + Google) |
| Deepgram Flux Improvements | ✅ Optimized | Reduced connection drops |
| Campaign Voice Selection | ✅ Functional | Per-campaign voice assignment |
| MicroSIP Bridge | ✅ Functional | Local SIP testing enabled |

---

## 1. AI Options: Full Configurability

### Problem
The AI Options page allowed users to view available models and voices, but changes were not persisting or being applied to live sessions. LLM and TTS selections were effectively read-only.

### Solution
Implemented real-time configuration updates via WebSocket, with persistence to a global configuration store.

### Architecture

```
┌────────────────────┐      WebSocket       ┌────────────────────────┐
│    Frontend UI     │◄───────────────────►│  ai_options_ws.py      │
│   (ai-options)     │   (config updates)   │  (FastAPI Endpoint)    │
└────────────────────┘                      └──────────┬─────────────┘
                                                       │
                                                       ▼
                                            ┌────────────────────────┐
                                            │  global_ai_config.py   │
                                            │  (Singleton Store)     │
                                            └──────────┬─────────────┘
                                                       │
                       ┌───────────────────────────────┼───────────────────────────────┐
                       ▼                               ▼                               ▼
           ┌─────────────────────┐       ┌─────────────────────┐       ┌─────────────────────┐
           │   LLM Provider      │       │   TTS Provider      │       │  VoicePipeline      │
           │   (Groq/OpenAI)     │       │  (Cartesia/Google)  │       │  Service            │
           └─────────────────────┘       └─────────────────────┘       └─────────────────────┘
```

### Files Modified

| File | Action | Description |
|------|--------|-------------|
| `app/api/v1/endpoints/ai_options_ws.py` | MODIFIED | Added handlers for `update_llm`, `update_tts_voice`, `update_campaign_voice` |
| `app/domain/services/global_ai_config.py` | MODIFIED | Added setters for LLM model, TTS provider, TTS voice |
| `frontend/src/app/ai-options/page.tsx` | MODIFIED | Connected save buttons to WebSocket events |

### Key Code Changes

**Backend - WebSocket Handler:**
```python
# app/api/v1/endpoints/ai_options_ws.py
@router.websocket("/ws")
async def ai_options_websocket(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            
            if action == "update_llm":
                provider = data.get("provider")  # "groq", "openai"
                model = data.get("model")        # "llama-3.3-70b-versatile"
                global_ai_config.set_llm(provider, model)
                await websocket.send_json({"status": "ok", "llm": model})
                
            elif action == "update_tts_voice":
                voice_id = data.get("voice_id")
                provider = data.get("provider")  # "cartesia", "google"
                global_ai_config.set_tts_voice(provider, voice_id)
                await websocket.send_json({"status": "ok", "voice": voice_id})
```

**Frontend - Save Configuration:**
```typescript
// frontend/src/app/ai-options/page.tsx
const handleSaveConfiguration = () => {
  if (wsRef.current?.readyState === WebSocket.OPEN) {
    // Save LLM
    wsRef.current.send(JSON.stringify({
      action: 'update_llm',
      provider: selectedLLMProvider,
      model: selectedLLMModel
    }));
    
    // Save TTS Voice
    wsRef.current.send(JSON.stringify({
      action: 'update_tts_voice',
      provider: selectedVoice.provider,
      voice_id: selectedVoice.id
    }));
  }
};
```

---

## 2. Deepgram Flux Optimization

### Problem
The Deepgram Flux WebSocket connection was experiencing intermittent timeouts and "connection handshake" errors when running within the FastAPI event loop. The standalone test script worked fine, indicating event loop contention.

### Root Cause Analysis

| Symptom | Cause |
|---------|-------|
| `asyncio.TimeoutError` during handshake | FastAPI's event loop busy handling HTTP requests |
| Keepalive failures | Insufficient time to send heartbeat packets |
| Connection drops mid-session | Queue timeout in audio send loop |

### Solution
Aligned implementation with the official [Deepgram Flux demo](https://github.com/deepgram-devs/deepgram-demos-flux-agent) patterns:

1. **Removed Custom Timeouts:** The Flux demo uses default WebSocket parameters.
2. **Added User-Agent Header:** For better request tracking and debugging.
3. **Simplified Retry Logic:** Removed aggressive retry loops that could mask root issues.
4. **Non-Blocking Audio Buffer:** Changed from `asyncio.Queue.get(timeout=0.02)` to a polling loop with `asyncio.sleep(0.01)`.

### Before vs After

| Aspect | Before | After |
|--------|--------|-------|
| **WebSocket Connection** | Custom 30s timeout, 20s ping interval | Default parameters |
| **Headers** | `Authorization` only | `Authorization` + `User-Agent: TalkyAI-VoiceAgent/1.0` |
| **URL Parameters** | `?model=...&eot_threshold=0.7` | `?model=...&encoding=...&sample_rate=...` (no `eot_threshold`) |
| **Audio Loop** | `await queue.get(timeout=0.02)` | `await asyncio.sleep(0.01)` polling |

### Code Changes

**File:** `app/infrastructure/stt/deepgram_flux.py`

```python
# Before
headers = {"Authorization": f"Token {self._api_key}"}
url = f"wss://api.deepgram.com/v2/listen?model={self._model}&eot_threshold=0.7"
async with websockets.connect(url, additional_headers=headers, open_timeout=30, ping_interval=20) as ws:
    ...

# After
headers = {
    "Authorization": f"Token {self._api_key}",
    "User-Agent": "TalkyAI-VoiceAgent/1.0"
}
url = (
    f"wss://api.deepgram.com/v2/listen"
    f"?model={self._model}"
    f"&encoding={self._encoding}"
    f"&sample_rate={self._sample_rate}"
)
async with websockets.connect(url, additional_headers=headers) as ws:
    ...
```

**Audio Send Loop:**
```python
# Before - could timeout if queue empty
audio_data = await asyncio.wait_for(audio_queue.get(), timeout=0.02)

# After - non-blocking polling (matches official demo)
while self._is_connected:
    if not audio_queue.empty():
        audio_data = await audio_queue.get()
        await ws.send(audio_data)
    await asyncio.sleep(0.01)  # 10ms polling
```

---

## 3. Google TTS Integration

### Status: ✅ Functional

Google Cloud Text-to-Speech with Chirp 3 HD voices is now fully integrated and working alongside Cartesia.

### Voices Available

| Voice | Gender | Description | Use Case |
|-------|--------|-------------|----------|
| Orus | Male | Deep, authoritative | Professional calls |
| Charon | Male | Mature, reassuring | Trustworthy tone |
| Fenrir | Male | Energetic, confident | Sales, outreach |
| Puck | Male | Friendly, approachable | Customer service |
| Kore | Female | Warm, professional | Business communications |
| Aoede | Female | Clear, articulate | Appointments, reminders |
| Leda | Female | Soothing, empathetic | Support, healthcare |
| Zephyr | Female | Youthful, vibrant | Engagement, outreach |

### Authentication
Uses Service Account JSON file at `backend/config/google-service-account.json`.

```python
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
tts = GoogleTTSStreamingProvider()
```

---

## 4. Campaign Voice Selection

### Feature
Each outreach campaign can now have a specific TTS voice assigned, allowing for different agent personas.

### Implementation

**Database Schema (if using DB):**
```sql
-- campaigns table
ALTER TABLE campaigns ADD COLUMN voice_id VARCHAR(100);
ALTER TABLE campaigns ADD COLUMN voice_provider VARCHAR(50);
```

**API Endpoint:**
```
POST /api/v1/campaigns/{campaign_id}/voice
{
  "voice_id": "en-US-Chirp3-HD-Leda",
  "provider": "google"
}
```

**Usage in Pipeline:**
```python
# When starting a call for a campaign
voice_id = campaign.voice_id or global_ai_config.tts_voice_id
tts_provider = create_tts_provider(campaign.voice_provider or "cartesia")
```

---

## 5. MicroSIP Bridge Activation

### Status: ✅ Functional

The Python-based SIP bridge (implemented on Day 18) is now fully functional and tested with MicroSIP.

### Testing Workflow

1. **Start Backend:**
   ```bash
   cd backend
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

2. **Start SIP Bridge:**
   ```bash
   curl -X POST "http://localhost:8000/api/v1/sip/start?port=5060"
   ```

3. **Configure MicroSIP:**
   ```
   SIP Server: localhost:5060
   Username: agent001
   Password: (any)
   Transport: UDP
   Codec: PCMU (G.711 μ-law)
   ```

4. **Make Call:**
   - Dial any number in MicroSIP (e.g., `100`)
   - Call auto-answers
   - Speak to AI agent
   - Verify response

### Audio Flow

```
┌───────────┐   RTP/G.711    ┌─────────────────┐   PCM/16kHz    ┌────────────────┐
│ MicroSIP  │───────────────►│  SIPMediaGateway│───────────────►│ Deepgram Flux  │
│           │◄───────────────│  (G.711↔PCM)    │◄───────────────│ (STT)          │
└───────────┘                └─────────────────┘                └────────────────┘
                                     ▲                                   │
                                     │                                   ▼
                                     │                          ┌────────────────┐
                                     │                          │ Groq LLM       │
                                     │                          │ (Response)     │
                                     │                          └────────┬───────┘
                                     │                                   │
                                     │                                   ▼
                                     │                          ┌────────────────┐
                                     └──────────────────────────│ Cartesia TTS   │
                                                                │ (Audio)        │
                                                                └────────────────┘
```

---

## Performance Metrics

| Stage | Latency | Notes |
|-------|---------|-------|
| STT (Deepgram Flux) | ~200-300ms | Real-time streaming |
| LLM (Groq Llama 3.3) | ~300-500ms | State-aware responses |
| TTS (Cartesia Sonic 3) | ~90ms | First audio chunk |
| TTS (Google Chirp 3 HD) | ~200ms | Higher quality |
| **Total Round-Trip** | **~500-900ms** | Human-like response |

---

## Files Modified Today

| File | Lines Changed | Description |
|------|---------------|-------------|
| `app/infrastructure/stt/deepgram_flux.py` | ~50 | Flux optimization |
| `app/api/v1/endpoints/ai_options_ws.py` | ~80 | Config update handlers |
| `app/domain/services/global_ai_config.py` | ~30 | LLM/TTS setters |
| `app/domain/models/ai_config.py` | ~20 | Added Google voices |
| `frontend/src/app/ai-options/page.tsx` | ~100 | Save configuration |

---

## Environment Variables

```bash
# Required
DEEPGRAM_API_KEY=your_deepgram_key
GROQ_API_KEY=your_groq_key
CARTESIA_API_KEY=your_cartesia_key

# Optional - Google TTS (if using Google voices)
GOOGLE_APPLICATION_CREDENTIALS=config/google-service-account.json
```

---

## Testing Commands

### Verify AI Options
```bash
# Get available voices
curl http://localhost:8000/api/v1/ai-options/voices | jq 'length'
# Expected: 18

# Get LLM providers
curl http://localhost:8000/api/v1/ai-options/providers
```

### Test Flux Connection
```bash
cd backend
python test_deepgram_connection.py
```

### Test SIP Bridge
```bash
curl http://localhost:8000/api/v1/sip/status
# Expected: {"status": "running", "calls": 0}
```

---

## Known Issues & Next Steps

### Resolved Today
- [x] AI Options not persisting → WebSocket handlers added
- [x] Flux connection drops → Event loop optimization
- [x] Google TTS authentication → Service Account configured

### Remaining
- [ ] Add voice preview caching (reduce repeated TTS calls)
- [ ] Implement call recording storage for SIP calls
- [ ] Add latency metrics dashboard
- [ ] Support multiple concurrent SIP calls

---


