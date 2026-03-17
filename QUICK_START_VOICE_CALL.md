# Quick Start: Make a Voice Call with Talky.ai

## ✅ Backend Status: RUNNING

The backend is running on `http://localhost:8000` with all services initialized:
- ✅ PostgreSQL database connected
- ✅ Redis cache connected
- ✅ Deepgram STT configured
- ✅ Groq LLM configured
- ✅ Cartesia TTS configured
- ✅ Voice Orchestrator ready

---

## Option 1: Browser Voice Call (Easiest)

### Step 1: Open the Test Page

Open `test_voice_call.html` in your browser:

```bash
# Option A: Using Python's built-in server
python3 -m http.server 8080

# Then open: http://localhost:8080/test_voice_call.html
```

Or simply double-click `test_voice_call.html` to open it directly in your browser.

### Step 2: Start the Call

1. Click "Start Voice Call"
2. Allow microphone access when prompted
3. Wait for "Call active - Speak now!" message
4. Start talking to the AI!

### What to Expect:

- **AI Greeting**: "Hello! I'm your AI assistant. How can I help you today?"
- **You speak**: The system transcribes your speech in real-time
- **AI responds**: Natural conversation with < 1 second latency
- **Metrics displayed**: STT, LLM, TTS, and total latency

### Example Conversation:

```
You: "Hello, how are you?"
AI: "I'm doing great, thank you for asking! How can I assist you today?"

You: "Tell me about Talky.ai"
AI: "Talky.ai is an AI-powered voice platform that enables natural conversations..."
```

---

## Option 2: API Testing (For Developers)

### Test the Voice Demo Endpoint

```bash
# Check available endpoints
curl http://localhost:8000/docs

# Test voice demo WebSocket (requires WebSocket client)
wscat -c ws://localhost:8000/api/v1/ws/voice-demo
```

### Test with Python Script

```python
import asyncio
import websockets
import json

async def test_voice_call():
    uri = "ws://localhost:8000/api/v1/ws/voice-demo"
    
    async with websockets.connect(uri) as websocket:
        # Receive greeting
        greeting = await websocket.recv()
        print(f"Received: {greeting}")
        
        # Send audio data (16-bit PCM, 16kHz)
        # ... (implement audio capture)
        
        # Receive responses
        while True:
            response = await websocket.recv()
            data = json.loads(response)
            print(f"Type: {data['type']}, Data: {data}")

asyncio.run(test_voice_call())
```

---

## Option 3: Real Phone Call (Production)

### Prerequisites:

1. **Start Telephony Stack**:
   ```bash
   cd telephony/deploy/docker
   docker-compose -f docker-compose.telephony.yml up -d
   ```

2. **Configure PBX** (if using external PBX):
   - Edit `telephony/asterisk/conf/pjsip.conf`
   - Add your PBX IP, credentials, and codecs
   - Reload Asterisk: `docker exec asterisk asterisk -rx "pjsip reload"`

3. **Start Telephony Bridge**:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/sip/telephony/start?adapter_type=asterisk"
   ```

4. **Make a Call**:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/sip/telephony/call?destination=1002&caller_id=1001"
   ```

---

## Troubleshooting

### Backend Not Starting

```bash
# Check if port 8000 is in use
lsof -i:8000

# Kill process if needed
kill -9 $(lsof -ti:8000)

# Restart backend
cd backend
source venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Microphone Not Working

1. Check browser permissions (Chrome: Settings → Privacy → Microphone)
2. Use HTTPS or localhost (required for microphone access)
3. Try a different browser (Chrome/Firefox recommended)

### No Audio Response

1. Check browser console for errors
2. Verify API keys in `backend/.env`:
   - `DEEPGRAM_API_KEY` (STT)
   - `GROQ_API_KEY` (LLM)
   - `CARTESIA_API_KEY` (TTS)
3. Check backend logs for errors

### WebSocket Connection Failed

```bash
# Check backend is running
curl http://localhost:8000/health

# Check WebSocket endpoint
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
  http://localhost:8000/api/v1/ws/voice-demo
```

---

## API Endpoints

### Voice Demo (Browser)
- **WebSocket**: `ws://localhost:8000/api/v1/ws/voice-demo`
- **Protocol**: Binary audio (16-bit PCM, 16kHz, mono)

### Telephony (SIP/PBX)
- **Start**: `POST /api/v1/sip/telephony/start`
- **Call**: `POST /api/v1/sip/telephony/call`
- **Status**: `GET /api/v1/sip/telephony/status`
- **Hangup**: `POST /api/v1/sip/telephony/hangup/{call_id}`

### Health Check
- **Endpoint**: `GET /health`
- **Response**: `{"status":"healthy","container":"initialized"}`

---

## Next Steps

1. **Test Browser Call**: Open `test_voice_call.html` and start talking!
2. **Check Metrics**: Monitor latency in real-time
3. **Try Different Prompts**: Ask about products, services, or general questions
4. **Deploy Telephony**: Set up real phone calls with the telephony stack

---

## Support

- **Documentation**: `backend/docs/`
- **API Docs**: `http://localhost:8000/docs`
- **Telephony Docs**: `telephony/README.md`
- **Security**: `telephony/SECURITY_FIXES_SUMMARY.md`

---

**Status**: 🟢 Backend Running | Ready for Voice Calls!
