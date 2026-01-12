# Day 21-22: Stripe Billing & Google TTS Migration

**Date:** December 31, 2025  
**Focus:** Stripe subscription billing + Google TTS migration

---

# Part 1: Stripe Billing Integration

## Overview

This document details the implementation of Stripe billing integration for the Talky.ai voice agent platform. The integration enables subscription management, payment processing, and usage tracking for metered billing.

---

## Changes Made

### Database Schema

#### New Tables Created

| Table | Purpose |
|-------|---------|
| `subscriptions` | Tracks Stripe subscription state with period dates, status, and metadata |
| `invoices` | Stores invoice history from Stripe webhooks |
| `usage_records` | Tracks usage for metered billing (minutes, API calls) |

#### Existing Tables Modified

| Table | Columns Added |
|-------|---------------|
| `plans` | `stripe_price_id`, `stripe_product_id`, `billing_period` |
| `tenants` | `stripe_customer_id`, `stripe_subscription_id`, `subscription_status` |

**Migration File:** `backend/database/migrations/add_stripe_billing.sql`

---

### New API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/billing/create-checkout-session` | POST | Creates Stripe Checkout session |
| `/api/v1/billing/webhooks` | POST | Handles Stripe webhook events |
| `/api/v1/billing/subscription` | GET | Gets current subscription status |
| `/api/v1/billing/portal` | POST | Creates Customer Portal session |
| `/api/v1/billing/cancel` | POST | Cancels subscription |
| `/api/v1/billing/usage` | GET | Gets usage summary |
| `/api/v1/billing/invoices` | GET | Lists invoices |
| `/api/v1/billing/config` | GET | Gets billing config/mode |

---

### Files Created

| File | Description |
|------|-------------|
| `app/domain/services/billing_service.py` | Core billing logic with Stripe API |
| `app/api/v1/endpoints/billing.py` | REST API endpoints for billing |
| `database/migrations/add_stripe_billing.sql` | Database migration script |

### Files Modified

| File | Changes |
|------|---------|
| `app/api/v1/routes.py` | Added billing router |
| `app/api/v1/endpoints/plans.py` | Added Stripe fields to response |
| `requirements.txt` | Added `stripe>=8.0.0` |

---

## Mock Mode

The billing system supports **mock mode** for development without Stripe credentials:

- Automatically enabled when `STRIPE_SECRET_KEY` is not set
- Can be forced with `STRIPE_MOCK_MODE=true`
- Returns mock checkout URLs and session IDs
- All database operations still work normally

---

## Stripe Environment Variables

```bash
# Required for production
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Optional
STRIPE_MOCK_MODE=false  # Set to true to force mock mode
```

---

## Next Steps for Billing

1. **Run Migration:** Execute `add_stripe_billing.sql` on Supabase
2. **Create Stripe Products:** Set up products and prices in Stripe Dashboard
3. **Update Plan IDs:** Replace placeholder price IDs with real ones
4. **Configure Webhook:** Add webhook endpoint URL in Stripe Dashboard

---

## Webhook Events Handled

| Event | Handler |
|-------|---------|
| `checkout.session.completed` | Activates subscription, updates tenant |
| `customer.subscription.created` | Syncs subscription to database |
| `customer.subscription.updated` | Updates subscription status |
| `customer.subscription.deleted` | Marks subscription as canceled |
| `invoice.paid` | Stores invoice record |
| `invoice.payment_failed` | Marks subscription as past_due |

---

## Billing Architecture

```
┌─────────────────┐    Checkout     ┌──────────────────┐
│    Frontend     │───────────────►│  billing.py      │
│  (React/Next)   │                │  (API Endpoints) │
└─────────────────┘                └────────┬─────────┘
        ▲                                   │
        │ Redirect to                       ▼
        │ Stripe Checkout       ┌──────────────────────┐
        │                       │ billing_service.py   │
        │                       │ (Business Logic)     │
        │                       └────────┬─────────────┘
        │                                │
        │                                ▼
┌───────┴─────────┐             ┌──────────────────┐
│ Stripe Checkout │◄───────────│   Stripe API     │
│     Page        │             └────────┬─────────┘
└─────────────────┘                      │
                                         │ Webhooks
                                         ▼
                               ┌──────────────────┐
                               │ /billing/webhooks│
                               └────────┬─────────┘
                                        │
                                        ▼
                               ┌──────────────────┐
                               │    Supabase DB   │
                               │ (subscriptions,  │
                               │  invoices, etc)  │
                               └──────────────────┘
```

---

---

# Part 2: Google TTS Migration - Cartesia Disabled

## Overview

This section details the complete migration from Cartesia Text-to-Speech to Google Chirp3-HD TTS across the entire Talky.ai voice agent application. The migration was performed to resolve persistent audio quality issues with Cartesia (static "zzzzz" noise during playback).

---

## Problem Statement

### Initial Issue: Cartesia Voice Audio Quality
- **Symptoms:** Voice playback had static noise ("zzzzz" sound like radio tuning)
- **Root Cause Analysis:**
  1. Sample rate mismatch between TTS output and frontend playback
  2. Encoding issues (pcm_f32le vs pcm_s16le interpretation)
  3. Inconsistent default configurations across the pipeline

### Resolution Journey
1. First attempted to fix sample rate mismatches (16kHz → 24kHz)
2. Changed Cartesia encoding from `pcm_f32le` to `pcm_s16le` with proper conversion
3. **Final Decision:** Disable Cartesia entirely and use Google Chirp3-HD exclusively

---

## Migration Summary

### Decision Rationale
- Google Chirp3-HD was already integrated and working correctly
- Google TTS provides consistent Int16 → Float32 conversion
- Eliminates dependency on Cartesia API key
- Simplifies the TTS pipeline

---

## Files Modified for TTS

### Backend Changes

#### 1. `backend/app/domain/models/ai_config.py`
**Changes:**
- Default TTS provider changed from `CARTESIA` to `GOOGLE`
- Default TTS model changed to `GoogleTTSModel.CHIRP3_HD.value`
- Default voice changed to `en-US-Chirp3-HD-Leda`
- Sample rate: 24000 Hz (Chirp3-HD optimal)

```python
# Before
tts_provider: TTSProvider = TTSProvider.CARTESIA
tts_model: str = CartesiaModel.SONIC_3.value
tts_voice_id: str = "f786b574-daa5-4673-aa0c-cbe3e8534c02"  # Katie

# After
tts_provider: TTSProvider = TTSProvider.GOOGLE
tts_model: str = GoogleTTSModel.CHIRP3_HD.value  # "Chirp3-HD"
tts_voice_id: str = "en-US-Chirp3-HD-Leda"  # Leda
```

---

#### 2. `backend/app/api/v1/endpoints/ask_ai_ws.py`
**Changes:**
- Replaced `CartesiaTTSProvider` import with `GoogleTTSStreamingProvider`
- Updated ASK_AI_CONFIG to use Google voice settings
- Updated `create_ask_ai_pipeline()` function
- Updated `send_ask_ai_greeting()` function

```python
# Configuration
ASK_AI_CONFIG = {
    "voice_id": "en-US-Chirp3-HD-Leda",
    "sample_rate": 24000,
    "model_id": "Chirp3-HD",
    "llm_model": "llama-3.3-70b-versatile",
    "llm_temperature": 0.6,
    "llm_max_tokens": 150
}
```

---

#### 3. `backend/app/api/v1/endpoints/ai_options_ws.py`
**Changes:**
- Commented out `CartesiaTTSProvider` import
- Removed Cartesia provider selection logic
- Always initializes `GoogleTTSStreamingProvider`
- Added voice_id format conversion for non-Google format voices

```python
# Always use Google TTS Streaming (Cartesia disabled)
tts_provider = GoogleTTSStreamingProvider()
await tts_provider.initialize({
    "voice_id": tts_voice_id,
    "sample_rate": 24000
})
```

---

#### 4. `backend/app/api/v1/endpoints/ai_options.py`
**Changes:**
- Replaced `CartesiaTTSProvider` import with `GoogleTTSStreamingProvider`
- Changed `list_providers()` to only return Google TTS
- Updated `list_voices()` to return only Google Chirp3-HD voices
- Updated `preview_voice()` to always use Google TTS
- Updated `test_tts()` to use Google TTS
- Updated `run_benchmark()` to use Google TTS
- Updated `save_config()` to validate against Google TTS models
- **Added auto-migration:** Old Cartesia configs are automatically converted to Google TTS

```python
# Auto-migration in get_config():
if cached_config.tts_provider == "cartesia" or cached_config.tts_model in ["sonic-3", "sonic-2"]:
    cached_config.tts_provider = "google"
    cached_config.tts_model = GoogleTTSModel.CHIRP3_HD.value
    cached_config.tts_voice_id = "en-US-Chirp3-HD-Leda"
    cached_config.tts_sample_rate = 24000
```

---

#### 5. `backend/app/domain/services/voice_pipeline_service.py`
**Changes:**
- Commented out `CartesiaTTSProvider` import
- Changed TTS provider type from `CartesiaTTSProvider` to generic `TTSProvider`
- Updated docstring to reflect Google TTS usage

```python
from app.domain.interfaces.tts_provider import TTSProvider  # Generic type

def __init__(
    self,
    stt_provider: STTProvider,
    llm_provider: GroqLLMProvider,
    tts_provider: TTSProvider,  # Generic TTS provider (Google, etc.)
    media_gateway: MediaGateway
):
```

---

### Frontend Changes

#### 6. `frontend/src/lib/ai-options-api.ts`
**Changes:**
- Updated DEFAULT_CONFIG to use Google TTS settings

```typescript
export const DEFAULT_CONFIG: AIProviderConfig = {
    // ... other settings
    tts_provider: "google",
    tts_model: "Chirp3-HD",
    tts_voice_id: "en-US-Chirp3-HD-Leda",
    tts_sample_rate: 24000,
};
```

---

#### 7. `frontend/src/app/ai-options/page.tsx`
**Changes:**
- Changed `ttsProvider` state type to `"google"` only
- Removed Cartesia provider button from UI
- Updated voice cards grid to filter only Google voices
- Voice selection now always uses Google provider
- Added "Cartesia disabled" notice in UI

```tsx
// TTS Provider filter state - Google only (Cartesia disabled)
const [ttsProvider, setTtsProvider] = useState<"google">("google");

// Voice selection always uses Google
onClick={() => setConfig({
    ...config,
    tts_voice_id: voice.id,
    tts_provider: 'google',
    tts_sample_rate: 24000
})}
```

---

## Available Google Chirp3-HD Voices

| Voice ID | Name | Gender | Description |
|----------|------|--------|-------------|
| en-US-Chirp3-HD-Leda | Leda | Female | Professional, clear (default) |
| en-US-Chirp3-HD-Aoede | Aoede | Female | Warm, friendly |
| en-US-Chirp3-HD-Kore | Kore | Female | Expressive |
| en-US-Chirp3-HD-Zephyr | Zephyr | Female | Gentle |
| en-US-Chirp3-HD-Orus | Orus | Male | Confident |
| en-US-Chirp3-HD-Charon | Charon | Male | Deep, authoritative |
| en-US-Chirp3-HD-Fenrir | Fenrir | Male | Strong |
| en-US-Chirp3-HD-Puck | Puck | Male | Energetic |

---

## Technical Details

### Audio Pipeline Flow (After Migration)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AUDIO PIPELINE (Google TTS)                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  [Microphone @ 16kHz] ──> [Deepgram STT @ 16kHz] ──> [Groq LLM]     │
│       (correct for STT input)                                        │
│                                                                      │
│  [LLM Response] ──> [Google TTS @ 24kHz] ──pcm_f32le──> [WebSocket] │
│       (Chirp3-HD streaming)                                          │
│                                                                      │
│  [Frontend AudioContext @ 24kHz] ──receives Float32──> [Playback]   │
│       (matching sample rate)                                         │
│                                                                      │
│  Result: Clear, jitter-free audio playback ✅                        │
└─────────────────────────────────────────────────────────────────────┘
```

### Sample Rate Configuration

| Component | Sample Rate | Purpose |
|-----------|-------------|---------|
| Microphone Input | 16000 Hz | STT input (Deepgram Flux requirement) |
| Google TTS Output | 24000 Hz | Chirp3-HD optimal quality |
| Frontend AudioContext | 24000 Hz | Matches TTS output |

### Audio Encoding

| Component | Encoding | Bytes/Sample |
|-----------|----------|--------------|
| Google TTS Output (internal) | PCM Int16 (pcm_s16le) | 2 |
| Google TTS Output (to frontend) | Float32 (pcm_f32le) | 4 |
| Frontend AudioBuffer | Float32Array | 4 |

The Google TTS Streaming provider converts Int16 to Float32 before sending:
```python
int16_array = np.frombuffer(response.audio_content, dtype=np.int16)
float32_array = (int16_array.astype(np.float32) / 32768.0)
float32_data = float32_array.tobytes()
```

---

## Configuration Migration

### Automatic Migration
Old Cartesia configurations are automatically migrated to Google TTS when:
1. **Loading config** (`GET /config`): Cached configs with Cartesia settings are converted
2. **Saving config** (`POST /config`): Incoming Cartesia configs are converted before validation

### Migration Logic
```python
if config.tts_provider == "cartesia" or config.tts_model in ["sonic-3", "sonic-2"]:
    config.tts_provider = "google"
    config.tts_model = "Chirp3-HD"
    config.tts_voice_id = "en-US-Chirp3-HD-Leda"
    config.tts_sample_rate = 24000
```

---

## Cartesia Code Status

The following Cartesia-related code has been **commented out but preserved** for potential future re-enabling:

### Files with Commented Cartesia Code
1. `ask_ai_ws.py` - CartesiaTTSProvider import
2. `ai_options_ws.py` - CartesiaTTSProvider import and initialization
3. `ai_options.py` - CartesiaTTSProvider import
4. `voice_pipeline_service.py` - CartesiaTTSProvider import

### Files Still Containing Cartesia (Unused)
1. `backend/app/infrastructure/tts/cartesia.py` - Full implementation preserved
2. `backend/app/domain/models/ai_config.py` - CartesiaModel enum, CARTESIA_VOICES list
3. Various test files in `backend/tests/`

---

## TTS Environment Variables

### Required
- `DEEPGRAM_API_KEY` - For STT (Deepgram Flux)
- `GROQ_API_KEY` - For LLM (Groq)
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to Google service account JSON

### No Longer Required
- `CARTESIA_API_KEY` - Cartesia TTS disabled

---

## Testing

### Verify Google TTS is Working
1. Navigate to AI Options page (`/ai-options`)
2. Check that only Google voices are displayed
3. Select a voice and click "Preview"
4. Click "Start Dummy Call" - should hear Google TTS voice

### Verify Auto-Migration
1. If you had old Cartesia configs, they should auto-migrate on page load
2. Save config - should succeed without validation errors

---

## Rollback Instructions

To re-enable Cartesia TTS:
1. Uncomment CartesiaTTSProvider imports in all affected files
2. Restore Cartesia provider selection logic in ai_options_ws.py
3. Update DEFAULT_CONFIG to use Cartesia settings
4. Remove auto-migration logic from get_config() and save_config()
5. Add Cartesia voices back to list_voices() endpoint
6. Add Cartesia provider button back to frontend

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| TTS Provider | Cartesia | Google Chirp3-HD |
| Default Voice | Katie (Cartesia) | Leda (Google) |
| Sample Rate | 24000 Hz | 24000 Hz |
| Audio Quality | Static noise issues | Clear, jitter-free |
| Configuration | Manual | Auto-migrates old configs |

---


