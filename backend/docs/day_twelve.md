# Day 12: AI Options Page, REST API & Browser Voice Testing

## Overview

**Date:** Week 3, Day 12  
**Goal:** Build complete AI Options feature with REST API, frontend page, and browser-based dummy call for testing AI voice agents in real-time.

This document covers the AI Options API endpoints, frontend page implementation, STT provider integrations, and the full voice pipeline for browser testing.

---

## Table of Contents

1. [AI Options REST API](#1-ai-options-rest-api)
2. [Frontend AI Options Page](#2-frontend-ai-options-page)
3. [Dummy Call Architecture](#3-dummy-call-architecture)
4. [STT Provider Implementations](#4-stt-provider-implementations)
5. [Voice Pipeline Integration](#5-voice-pipeline-integration)
6. [Files Changed Summary](#6-files-changed-summary)

---

## 1. AI Options REST API

**File: `app/api/v1/endpoints/ai_options.py`**

REST endpoints for managing AI agent configuration:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/ai-options` | GET | Get current AI settings (voice, model, prompts) |
| `/api/v1/ai-options` | PUT | Update AI configuration |
| `/api/v1/ai-options/voices` | GET | List available TTS voices |
| `/api/v1/ai-options/models` | GET | List available LLM models |

---

## 2. Frontend AI Options Page

**File: `frontend/src/app/ai-options/page.tsx`**

Complete React page with voice configuration and dummy call testing:

- **Voice Selection**: Choose from available TTS voices with preview
- **Model Configuration**: Select LLM model and adjust temperature
- **System Prompt Editor**: Customize agent personality and instructions
- **Dummy Call Panel**: Real-time voice testing with microphone capture
- **Call Statistics**: Display latency metrics and turn detection status

---

## 3. Dummy Call Architecture

### 3.1 Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DUMMY CALL VOICE PIPELINE                           │
├─────────────────────────────────────────────────────────────────────────────┤
│   Browser Mic ──► Deepgram Flux STT ──► Groq LLM ──► Cartesia TTS ──► Audio │
│      (16kHz)        (EndOfTurn)         (llama-3.3)    (sonic-3)    Playback│
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Components

| Component | File | Purpose |
|-----------|------|---------|
| WebSocket Endpoint | `ai_options_ws.py` | Browser connection, pipeline orchestration |
| Voice Pipeline | `voice_pipeline_service.py` | STT → LLM → TTS coordination |
| Browser Gateway | `browser_media_gateway.py` | Audio queue management |
| Deepgram Flux | `deepgram_flux.py` | Real-time STT with EndOfTurn |
| ElevenLabs STT | `elevenlabs.py` | Alternative VAD-based STT |

---

## 4. STT Provider Implementations

### 2.1 Deepgram Flux Provider

**File: `app/infrastructure/stt/deepgram_flux.py`**

Deepgram Flux provides ultra-low latency transcription with model-integrated EndOfTurn detection. Uses direct WebSocket connection to Deepgram v2 API.

**Key Features:**
- Model: `flux-general-en`
- EndOfTurn detection via TurnInfo events
- ~260ms latency for turn detection

```python
class DeepgramFluxSTTProvider(STTProvider):
    """
    Deepgram Flux STT provider with ultra-low latency and 
    intelligent turn detection for voice agents.
    
    Uses direct WebSocket connection to Deepgram v2 API.
    """
    
    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None
    ) -> AsyncIterator[TranscriptChunk]:
        """
        Stream audio to Deepgram Flux and receive real-time transcriptions.
        """
        # Build WebSocket URL with Flux parameters
        url = (
            f"wss://api.deepgram.com/v2/listen"
            f"?model={self._model}"
            f"&encoding={self._encoding}"
            f"&sample_rate={self._sample_rate}"
            f"&eot_threshold=0.7"
        )
        
        headers = {"Authorization": f"Token {self._api_key}"}
        
        async with websockets.connect(url, additional_headers=headers) as ws:
            # Send audio, receive TurnInfo events
            ...
```

**Flux State Machine Events:**
- `Update`: Partial transcript updates (~every 0.25s)
- `StartOfTurn`: User started speaking
- `EagerEndOfTurn`: Early end-of-turn signal (optional)
- `TurnResumed`: User continued speaking after EagerEndOfTurn
- `EndOfTurn`: User definitely finished speaking

### 2.2 ElevenLabs Scribe Provider

**File: `app/infrastructure/stt/elevenlabs.py`**

ElevenLabs Scribe v2 Realtime provides WebSocket-based transcription with VAD-based turn detection.

**Key Features:**
- Model: `scribe_v2_realtime`
- VAD-based commit strategy
- Partial and committed transcripts

```python
class ElevenLabsSTTProvider(STTProvider):
    """
    ElevenLabs Scribe v2 Realtime STT provider with ultra-low latency
    Uses WebSocket for real-time streaming transcription
    """
    
    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None
    ) -> AsyncIterator[TranscriptChunk]:
        """
        Stream audio to ElevenLabs Scribe v2.
        """
        url = (
            f"wss://api.elevenlabs.io/v1/speech-to-text/realtime"
            f"?model_id={self._model}"
            f"&language_code={language}"
            f"&audio_format=pcm_{self._sample_rate}"
            f"&commit_strategy=vad"
            f"&vad_silence_threshold_secs=1.0"
        )
        
        headers = {"xi-api-key": self._api_key}
        
        async with websockets.connect(url, additional_headers=headers) as ws:
            # Send audio, receive partial/committed transcripts
            ...
```

### 2.3 STT Provider Interface

**File: `app/domain/interfaces/stt_provider.py`**

Both providers implement the common `STTProvider` interface:

```python
class STTProvider(ABC):
    @abstractmethod
    async def initialize(self, config: dict) -> None:
        """Initialize provider with configuration"""
        pass
    
    @abstractmethod
    async def stream_transcribe(
        self,
        audio_stream: AsyncIterator[AudioChunk],
        language: str = "en",
        context: Optional[str] = None
    ) -> AsyncIterator[TranscriptChunk]:
        """Stream audio and receive transcripts"""
        pass
    
    @abstractmethod
    def detect_turn_end(self, transcript_chunk: TranscriptChunk) -> bool:
        """Detect if user finished speaking"""
        pass
    
    @abstractmethod
    async def cleanup(self) -> None:
        """Release resources"""
        pass
```

---

## 5. Voice Pipeline Integration

**File: `app/domain/services/voice_pipeline_service.py`**

The Voice Pipeline Service was updated to use the generic `STTProvider` interface instead of a concrete implementation.

**Before:**
```python
from app.infrastructure.stt.deepgram_flux import DeepgramFluxSTTProvider

class VoicePipelineService:
    def __init__(
        self,
        stt_provider: DeepgramFluxSTTProvider,  # ◄── Concrete type
        ...
    ):
```

**After:**
```python
from app.domain.interfaces.stt_provider import STTProvider

class VoicePipelineService:
    def __init__(
        self,
        stt_provider: STTProvider,  # ◄── Interface type
        ...
    ):
```

This allows swapping between Deepgram Flux and ElevenLabs without code changes.

---

## 4. Browser Media Gateway

**File: `app/infrastructure/telephony/browser_media_gateway.py`**

Handles audio streaming between browser WebSocket and voice pipeline:

```python
class BrowserMediaGateway(MediaGateway):
    """
    Media gateway for browser-based audio streaming.
    Receives audio from browser WebSocket and sends TTS audio back.
    """
    
    async def receive_audio(self) -> Optional[AudioChunk]:
        """Get next audio chunk from browser"""
        try:
            return await asyncio.wait_for(
                self._audio_queue.get(),
                timeout=0.1
            )
        except asyncio.TimeoutError:
            return None
    
    async def send_audio(self, audio: AudioChunk) -> None:
        """Queue audio for sending to browser"""
        await self._output_queue.put(audio)
    
    async def add_audio_chunk(self, data: bytes) -> None:
        """Called when audio received from browser WebSocket"""
        chunk = AudioChunk(
            data=data,
            sample_rate=self._sample_rate,
            channels=self._channels
        )
        await self._audio_queue.put(chunk)
```

---

## 5. Frontend Integration

**File: `frontend/src/app/ai-options/page.tsx`**

### 5.1 Voice-Only Interface

The dummy call UI was updated to be voice-only (no text input):

```tsx
// Dummy call is now pure voice interaction
// No text input field or send button
// User speaks directly and sees transcripts in chat

{dummyCall.messages.length === 0 && (
    <div className="text-center text-gray-500 mt-8">
        <Mic className="w-12 h-12 mx-auto mb-2 text-purple-400" />
        <p>Microphone is active - start speaking</p>
    </div>
)}
```

### 5.2 Microphone Capture

Audio is captured at 16kHz PCM and sent as binary chunks:

```tsx
const startMicrophone = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
            sampleRate: 16000,
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true
        }
    });
    
    const audioContext = new AudioContext({ sampleRate: 16000 });
    const processor = audioContext.createScriptProcessor(4096, 1, 1);
    
    processor.onaudioprocess = (event) => {
        // Convert Float32 to Int16 PCM
        const inputData = event.inputBuffer.getChannelData(0);
        const pcmData = new Int16Array(inputData.length);
        
        for (let i = 0; i < inputData.length; i++) {
            const s = Math.max(-1, Math.min(1, inputData[i]));
            pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        
        // Send binary audio to WebSocket
        wsRef.current.send(pcmData.buffer);
    };
};
```

### 5.3 Transcript Display

Transcripts are displayed as user messages when final:

```tsx
case "transcript":
    if (data.is_final && data.text && data.text.trim()) {
        setDummyCall(prev => ({
            ...prev,
            messages: [...prev.messages, {
                role: "user",
                content: data.text,
                timestamp: Date.now()
            }]
        }));
    }
    break;
```

---

## 6. WebSocket Endpoint

**File: `app/api/v1/endpoints/ai_options_ws.py`**

### 6.1 Voice Pipeline Creation

```python
async def create_voice_pipeline() -> tuple:
    """
    Create the SAME voice pipeline used by real Vonage calls.
    """
    # Initialize STT (Deepgram Flux with ultra-low latency EndOfTurn detection)
    stt_provider = DeepgramFluxSTTProvider()
    await stt_provider.initialize({
        "api_key": os.getenv("DEEPGRAM_API_KEY"),
        "model": "flux-general-en",
        "sample_rate": 16000,
        "encoding": "linear16"
    })
    
    # Initialize LLM (Groq)
    llm_provider = GroqLLMProvider()
    await llm_provider.initialize({
        "api_key": os.getenv("GROQ_API_KEY"),
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.3,
        "max_tokens": 150
    })
    
    # Initialize TTS (Cartesia)
    tts_provider = CartesiaTTSProvider()
    await tts_provider.initialize({
        "api_key": os.getenv("CARTESIA_API_KEY"),
        "model_id": "sonic-3",
        "sample_rate": 16000
    })
    
    # Initialize Browser Media Gateway
    browser_gateway = BrowserMediaGateway()
    await browser_gateway.initialize({
        "sample_rate": 16000,
        "channels": 1,
        "bit_depth": 16
    })
    
    return stt_provider, llm_provider, tts_provider, browser_gateway
```

### 6.2 WebSocket Handler

```python
@router.websocket("/voice")
async def voice_test_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for browser dummy call testing.
    Uses the SAME voice pipeline as real Vonage calls.
    """
    await websocket.accept()
    
    # Create voice pipeline
    stt_provider, llm_provider, tts_provider, browser_gateway = await create_voice_pipeline()
    
    # Create voice pipeline service
    pipeline_service = VoicePipelineService(
        stt_provider=stt_provider,
        llm_provider=llm_provider,
        tts_provider=tts_provider,
        media_gateway=browser_gateway
    )
    
    # Handle messages
    async for message in websocket.iter_data():
        if isinstance(message, bytes):
            # Binary audio from browser
            await browser_gateway.add_audio_chunk(message)
        else:
            # JSON control messages
            data = json.loads(message)
            # Handle control messages...
```

---

## 7. Files Changed Summary

### 7.1 New Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `app/infrastructure/stt/elevenlabs.py` | ~210 | ElevenLabs Scribe v2 Realtime STT provider |

### 7.2 Files Modified

| File | Changes | Purpose |
|------|---------|---------|
| `app/infrastructure/stt/deepgram_flux.py` | Rewritten | Direct WebSocket, TurnInfo event handling |
| `app/domain/services/voice_pipeline_service.py` | ~10 lines | STTProvider interface |
| `app/api/v1/endpoints/ai_options.py` | Created | REST API for AI configuration |
| `app/api/v1/endpoints/ai_options_ws.py` | ~20 lines | WebSocket for dummy call |
| `frontend/src/app/ai-options/page.tsx` | Created | Full AI Options page with dummy call UI |

### 7.3 Environment Variables Required

| Variable | Provider | Description |
|----------|----------|-------------|
| `DEEPGRAM_API_KEY` | Deepgram | Flux STT transcription |
| `ELEVENLABS_API_KEY` | ElevenLabs | Scribe v2 STT (alternative) |
| `GROQ_API_KEY` | Groq | LLM response generation |
| `CARTESIA_API_KEY` | Cartesia | TTS voice synthesis |

---

## Summary

Day 12 focused on building the browser-based dummy call feature for testing AI voice agents. Two STT providers were implemented:

1. **Deepgram Flux** - Uses direct WebSocket with TurnInfo events for EndOfTurn detection
2. **ElevenLabs Scribe v2** - Uses VAD-based turn detection

The voice pipeline service was updated to use the generic `STTProvider` interface, allowing easy switching between providers. The frontend was modified to provide a voice-only experience, capturing microphone audio at 16kHz and streaming it to the backend via WebSocket.

The dummy call feature now provides identical behavior to real phone calls, enabling thorough testing of the AI agent without making actual calls.

---

*Document Version: 1.0*  
*Last Updated: Week 3, Day 12 of Development Sprint*  
*Project Status: Dummy Call Feature Complete*
