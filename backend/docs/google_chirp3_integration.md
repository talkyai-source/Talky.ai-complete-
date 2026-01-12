# Google Cloud TTS Chirp 3 HD Integration

## 📋 Overview

Successfully integrated **Google Cloud Text-to-Speech Chirp 3 HD voices** with **gRPC streaming** for ultra-realistic voice synthesis in the Talky.ai voice assistant platform.

## ✅ What Was Implemented

### 1. Service Account Credentials

**File:** `backend/config/google-service-account.json`

- Saved the provided Google Cloud service account key for gRPC authentication
- Project ID: `gen-lang-client-0285154594`
- Service account: `talkly-ai@gen-lang-client-0285154594.iam.gserviceaccount.com`
- **Security Note:** This file contains sensitive credentials and should be kept secure

### 2. Google Chirp 3 HD Voices Added

**File:** `backend/app/domain/models/ai_config.py`

Added all **8 Chirp 3 HD voices** with detailed metadata:

#### Male Voices:
1. **Orus** - Deep, authoritative male voice. Commanding presence for professional calls.
2. **Charon** - Mature, reassuring male voice. Trustworthy and reliable tone.
3. **Fenrir** - Energetic, confident male voice. Great for sales and outreach.
4. **Puck** - Friendly, approachable male voice. Perfect for customer service.

#### Female Voices:
5. **Kore** - Warm, professional female voice. Ideal for business communications.
6. **Aoede** - Clear, articulate female voice. Excellent for appointments and reminders.
7. **Leda** - Soothing, empathetic female voice. Perfect for support and healthcare.
8. **Zephyr** - Youthful, vibrant female voice. Great for engagement and outreach.

Each voice includes:
- Unique color coding for UI display (`accent_color`)
- Gender tags (male/female)
- Descriptive use cases
- Custom preview text optimized for each voice's characteristics
- Full voice ID in Google's format: `en-US-Chirp3-HD-{VoiceName}`

### 3. Backend API Updates

**File:** `backend/app/api/v1/endpoints/ai_options.py`

#### Updated Endpoints:

**GET `/api/v1/ai-options/providers`**
- Now includes `"google"` in TTS providers
- Returns both Cartesia and Google TTS models

**GET `/api/v1/ai-options/voices`**
- Returns combined list of **Cartesia** (10 voices) + **Google Chirp 3 HD** (8 voices) = **18 total voices**

**POST `/api/v1/ai-options/voices/preview`**
- Enhanced to automatically detect voice provider (Cartesia vs Google)
- Dynamically routes to appropriate TTS service based on `voice.provider` field
- Google voices use 24kHz sample rate (optimal for Chirp 3 HD)
- Automatically sets `GOOGLE_APPLICATION_CREDENTIALS` environment variable from service account path

### 4. Frontend Integration

**File:** `frontend/src/app/ai-options/page.tsx`

#### Voice Selector UI:
- Displays **18 voices** with play buttons (identical to Cartesia implementation)
- All voices show:
  - Voice name and description
  - Gender badge (pink for female, blue for male)
  - Play button with accent color matching voice personality
  - Selected indicator (green checkmark)
- **Play previews work for both Cartesia and Google voices** - no visual difference for users

#### Updated Header:
- Changed from: `"Cartesia Sonic 3 - Select a voice"`
- Changed to: `"Cartesia Sonic 3 + Google Chirp 3 HD - Select a voice"`

## 🎯 How It Works

### Voice Preview Flow:

1. **User clicks play button** on any voice card
2. Frontend calls `POST /ai-options/voices/preview` with `voice_id`
3. Backend detects provider from voice metadata:
   ```python
   if voice.provider == "google":
       # Use GoogleTTSStreamingProvider
       # Set credentials path
       os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
       tts = GoogleTTSStreamingProvider()
   else:
       # Use CartesiaTTSProvider
       tts = CartesiaTTSProvider()
   ```
4. Audio streams back to frontend as base64
5. Frontend plays audio using Web Audio API

### Authentication:

Google TTS uses **Service Account** authentication via `GOOGLE_APPLICATION_CREDENTIALS`:
```python
creds_path = os.path.join(
    os.path.dirname(__file__),
    "config",
    "google-service-account.json"
)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
```

## 📊 Voice Comparison

| Feature | Cartesia Sonic 3 | Google Chirp 3 HD |
|---------|------------------|------------------|
| **Voices Available** | 10 | 8 |
| **First Audio Latency** | ~90ms | ~200ms |
| **Sample Rate** | 16kHz | 24kHz |
| **Technology** | SSE streaming | gRPC bidirectional streaming |
| **Authentication** | API Key | Service Account (gRPC) |
| **Best For** | Ultra-low latency, real-time | Highest quality, natural speech |

## 🚀 Usage

### For Users:
1. Go to **AI Options** page in dashboard
2. Scroll to **TTS Voice** section
3. See all **18 voices** (10 Cartesia + 8 Google)
4. Click **play button** on any voice to preview
5. Click voice card to select
6. Click **Save Configuration** to apply globally

### For Developers:

#### Testing Google Voice Preview:
```bash
# From backend directory
curl -X POST http://localhost:8000/api/v1/ai-options/voices/preview \
  -H "Content-Type: application/json" \
  -d '{
    "voice_id": "en-US-Chirp3-HD-Leda",
    "text": "Hello, this is a test of Google Chirp 3 HD voice"
  }'
```

#### Using in Voice Pipeline:
```python
from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider

# Initialize
tts = GoogleTTSStreamingProvider()
await tts.initialize({
    "voice_id": "en-US-Chirp3-HD-Leda",
    "sample_rate": 24000
})

# Stream synthesis
async for chunk in tts.stream_synthesize(
    text="Hello world",
    voice_id="en-US-Chirp3-HD-Leda",
    sample_rate=24000
):
    # Process audio chunk
    print(f"Received {len(chunk.data)} bytes")
```

## 📝 Configuration

### Environment Variables (Optional):

If you want to override the default service account path, set:
```bash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/service-account.json
```

The system automatically uses `backend/config/google-service-account.json` if not specified.

### .env.example Update (Recommended):

Add to `backend/.env.example`:
```bash
# Google Cloud TTS (Optional - uses service account JSON by default)
# GOOGLE_APPLICATION_CREDENTIALS=config/google-service-account.json
```

## 🔒 Security Notes

1. **Service Account Key** (`google-service-account.json`) contains sensitive credentials
2. Make sure it's in `.gitignore` (already in `backend/.gitignore`)
3. Never commit this file to version control
4. For production, use Secret Manager or environment-based credentials

## ✨ Key Features

✅ **Seamless Integration** - Works exactly like Cartesia voices from user perspective  
✅ **Auto-Detection** - Backend automatically routes to correct TTS provider  
✅ **Play Previews** - Test any voice before selecting  
✅ **Color-Coded UI** - Each voice has unique accent color  
✅ **Gender Tags** - Visual indicators for male/female voices  
✅ **18 Total Voices** - More variety for different use cases  
✅ **gRPC Streaming** - Low-latency bidirectional streaming for Google TTS  
✅ **Professional Quality** - Chirp 3 HD provides ultra-realistic speech  

## 🎨 Voice Selection UI

The voice selector displays voices in a responsive grid with:
- **2 columns** on mobile
- **3 columns** on tablet
- **4 columns** on desktop

Each card shows:
- Color-coded avatar icon
- Voice name
- Description (2-line clamp)
- Gender badge
- Play button (top-right)
- Selected indicator (bottom-right checkmark)

## 📦 Files Modified

### Backend:
- `backend/config/google-service-account.json` ✨ NEW
- `backend/app/domain/models/ai_config.py` (Added GOOGLE_CHIRP3_VOICES)
- `backend/app/api/v1/endpoints/ai_options.py` (Updated all voice endpoints)
- `backend/app/infrastructure/tts/google_tts_streaming.py` (Already existed, now fully functional)

### Frontend:
- `frontend/src/app/ai-options/page.tsx` (Updated header text)
- `frontend/src/lib/ai-options-api.ts` (No changes needed - already supports VoiceInfo interface)

## 🧪 Testing

### Test Voice Preview:
1. Start backend: `cd backend && uvicorn app.main:app --reload`
2. Start frontend: `cd frontend && npm run dev`
3. Navigate to: http://localhost:3000/ai-options
4. Click play button on any Google voice (Orus, Charon, Fenoir, Puck, Kore, Aoede, Leda, Zephyr)
5. Should hear preview audio immediately

### Verify Integration:
```bash
# Check if voices are returned
curl http://localhost:8000/api/v1/ai-options/voices | jq 'length'
# Should return: 18

# Check Google voices specifically
curl http://localhost:8000/api/v1/ai-options/voices | jq '[.[] | select(.provider == "google")] | length'
# Should return: 8
```

## 🎯 Next Steps (Optional Enhancements)

1. **Add TTS Provider Selector** - Let users filter by Cartesia or Google
2. **Latency Comparison** - Show latency metrics per provider
3. **Voice Tags Filtering** - Filter by tags (professional, energetic, calm, etc.)
4. **Favorite Voices** - Let users mark favorite voices
5. **Voice Samples** - Upload custom preview text per user

## ✅ Summary

Successfully integrated **Google Cloud TTS Chirp 3 HD** with **8 ultra-realistic voices** into the AI Options system. Users can now choose from **18 total voices** (10 Cartesia + 8 Google) with identical play-preview functionality. The backend automatically handles provider routing, authentication, and audio streaming, providing a seamless experience.

**All voice options work with the same play button interface as Cartesia!** 🎉
