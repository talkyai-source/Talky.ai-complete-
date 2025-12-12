# Talky.ai - Comprehensive Testing Guide

This guide covers testing all components of the Talky.ai Voice AI Dialer platform.

---

## Quick Start

### 1. Run All Unit Tests
```bash
cd c:\Users\AL AZIZ TECH\Desktop\Talky.ai-complete-\backend
python -m pytest tests/unit/ -v
```

### 2. Run All Integration Tests
```bash
python -m pytest tests/integration/ -v
```

### 3. Start the Server
```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Test Categories

### Unit Tests (22 files)

| Test File | What It Tests |
|-----------|---------------|
| `test_api_endpoints.py` | All API endpoint routing |
| `test_audio_utils.py` | Audio format conversion |
| `test_conversation_engine.py` | State machine logic |
| `test_core.py` | Core functionality |
| `test_day9.py` | Day 9 features |
| `test_day10.py` | Recording & transcript services |
| `test_dialer_engine.py` | Dialer queue management |
| `test_latency_tracker.py` | Latency measurements |
| `test_media_gateway.py` | Vonage/RTP gateways |
| `test_prompt_manager.py` | Prompt templates |
| `test_rtp_builder.py` | RTP packet building |
| `test_session.py` | Call session handling |
| `test_websocket_messages.py` | WebSocket protocols |

### Integration Tests

| Test File | What It Tests |
|-----------|---------------|
| `test_day3_completion.py` | Day 3 integration |
| `test_day4_audio_pipeline.py` | Audio pipeline |
| `test_day5_groq_integration.py` | Groq LLM integration |
| `test_deepgram_connection.py` | Deepgram STT |
| `test_dialer_integration.py` | Dialer with Vonage |
| `test_text_to_voice.py` | Cartesia TTS |
| `test_tts_streaming.py` | TTS streaming |
| `test_voice_pipeline.py` | Full STT→LLM→TTS |
| `test_voice_pipeline_conversation.py` | Multi-turn conversations |

---

## API Endpoints Testing

Start the server first, then use curl or Postman:

### Health Check
```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/health
```

### Authentication (requires Supabase auth)
```bash
# Login
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "password"}'
```

### Campaigns
```bash
# List campaigns (requires auth token)
curl http://localhost:8000/api/v1/campaigns \
  -H "Authorization: Bearer YOUR_TOKEN"

# Create campaign
curl -X POST http://localhost:8000/api/v1/campaigns \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Campaign", "system_prompt": "You are a helpful AI agent.", "voice_id": "alloy"}'

# Start campaign (triggers dialer)
curl -X POST http://localhost:8000/api/v1/campaigns/{campaign_id}/start \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Contacts/Leads
```bash
# List contacts
curl http://localhost:8000/api/v1/contacts?campaign_id={id} \
  -H "Authorization: Bearer YOUR_TOKEN"

# Create contact
curl -X POST http://localhost:8000/api/v1/contacts \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"campaign_id": "...", "phone_number": "+1234567890", "first_name": "John"}'
```

### Calls
```bash
# List calls
curl http://localhost:8000/api/v1/calls \
  -H "Authorization: Bearer YOUR_TOKEN"

# Get call details
curl http://localhost:8000/api/v1/calls/{call_id} \
  -H "Authorization: Bearer YOUR_TOKEN"

# Get call transcript (Day 10)
curl "http://localhost:8000/api/v1/calls/{call_id}/transcript?format=json" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Recordings
```bash
# List recordings
curl http://localhost:8000/api/v1/recordings \
  -H "Authorization: Bearer YOUR_TOKEN"

# Stream recording
curl http://localhost:8000/api/v1/recordings/{recording_id}/stream \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Dashboard & Analytics
```bash
# Dashboard stats
curl http://localhost:8000/api/v1/dashboard \
  -H "Authorization: Bearer YOUR_TOKEN"

# Analytics
curl http://localhost:8000/api/v1/analytics \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## Component Testing

### 1. Voice Pipeline (STT → LLM → TTS)
```bash
python -m pytest tests/integration/test_voice_pipeline.py -v
```

### 2. Deepgram STT
```bash
python -m pytest tests/integration/test_deepgram_connection.py -v
```

### 3. Groq LLM
```bash
python -m pytest tests/integration/test_day5_groq_integration.py -v
```

### 4. Cartesia TTS
```bash
python -m pytest tests/integration/test_tts_streaming.py -v
```

### 5. Dialer Engine
```bash
python -m pytest tests/unit/test_dialer_engine.py -v
python -m pytest tests/integration/test_dialer_integration.py -v
```

### 6. Recording & Transcripts (Day 10)
```bash
python -m pytest tests/unit/test_day10.py -v
```

---

## Manual Testing Checklist

### Server Startup
- [ ] Server starts without errors
- [ ] Health endpoint returns healthy
- [ ] Redis connection (if enabled)

### Authentication
- [ ] Login works with valid credentials
- [ ] Token is returned
- [ ] Protected endpoints reject without token

### Campaign Flow
- [ ] Create campaign
- [ ] Add contacts to campaign
- [ ] Start campaign
- [ ] Monitor dialer queue

### Call Flow
- [ ] Vonage webhook receives events
- [ ] WebSocket connects for voice
- [ ] STT transcribes audio
- [ ] LLM generates responses
- [ ] TTS synthesizes speech
- [ ] Call ends properly

### Recording & Transcript (Day 10)
- [ ] Recording buffer accumulates audio
- [ ] Recording uploads to Supabase Storage
- [ ] Transcript saves to database
- [ ] Transcript endpoint returns data

---

## Run Specific Test Modules

```bash
# Run single test file
python -m pytest tests/unit/test_day10.py -v

# Run tests matching pattern
python -m pytest -k "dialer" -v

# Run with coverage report
python -m pytest tests/unit/ --cov=app --cov-report=html

# Run only failed tests
python -m pytest --lf -v
```

---

## Environment Requirements

Required environment variables in `.env`:
```
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_KEY=eyJxxx
VONAGE_API_KEY=xxx
VONAGE_API_SECRET=xxx
VONAGE_APPLICATION_ID=xxx
VONAGE_PRIVATE_KEY_PATH=./private.key
GROQ_API_KEY=gsk_xxx
DEEPGRAM_API_KEY=xxx
CARTESIA_API_KEY=xxx
REDIS_URL=redis://localhost:6379 (optional)
```

---

## Troubleshooting

### Import Errors
```bash
# Verify imports work
python -c "from app.main import app; print('OK')"
```

### Provider Connection Issues
```bash
# Test individual providers
python -c "from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider; print('Deepgram OK')"
python -c "from app.infrastructure.llm.groq import GroqLLMProvider; print('Groq OK')"
python -c "from app.infrastructure.tts.cartesia import CartesiaTTSProvider; print('Cartesia OK')"
```

### Database Issues
```bash
# Test Supabase connection
python -c "from app.api.v1.dependencies import get_supabase; s = next(get_supabase()); print('Supabase OK')"
```
