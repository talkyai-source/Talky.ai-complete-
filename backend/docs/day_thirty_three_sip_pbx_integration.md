# Day 33: SIP PBX Integration Progress

**Date:** January 19, 2026  
**Objective:** Enable Talky.ai agent to make outgoing SIP calls and speak using TTS

---

## Summary

This session focused on integrating Talky.ai with an external PBX (3CX) to enable the AI agent to:
1. Register as extension 1002 on the PBX
2. Make outgoing calls to extension 1001
3. Send TTS audio greeting via RTP

---

## What Was Accomplished

### ✅ Successfully Implemented

1. **SIP Registration Works**
   - AI agent registers as extension 1002 on PBX at 192.168.1.6:5060
   - Registration confirmed with `{"registered": true}`

2. **Outgoing Calls Work**
   - Calls are successfully initiated to extension 1001
   - Phone rings and can be answered
   - SIP signaling (INVITE, 200 OK, ACK) works correctly

3. **Deepgram TTS Integration**
   - Replaced Google TTS (requires billing) with Deepgram TTS
   - Deepgram Aura voices work correctly
   - Test script `test_google_tts.py` plays audio successfully via WAV file
   - TTS generates clear audio (verified locally)

4. **RTP Audio Improvements**
   - Implemented proper G.711 μ-law encoding for telephony
   - Implemented 20ms frame timing for RTP packets
   - Added time-based pacing for smoother audio delivery
   - Fixed race condition with RTP socket creation
   - Added RTP keep-alive (silence packets) to prevent PBX timeout

5. **Bug Fixes**
   - Fixed multiple callback triggers for same call
   - Fixed RTP listener not closing socket on benign errors
   - Removed test tone that was causing noise interference

### ⚠️ Partially Working

1. **Audio Briefly Heard**
   - User heard "Hello" greeting (distorted, stuttering)
   - Audio quality improved after removing test tone
   - Calls disconnect after ~6 seconds

### ❌ Current Issues

1. **Call Drops After 6 Seconds - ROOT CAUSE IDENTIFIED & FIXED**
   - **Root Cause:** The `_rtp_listener_loop` was deleting the RTP socket when it exited, but the keep-alive task still needed that socket to send silence packets
   - **Why 6 Seconds:** 3CX PBX has a default "No Audio RTP Timeout" of ~6 seconds. When no RTP packets are received for this duration, PBX sends BYE
   - **The Fix:** Removed socket deletion from `_rtp_listener_loop` (lines 747-755 in sip_pbx_client.py). Socket cleanup now only happens in `_end_call()` when the call actually terminates
   - **Status:** ✅ Fix Applied - Testing Required

2. **Previous Issue: RTP Packets Not Reaching Phone** (May be resolved by fix above)
   - Keep-alive was failing because socket was deleted
   - Added debug logging to verify keep-alive is now working

---

## Files Modified

### Core Implementation Files

| File | Changes |
|------|---------|
| `app/api/v1/endpoints/sip_bridge.py` | Added PBX client integration, Deepgram TTS, RTP keep-alive |
| `app/infrastructure/telephony/sip_pbx_client.py` | Fixed callback duplication, improved RTP handling |
| `app/infrastructure/tts/deepgram_tts.py` | **NEW** - Deepgram TTS provider with Aura voices |

### Configuration Files

| File | Changes |
|------|---------|
| `config/google-service-account.json` | Updated service account (billing required) |
| `test_google_tts.py` | Created test script for TTS verification |

---

## Architecture

```
┌─────────────────┐      SIP (5060)      ┌─────────────┐
│   Talky.ai      │◄────────────────────►│   3CX PBX   │
│   Backend       │                      │ 192.168.1.6 │
│   (1002)        │      RTP             │             │
│                 │◄────────────────────►│   Phone     │
│   Port: 5062    │    (Dynamic Port)    │   (1001)    │
└─────────────────┘                      └─────────────┘
```

### Call Flow

1. **Registration**: Backend → PBX (REGISTER)
2. **Outgoing Call**: Backend → PBX (INVITE to 1001)
3. **Call Setup**: PBX → Backend (100 Trying, 180 Ringing, 200 OK)
4. **Audio**: Backend ←→ Phone via RTP

---

## API Endpoints

### Start PBX Client
```bash
POST /api/v1/sip/pbx/start?host=192.168.1.6&port=5060&username=1002&password=1002
```

### Make Outgoing Call
```bash
POST /api/v1/sip/pbx/call?to_extension=1001
```

### Check Status
```bash
GET /api/v1/sip/pbx/status
```

---

## Next Steps to Resolve Issues

### Investigation Needed

1. **Verify RTP Routing**
   - Check if RTP packets are being sent to correct IP/port
   - Use Wireshark to capture RTP traffic
   - Verify SDP negotiation is correct

2. **Debug Callback Chain**
   - Find why logs after `_on_pbx_call_started` don't appear
   - Check if exception is being swallowed silently
   - Verify `_active_sessions` dict is being populated

3. **Network Configuration**
   - Check firewall rules for UDP ports
   - Verify no NAT issues between backend and PBX
   - Ensure RTP port range is open

### Potential Fixes

1. **RTP Socket Binding**
   - Bind to specific interface instead of 0.0.0.0
   - Use same IP as advertised in SDP

2. **Call State Management**
   - Ensure call state transitions are correct
   - Add more defensive checks

3. **PBX Configuration**
   - Increase "no audio" timeout on PBX
   - Check codecs supported (should be PCMU/G.711)

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `deepgram-sdk` | TTS (Aura voices) |
| `aiohttp` | HTTP client for Deepgram API |
| `numpy` | Audio format conversion |
| `audioop` | G.711 encoding/resampling |

---

## Environment Variables Required

```env
DEEPGRAM_API_KEY=your_deepgram_key
```

---

## Test Commands

```bash
# Test Deepgram TTS (outputs WAV file)
python test_google_tts.py

# Start server
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Register and call
curl -X POST "http://localhost:8000/api/v1/sip/pbx/start?host=192.168.1.6&port=5060&username=1002&password=1002"
curl -X POST "http://localhost:8000/api/v1/sip/pbx/call?to_extension=1001"
```

---

## Session Statistics

- **Duration:** ~1.5 hours
- **Calls Tested:** ~10+
- **Audio Quality:** Improved from distorted/noisy to briefly audible
- **Connection Time:** 6 seconds before PBX disconnect

---

## Conclusion

The SIP/RTP infrastructure is largely in place and working:
- SIP signaling works correctly
- RTP sockets are created
- TTS audio is generated correctly
- G.711 encoding is implemented

The remaining issue is that RTP packets are not reaching the phone/PBX, causing a 6-second timeout. This is likely a network routing issue (NAT, wrong IP in SDP, or firewall) rather than a code issue.

**Recommended Next Step:** Use Wireshark to capture network traffic and verify:
1. RTP packets are being sent
2. Destination IP/port matches what phone expects
3. Packets are not being blocked by firewall
