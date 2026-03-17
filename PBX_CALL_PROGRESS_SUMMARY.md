# PBX Call System - Progress Summary

## ✅ What's Working

### 1. Call Initiation
- ✅ Backend API operational (port 8000)
- ✅ Asterisk 20.6.0 running in Docker
- ✅ C++ Voice Gateway running (port 18080)
- ✅ Telephony bridge connected to Asterisk adapter
- ✅ Outbound calls successfully initiated via ARI

### 2. Call Routing
- ✅ PJSIP endpoint configured for external PBX (192.168.1.6:5060)
- ✅ Calls routing to `PJSIP/1002@lan-pbx`
- ✅ Softphone receiving calls and ringing
- ✅ Call answered successfully

### 3. Channel Setup
- ✅ PJSIP channel created and enters Stasis app
- ✅ ExternalMedia (UnicastRTP) channel created
- ✅ Bridge created connecting PJSIP + UnicastRTP
- ✅ Both channels in "Up" state

### 4. Audio Path (Partial)
- ✅ C++ Gateway receiving RTP packets from Asterisk (6361 packets in)
- ✅ Audio flowing: Softphone → Asterisk → C++ Gateway
- ❌ NO audio flowing back: C++ Gateway → Asterisk → Softphone

---

## ❌ What's NOT Working

### Critical Issue: No AI Audio Output

**Symptoms:**
- Call is silent (no AI greeting, no responses)
- Gateway stats show: `packets_out: 0`, `tts_segments_started_total: 0`
- Gateway receiving audio but not sending any back

**Root Cause:**
The AI greeting code in `telephony_bridge.py` (`_on_new_call`) is attempting to send TTS audio, but it's not reaching the C++ gateway. The TelephonyMediaGateway is not properly wired to send audio through the adapter's `send_tts_audio()` method.

**Technical Details:**
1. `_on_new_call` callback IS triggered when PJSIP channel enters Stasis
2. Voice session created with TelephonyMediaGateway
3. Greeting TTS synthesis attempted
4. BUT: Audio not reaching C++ gateway (no HTTP POST to `/v1/sessions/{id}/tts/play`)

---

## 🔧 Architecture Overview

### Current Call Flow

```
API Request (curl)
    ↓
Backend (port 8000)
    ↓ ARI originate
Asterisk
    ↓ PJSIP/1002@lan-pbx
External PBX (192.168.1.6:5060)
    ↓ SIP INVITE
Softphone (rings & answers)
    ↓ RTP audio
Asterisk
    ↓ Bridge (PJSIP + UnicastRTP)
    ↓ RTP to 127.0.0.1:32000
C++ Voice Gateway
    ↓ HTTP POST audio chunks
Backend AI Pipeline
    ↓ STT → LLM → TTS
    ↓ ❌ BROKEN: TTS not reaching gateway
C++ Voice Gateway
    ↓ ❌ NO RTP sent back
Asterisk
    ↓ ❌ NO audio to softphone
Softphone (SILENT)
```

---

## 📊 Diagnostic Data

### C++ Gateway Stats
```json
{
    "active_sessions": 1,
    "packets_in": 6361,
    "packets_out": 0,              ← NO OUTPUT
    "bytes_in": 1017760,
    "bytes_out": 0,                ← NO OUTPUT
    "tts_segments_started_total": 0,  ← NO TTS
    "tts_frames_sent_total": 0,    ← NO TTS
    "jitter_buffer_overflow_drops": 6297  ← DROPPING INPUT
}
```

### Asterisk Channels
```
PJSIP/lan-pbx-00000002          Up    Stasis    (your softphone)
UnicastRTP/127.0.0.1:32000      Up    Stasis    (AI gateway)
Bridge: 8a280586... (2 channels, simple_bridge)
```

### Asterisk Version
- **Asterisk 20.6.0** (LTS, released 2022)
- Using PJSIP channel driver
- ARI enabled with ExternalMedia support

---

## 🎯 What Needs to be Fixed

### Issue 1: TTS Audio Not Reaching Gateway

**Problem:** The greeting code in `_on_new_call` synthesizes TTS but doesn't send it to the C++ gateway.

**Location:** `backend/app/api/v1/endpoints/telephony_bridge.py` lines 145-165

**Current Code:**
```python
async for audio_chunk in voice_session.tts_provider.stream_synthesize(...):
    await voice_session.media_gateway.send_audio(
        voice_session.call_id,
        audio_chunk.data,
    )
```

**Issue:** `TelephonyMediaGateway.send_audio()` needs to call `adapter.send_tts_audio()` which POSTs to the C++ gateway, but the connection isn't established.

**Solution Needed:**
1. Ensure TelephonyMediaGateway has reference to adapter and PBX call_id
2. Verify `send_tts_audio()` is calling gateway's `/v1/sessions/{id}/tts/play` endpoint
3. Check audio format conversion (TTS output → PCMU for gateway)

### Issue 2: Audio Format Mismatch

**Problem:** TTS provider outputs 24kHz audio, but gateway expects 8kHz PCMU.

**Check Needed:**
- Is audio being resampled 24kHz → 8kHz?
- Is audio being encoded to PCMU (G.711 μ-law)?
- Is audio being sent in correct packet size (160 bytes for 20ms @ 8kHz)?

### Issue 3: Gateway Session Mapping

**Problem:** Gateway session_id might not match what the backend is using.

**Check Needed:**
- Verify `_gateway_sessions` dict has correct mapping
- Confirm session_id format: `asterisk-{channel_id[:12]}-{port}`
- Ensure greeting code uses correct session_id

---

## 📝 Next Steps

### Immediate Actions

1. **Add detailed logging** to track TTS audio flow:
   - Log when TTS synthesis starts
   - Log when `send_audio()` is called
   - Log when `send_tts_audio()` is called
   - Log HTTP requests to C++ gateway

2. **Verify TelephonyMediaGateway setup**:
   - Check `on_call_started()` is called with correct adapter reference
   - Verify `send_audio()` method implementation
   - Confirm audio format conversion

3. **Test C++ gateway directly**:
   - Send test TTS audio via curl to `/v1/sessions/{id}/tts/play`
   - Verify gateway can play audio back to Asterisk
   - Check RTP packets are sent

4. **Fix audio pipeline**:
   - Ensure TTS audio reaches gateway
   - Verify audio format (8kHz PCMU)
   - Test end-to-end audio flow

---

## 🔍 Key Files to Review

1. `backend/app/api/v1/endpoints/telephony_bridge.py` - Greeting code
2. `backend/app/infrastructure/telephony/asterisk_adapter.py` - Adapter implementation
3. `backend/app/infrastructure/media_gateways/telephony_media_gateway.py` - Audio routing
4. `services/voice-gateway-cpp/` - C++ gateway implementation

---

## 📞 Test Command

```bash
# Make call to softphone
curl -X POST "http://localhost:8000/api/v1/sip/telephony/call?destination=1002&caller_id=TalkyAI"

# Check gateway stats
curl http://127.0.0.1:18080/stats

# Check Asterisk channels
docker exec talky-asterisk asterisk -rx "core show channels"

# Check bridge
docker exec talky-asterisk asterisk -rx "bridge show all"
```

---

**Status**: 🟡 PARTIALLY WORKING  
**Call Connects**: ✅ YES  
**Audio Input**: ✅ YES (softphone → gateway)  
**Audio Output**: ❌ NO (gateway → softphone)  
**AI Greeting**: ❌ NOT PLAYING  

**Next**: Fix TTS audio routing to C++ gateway
