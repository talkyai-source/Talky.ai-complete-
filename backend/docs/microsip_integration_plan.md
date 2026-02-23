# MicroSIP Integration Plan

## Goal
Connect MicroSIP softphone to Talky.ai voice agent for local testing without external telephony costs.

---

## Current Architecture

```
┌─────────────┐     WebSocket      ┌────────────────┐
│   Browser   │◄──────────────────►│  FastAPI WS    │
│  (Dummy)    │    (PCM audio)     │  /ws/voice/    │
└─────────────┘                    └───────┬────────┘
                                           │
                                           ▼
                             ┌───────────────────────┐
                             │ VoicePipelineService  │
                             │  STT → LLM → TTS      │
                             └───────────────────────┘
```

MicroSIP uses **SIP + RTP** (not WebSocket), so we need a bridge.

---

## Proposed Architecture

```
┌───────────┐   SIP/RTP    ┌─────────────────┐   WebSocket    ┌────────────────┐
│ MicroSIP  │◄────────────►│  SIP Gateway    │◄──────────────►│  FastAPI WS    │
│ Softphone │  (G.711/PCM) │  (FreeSWITCH    │   (PCM audio)  │  /ws/sip/      │
│           │              │   or Asterisk)  │                │                │
└───────────┘              └─────────────────┘                └───────┬────────┘
                                                                      │
                                                                      ▼
                                                        ┌───────────────────────┐
                                                        │ VoicePipelineService  │
                                                        │  STT → LLM → TTS      │
                                                        └───────────────────────┘
```

---

## Integration Approaches

### Option A: FreeSWITCH with mod_audio_fork (Recommended)

**Pros:** Industry standard, robust, well-documented  
**Cons:** Requires FreeSWITCH installation

| Component | Action |
|-----------|--------|
| FreeSWITCH | Install and configure as SIP server |
| mod_audio_fork | Stream audio to WebSocket |
| SIPMediaGateway | New gateway implementing MediaGateway interface |
| WebSocket endpoint | New `/ws/sip/{call_id}` endpoint |

### Option B: Asterisk with ExternalMedia

**Pros:** More common, easier to find resources  
**Cons:** More complex audio handling

### Option C: Python SIP Library (PJSIP)

**Pros:** All Python, no external server  
**Cons:** Complex, harder to maintain

> **Recommendation:** Option A (FreeSWITCH) for production, but for **quick local testing**, we can create a simpler bridge.

---

## Quick Local Testing Approach

For immediate testing with MicroSIP, use a **WebRTC-SIP Gateway** like:

1. **JsSIP + Browser Bridge** - MicroSIP → Local PBX → Browser WebRTC → Existing WS endpoint
2. **Simple Python SIP server** - Direct SIP handling with `aiosip` library

### Minimal Viable Integration

```
┌───────────┐              ┌─────────────────┐              ┌────────────────┐
│ MicroSIP  │──► SIP ────►│  Python SIP     │──► WS ─────►│  Existing WS   │
│ (Windows) │              │  Bridge Server  │              │  /ws/voice/    │
│           │◄── RTP ◄────│  (aiosip)       │◄── WS ◄─────│                │
└───────────┘              └─────────────────┘              └────────────────┘
```

---

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `app/infrastructure/telephony/sip_media_gateway.py` | NEW | SIP-to-WebSocket bridge |
| `app/infrastructure/telephony/factory.py` | MODIFY | Add "sip" gateway type |
| `app/api/v1/endpoints/sip_bridge.py` | NEW | SIP signaling endpoint |
| `config/sip_config.yaml` | NEW | SIP server configuration |

---

## MicroSIP Configuration

```ini
# MicroSIP → Account Settings
SIP Server: localhost:5060  (or your machine IP)
Username: agent001
Password: <any>
Transport: UDP

# Audio Codecs (in order of preference)
1. PCMU (G.711 μ-law) - 8kHz
2. PCMA (G.711 A-law) - 8kHz
```

---

## Implementation Steps

### Phase 1: Quick Bridge (For Testing)

1. [ ] Install `aiosip` Python library
2. [ ] Create simple SIP server that accepts calls
3. [ ] Forward RTP audio to existing WebSocket endpoint
4. [ ] Handle audio format conversion (G.711 → PCM 16kHz)

### Phase 2: Production Gateway

1. [ ] Install FreeSWITCH locally
2. [ ] Configure SIP dialplan for AI agent
3. [ ] Use mod_audio_fork for WebSocket streaming
4. [ ] Create `SIPMediaGateway` class
5. [ ] Add unit tests

---

## Questions for User

1. **Quick test or production-ready?** 
   - Quick: Python SIP bridge (2-3 hours)
   - Production: FreeSWITCH setup (4-6 hours)

2. **Do you have FreeSWITCH or Asterisk installed?**
   - If yes, we can configure audio_fork directly
   - If no, we need to install

3. **Audio quality preference?**
   - 8kHz (G.711) - lower quality, standard telephony
   - 16kHz (Linear16) - higher quality, needs conversion
