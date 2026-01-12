# Google Cloud TTS Chirp 3 HD - Quick Setup Guide

## Installation

1. **Install the required Python package:**
```bash
cd backend
pip install google-cloud-texttospeech>=2.16.0
```

Or install all requirements:
```bash
pip install -r requirements.txt
```

2. **Verify the service account credentials are in place:**
```bash
ls config/google-service-account.json
```

Should show the file exists.

## Testing

### Test Voice List Endpoint
```bash
curl http://localhost:8000/api/v1/ai-options/voices | jq '.[] | select(.provider == "google")'
```

Expected output (8 Google voices):
```json
{
  "id": "en-US-Chirp3-HD-Orus",
  "name": "Orus",
  "language": "en",
  "description": "Deep, authoritative male voice...",
  "gender": "male",
  "accent": "American",
  "accent_color": "#1e40af",
  "preview_text": "Hello, I'm calling from your voice assistant...",
  "provider": "google",
  "tags": ["authoritative", "deep", "professional"]
}
```

### Test Voice Preview
```bash
curl -X POST http://localhost:8000/api/v1/ai-options/voices/preview \
  -H "Content-Type: application/json" \
  -d '{
    "voice_id": "en-US-Chirp3-HD-Leda",
    "text": "Hello, this is a test of the Google Chirp 3 HD voice system."
  }' | jq '.latency_ms'
```

Expected latency: **200-500ms** (compared to Cartesia's ~90ms)

## Voice IDs Reference

Copy-paste ready voice IDs for testing:

**Male:**
- `en-US-Chirp3-HD-Orus` - Deep, authoritative
- `en-US-Chirp3-HD-Charon` - Mature, reassuring
- `en-US-Chirp3-HD-Fenrir` - Energetic, confident
- `en-US-Chirp3-HD-Puck` - Friendly, approachable

**Female:**
- `en-US-Chirp3-HD-Kore` - Warm, professional
- `en-US-Chirp3-HD-Aoede` - Clear, articulate
- `en-US-Chirp3-HD-Leda` - Soothing, empathetic
- `en-US-Chirp3-HD-Zephyr` - Youthful, vibrant

## Troubleshooting

### Error: "google-cloud-texttospeech not installed"
```bash
pip install google-cloud-texttospeech
```

### Error: "credentials not found"
Check that `backend/config/google-service-account.json` exists and contains valid JSON.

### Error: "permission denied"
Ensure the service account has the "Cloud Text-to-Speech User" role in Google Cloud Console.

## Frontend Usage

1. Navigate to: http://localhost:3000/ai-options
2. Scroll to **TTS Voice** section
3. You should see **18 voices** (10 Cartesia + 8 Google)
4. Click the **play button** on any Google voice to test

## Architecture

```
Frontend (AI Options Page)
    ↓ Click Play on Google Voice
    ↓ POST /api/v1/ai-options/voices/preview
Backend API Handler
    ↓ Detect provider = "google"
    ↓ Set GOOGLE_APPLICATION_CREDENTIALS
GoogleTTSStreamingProvider
    ↓ gRPC streaming
Google Cloud TTS API
    ↓ Audio chunks (Float32 PCM)
Backend → Frontend
    ↓ Base64 audio
Web Audio API → Speakers 🔊
```

## Next Steps

- ✅ Google voices work with play previews
- ✅ All 8 Chirp 3 HD voices available
- ✅ Same UI/UX as Cartesia voices
- ⏭️ Ready to be selected and used in voice pipeline
- ⏭️ Can be set as default voice for AI agents
- ⏭️ Works in Dummy Call testing

## Quick Start

**To use a Google voice in a dummy call:**

1. Go to AI Options
2. Click on **Leda** (or any Google voice)
3. Click **Save Configuration**
4. Start a **Dummy Call**
5. The AI will speak with the selected Google Chirp 3 HD voice! 🎉
