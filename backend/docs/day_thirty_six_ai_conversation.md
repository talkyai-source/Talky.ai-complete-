# Day 36: AI Voice Conversation Integration

**Date:** January 20, 2026 (Evening Session)  
**Objective:** Connect FreeSWITCH to full AI voice pipeline for natural conversations

---

## Summary

With FreeSWITCH running on Windows and ESL connection established, this session focused on building the complete AI voice conversation system. We implemented:

1. ✅ TTS greeting playback via FreeSWITCH
2. ✅ Call origination with audio playback
3. ✅ AI conversation controller architecture
4. ✅ New `/ai-call` endpoint for full AI calls
5. ⏳ Record-then-process conversation flow (pending testing)

---

## Architecture: AI Voice Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                     FreeSWITCH (Windows)                     │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │ SIP Gateway │  │ Call Control │  │ Audio Playback      │ │
│  │ (3cx-pbx)   │  │ (ESL)        │  │ (uuid_broadcast)   │ │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬──────────┘ │
└─────────┼────────────────┼─────────────────────┼────────────┘
          │                │                     │
          ▼                ▼                     ▲
    ┌─────────┐     ┌────────────┐        ┌────────────┐
    │ 3CX PBX │     │ Python ESL │        │ WAV Files  │
    │192.168.1.6    │ Client     │        │ (TTS Audio)│
    └─────────┘     └─────┬──────┘        └─────┬──────┘
                          │                      │
                    ┌─────▼──────────────────────▼─────┐
                    │     AI Conversation Controller    │
                    │  ┌───────┐ ┌─────┐ ┌───────────┐ │
                    │  │  STT  │ │ LLM │ │    TTS    │ │
                    │  │Deepgram│ │Groq │ │ Deepgram │ │
                    │  └───────┘ └─────┘ └───────────┘ │
                    └──────────────────────────────────┘
```

---

## What Was Implemented

### 1. TTS Greeting Generation

Function to generate AI greeting as WAV file:

```python
async def _generate_greeting_file(call_id: str, text: str = None) -> Optional[str]:
    """Generate TTS greeting and save as WAV file."""
    # Get voice configuration
    config = get_global_config()
    voice_info = get_selected_voice_info()
    
    # Generate with Deepgram Aura voice
    tts_provider = DeepgramTTSProvider()
    await tts_provider.initialize({
        "voice_id": "aura-asteria-en",
        "sample_rate": 8000  # Telephony rate
    })
    
    audio_data = await tts_provider.synthesize_raw(
        text=greeting_text,
        voice_id=tts_voice_id,
        sample_rate=8000
    )
    
    # Save as WAV for FreeSWITCH playback
    with wave.open(wav_file, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(audio_data)
    
    return wav_file  # Local path for Windows FreeSWITCH
```

### 2. Call with Playback

Added `originate_with_playback` method to ESL client:

```python
async def originate_with_playback(
    self,
    destination: str,
    audio_file: str,
    gateway: str = "3cx-pbx",
    caller_id: str = "1001"
) -> Optional[str]:
    """Originate call and play audio file."""
    dial_string = f"sofia/gateway/{gateway}/{destination}"
    app_string = f"&playback({audio_file})"
    
    command = f"originate {dial_string} '{app_string}'"
    result = await self.api(command)
    # Parse UUID from +OK response...
```

### 3. AI Conversation Controller

Created `ai_conversation_controller.py` with turn-based conversation flow:

```python
class AIConversationController:
    """Controls AI conversations via FreeSWITCH."""
    
    async def _conversation_loop(self, call_uuid: str):
        """Main conversation loop."""
        while call_uuid in self._conversations:
            # 1. Record caller speech
            recording_file = await self._record_speech(call_uuid)
            
            # 2. Transcribe with STT
            transcript = await self._transcribe_audio(recording_file)
            
            # 3. Get AI response from LLM
            ai_response = await self._get_ai_response(state)
            
            # 4. Generate TTS and play
            await self._play_tts(call_uuid, ai_response)
```

**System Prompt:**
```
You are a friendly, helpful AI assistant named Aria from Talky AI.
You are having a phone conversation and should keep your responses 
concise and natural. Be helpful, empathetic, and conversational.
Keep responses to 2-3 sentences unless the user asks for detailed information.
```

### 4. AI Call Endpoint

New endpoint for full AI conversation calls:

```python
@router.post("/ai-call")
async def make_ai_call(
    to_extension: str = Query(...),
    caller_id: str = Query(default="1001"),
    greeting: str = Query(default=None)
):
    """
    Make an outbound call with full AI conversation.
    
    Flow:
    1. Connect to the extension
    2. Play AI greeting
    3. Listen for caller speech
    4. Process with AI (STT → LLM → TTS)
    5. Continue conversation until caller hangs up
    """
```

---

## API Endpoints

### New Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/sip/freeswitch/ai-call` | POST | Start AI conversation call |
| `/api/v1/sip/freeswitch/ai-conversation/{uuid}` | GET | Get conversation status |
| `/api/v1/sip/freeswitch/ai-conversation/{uuid}/end` | POST | End conversation |

### Updated Endpoints

| Endpoint | Change |
|----------|--------|
| `/api/v1/sip/freeswitch/call` | Added `with_greeting` parameter |
| `/api/v1/sip/freeswitch/start` | Added `use_docker` parameter |

---

## Test Results

### Greeting Playback ✅
```bash
curl -X POST "http://localhost:8000/api/v1/sip/freeswitch/call?to_extension=1002&with_greeting=true"
```
- Phone rings
- User answers
- AI greeting plays: "Hello! This is Aria from Talky AI. How can I help you today?"

### AI Call ✅
```bash
curl -X POST "http://localhost:8000/api/v1/sip/freeswitch/ai-call?to_extension=1002"
```
- Response: `{"status":"calling","mode":"ai_conversation","message":"Starting AI conversation..."}`
- Greeting plays successfully
- Conversation loop initiated

---

## Files Created/Modified

| File | Status | Description |
|------|--------|-------------|
| `app/infrastructure/telephony/ai_conversation_controller.py` | NEW | AI conversation controller |
| `app/infrastructure/telephony/freeswitch_esl.py` | MODIFIED | Added `originate_with_playback` |
| `app/api/v1/endpoints/freeswitch_bridge.py` | MODIFIED | Added AI endpoints |

---

## Technical Notes

### Windows FreeSWITCH Limitations

The Windows build of FreeSWITCH lacks:
- `mod_audio_fork` - WebSocket audio streaming
- `mod_audio_stream` - Alternative streaming module

This necessitated a **record-then-process approach** instead of real-time streaming:
1. Use `uuid_record` to capture caller audio
2. Send recorded file to STT
3. Generate response and play back

### Audio Specifications

| Parameter | Value |
|-----------|-------|
| Sample Rate | 8000 Hz (telephony standard) |
| Channels | 1 (mono) |
| Bit Depth | 16-bit |
| Format | WAV (PCM) |

---

## Current Status

| Component | Status |
|-----------|--------|
| FreeSWITCH Windows Service | ✅ Running |
| ESL Connection | ✅ Connected |
| 3CX Gateway | ✅ Registered (REGED) |
| Call Origination | ✅ Working |
| TTS Greeting | ✅ Playing |
| AI Conversation Loop | ⏳ Testing |

---

## Next Steps

1. **Test Recording**: Verify `uuid_record` works on Windows FreeSWITCH
2. **STT Integration**: Test Deepgram transcription of recorded audio
3. **Full Loop Test**: Complete end-to-end AI conversation
4. **Silence Detection**: Fine-tune recording parameters
5. **Error Handling**: Add retry logic for STT/LLM failures

---

## Environment Variables

```env
# FreeSWITCH ESL
FREESWITCH_ESL_HOST=127.0.0.1
FREESWITCH_ESL_PORT=8021
FREESWITCH_ESL_PASSWORD=ClueCon

# AI Providers
DEEPGRAM_API_KEY=<your_key>
GROQ_API_KEY=<your_key>

# PBX
SIP_PBX_HOST=192.168.1.6
SIP_AI_EXTENSION=1001
```

---

## Commands Reference

```powershell
# Start FreeSWITCH service
Start-Service -Name "FreeSWITCH"

# Check gateway status
& "C:\Program Files\FreeSWITCH\fs_cli.exe" -x "sofia status gateway 3cx-pbx"

# Make test call
& "C:\Program Files\FreeSWITCH\fs_cli.exe" -x "originate sofia/gateway/3cx-pbx/1002 &echo"

# Check active calls
& "C:\Program Files\FreeSWITCH\fs_cli.exe" -x "show calls"
```

---

## Conclusion

The FreeSWITCH integration is now functional with:
- ✅ Native Windows installation bypassing Docker issues
- ✅ ESL connection from Python backend
- ✅ Call origination with TTS greeting
- ✅ AI conversation architecture in place

The foundation for AI voice conversations is complete. The next step is testing the full conversation loop with speech recognition and AI responses.
