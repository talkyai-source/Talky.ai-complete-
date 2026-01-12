# MicroSIP Setup Guide for Talky.ai

## What You Need

### 1. Download and Install MicroSIP
- Download from: https://www.microsip.org/downloads
- Install on your Windows machine

### 2. Backend Requirements
Make sure these environment variables are set in your `.env`:
```bash
# Required API Keys
DEEPGRAM_API_KEY=your_deepgram_key
CARTESIA_API_KEY=your_cartesia_key
GROQ_API_KEY=your_groq_key
```

---

## MicroSIP Configuration

### Step 1: Open MicroSIP Account Settings
1. Launch MicroSIP
2. Right-click on the system tray icon → **Account Settings**
3. Or click the menu icon → **Account** → **Add**

### Step 2: Configure Account
Fill in these values:

| Field | Value |
|-------|-------|
| **Account Name** | Talky.ai Agent |
| **SIP Server** | `localhost` (or your PC's IP address) |
| **SIP Proxy** | Leave empty |
| **Username** | `agent001` |
| **Domain** | `localhost` |
| **Password** | Any value (e.g., `password123`) |
| **Transport** | `UDP` |
| **Port** | `5060` |

### Step 3: Audio Settings
1. Go to **Settings** → **Audio**
2. Set **Microphone** to your preferred input device
3. Set **Speaker** to your preferred output device
4. Enable **Echo Cancellation** if available

---

## Starting the SIP Bridge

### Option 1: Start Automatically with Server
The SIP bridge needs to be started explicitly. You can use the API:

```bash
# Start the backend
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# In another terminal, start the SIP bridge
curl -X POST "http://localhost:8000/api/v1/sip/start"
```

### Option 2: Check Status
```bash
curl http://localhost:8000/api/v1/sip/status
```

Expected response when running:
```json
{
  "status": "running",
  "sip_port": 5060,
  "host": "0.0.0.0",
  "active_calls": 0
}
```

---

## Making a Test Call

1. **Start the Backend** (if not already running):
   ```bash
   cd backend
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

2. **Start the SIP Bridge**:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/sip/start"
   ```

3. **Check MicroSIP Registration**:
   - MicroSIP should show "Online" status
   - The icon should be green

4. **Make a Call**:
   - In MicroSIP, dial any number (e.g., `1000` or `talky`)
   - The AI agent should answer automatically

5. **Speak to the AI**:
   - After the call connects, you should hear the AI greeting
   - Speak normally - the AI will respond

---

## Troubleshooting

### MicroSIP Shows "Offline"

**Check 1: Firewall**
- Allow MicroSIP through Windows Firewall
- Allow UDP ports 5060 (SIP) and 10000-20000 (RTP)

**Check 2: SIP Bridge Running**
```bash
curl http://localhost:8000/api/v1/sip/status
```
If status is "stopped", start it:
```bash
curl -X POST "http://localhost:8000/api/v1/sip/start"
```

**Check 3: Port Conflict**
- Another application might be using port 5060
- Use a different port:
```bash
curl -X POST "http://localhost:8000/api/v1/sip/start?port=5061"
```
Then update MicroSIP to use port 5061

### No Audio After Call Connects

**Check 1: RTP Ports**
- Ensure UDP ports 10000-20000 are open

**Check 2: Audio Devices**
- Check MicroSIP audio settings
- Ensure microphone and speakers are set correctly

**Check 3: Backend Logs**
```bash
# Look for RTP listener messages
curl http://localhost:8000/api/v1/sip/calls
```

### Lag or Delay in Responses

**Fix 1: Check Network**
- Using localhost should have minimal latency

**Fix 2: Audio Buffer**
- The system uses 4096-byte chunks for smooth audio

**Fix 3: Provider Latency**
Running servers:
- STT: Deepgram Flux (~200ms)
- LLM: Groq (~300-500ms)
- TTS: Cartesia (~90ms)

Expected total: 500-800ms end-to-end

---

## API Reference

### Start SIP Bridge
```bash
POST /api/v1/sip/start?host=0.0.0.0&port=5060
```

### Stop SIP Bridge
```bash
POST /api/v1/sip/stop
```

### Check Status
```bash
GET /api/v1/sip/status
```

### List Active Calls
```bash
GET /api/v1/sip/calls
```

---

## Architecture

```
┌───────────────┐      SIP/UDP       ┌─────────────────┐
│   MicroSIP    │◄──────────────────►│  SIP Bridge     │
│   Softphone   │      Port 5060     │  Server         │
└───────────────┘                    └────────┬────────┘
        │                                     │
        │                                     │
    RTP/UDP                              Internal
  Port 10000+                            Processing
        │                                     │
        ▼                                     ▼
┌───────────────┐                    ┌─────────────────┐
│  RTP Audio    │────────────────────│ SIP Media       │
│  (G.711)      │  Convert to PCM    │ Gateway         │
└───────────────┘                    └────────┬────────┘
                                              │
                                              ▼
                                     ┌─────────────────┐
                                     │ Voice Pipeline  │
                                     │ STT → LLM → TTS │
                                     └─────────────────┘
```

---

## Notes

- **Local Testing Only**: This SIP bridge is designed for local testing with MicroSIP
- **Production**: For production telephony, integrate with Vonage, Twilio, or FreeSWITCH
- **Audio Format**: MicroSIP uses G.711 μ-law (8kHz), which is converted to 16kHz PCM for STT
