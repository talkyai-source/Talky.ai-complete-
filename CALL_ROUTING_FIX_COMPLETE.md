# Call Routing Fix - Natural Conversation System

## Problem Identified

When originating calls to extension 1002, you heard nothing because:

1. **Root Cause**: The `originate_call()` method was dialing directly to `PJSIP/1002@lan-pbx`, which bypassed the Asterisk dialplan entirely
2. **Impact**: Calls never entered the Stasis application, so no AI pipeline was attached
3. **Result**: No audio processing, no AI conversation - just silence

## Fix Applied

### 1. Fixed Call Origination (asterisk_adapter.py)

**Before**:
```python
# Extension 750 is the AI test extension - use Local channel
if destination == "750":
    endpoint = f"Local/{destination}@from-opensips"
else:
    # For other extensions, try direct PJSIP dial to external PBX
    # This will actually ring the softphone on the external PBX
    endpoint = f"PJSIP/{destination}@lan-pbx"
```

**After**:
```python
# Always use Local channel to route through dialplan
# This ensures the call enters Stasis application and gets AI attached
endpoint = f"Local/{destination}@from-opensips"
```

**Why**: Local channels route through the dialplan, ensuring every call goes through the Stasis application where the AI pipeline is attached.

### 2. Updated Dialplan (extensions.conf)

**Before**:
```
exten => 1002,1,NoOp(AI call to external PBX extension ${EXTEN})
 same => n,Answer()
 same => n,Stasis(talky_day5,inbound)
 same => n,Hangup()
```

**After**:
```
exten => 1002,1,NoOp(AI call to external PBX extension ${EXTEN})
 same => n,Stasis(talky_day5,inbound)
 same => n,Dial(PJSIP/${EXTEN}@lan-pbx,60)
 same => n,Hangup()
```

**Why**: 
- Removed premature `Answer()` - let Stasis handle call state
- Added `Dial()` after Stasis so the call can be transferred to the actual softphone if needed
- This allows the AI to handle the call first, then optionally transfer to the real extension

### 3. Reloaded Asterisk Dialplan

```bash
docker exec talky-asterisk asterisk -rx "dialplan reload"
```

## Current System State

### What's Working ✅
1. Call origination now routes through dialplan
2. Calls enter Stasis application (`talky_day5`)
3. Asterisk creates ExternalMedia channels and bridges
4. C++ Voice Gateway sessions are created
5. Audio callback URL is configured correctly
6. Backend telephony bridge is connected

### What Needs Investigation ⚠️

1. **C++ Gateway Session Timeouts**:
   - Gateway stats show 19 sessions started, but 0 active
   - Sessions are timing out immediately
   - 14,885 packets dropped (jitter buffer overflow)
   - Only 304 packets sent vs 15,077 received

2. **Possible Causes**:
   - Audio callback might not be reaching backend fast enough
   - Backend AI pipeline might not be consuming audio from input queue
   - TTS audio might not be flowing back to gateway properly
   - Session timeout configuration might be too aggressive

3. **Next Steps to Debug**:
   - Monitor backend logs during a call to see if `_on_new_call` is triggered
   - Check if STT provider is receiving audio chunks
   - Verify LLM is generating responses
   - Confirm TTS audio is being sent back to gateway
   - Check C++ gateway timeout settings

## How to Test

1. **Start Telephony Adapter**:
```bash
curl -X POST "http://localhost:8000/api/v1/sip/telephony/start?adapter_type=auto"
```

2. **Make a Call**:
```bash
curl -X POST "http://localhost:8000/api/v1/sip/telephony/call?destination=1002&caller_id=1001"
```

3. **Check Gateway Stats**:
```bash
curl -s http://localhost:18080/stats | python3 -m json.tool
```

4. **Check Asterisk Channels**:
```bash
docker exec talky-asterisk asterisk -rx "core show channels verbose"
```

## Architecture Flow

```
Backend API
    ↓ (originate_call)
Asterisk ARI
    ↓ (Local/1002@from-opensips)
Dialplan [from-opensips]
    ↓ (Stasis(talky_day5,inbound))
Stasis Application
    ↓ (creates ExternalMedia + Bridge)
C++ Voice Gateway
    ↓ (POST /api/v1/sip/telephony/audio/{session_id})
Backend Telephony Bridge
    ↓ (_on_new_call)
Voice Pipeline (STT → LLM → TTS)
    ↓ (send_audio)
Telephony Media Gateway
    ↓ (adapter.send_tts_audio)
C++ Voice Gateway
    ↓ (RTP)
Asterisk
    ↓ (PJSIP)
Softphone (Extension 1002)
```

## Files Modified

1. `backend/app/infrastructure/telephony/asterisk_adapter.py` - Fixed originate_call method
2. `telephony/asterisk/conf/extensions.conf` - Updated dialplan for extension 1002

## Configuration Verified

- `BACKEND_INTERNAL_URL=http://127.0.0.1:8000` ✅
- Audio callback endpoint: `/api/v1/sip/telephony/audio/{session_id}` ✅
- C++ Voice Gateway running on port 18080 ✅
- Asterisk ARI on port 8088 ✅
- Backend API on port 8000 ✅

## Summary

The critical routing issue has been fixed. Calls now properly route through the Stasis application. However, there appears to be a secondary issue with the C++ gateway sessions timing out immediately, which needs further investigation to achieve full bidirectional audio conversation.
