# Day 34: FreeSWITCH Integration - Architecture & Configuration

**Date:** January 20, 2026  
**Objective:** Replace Python SIP/RTP handling with proper FreeSWITCH PBX integration

---

## Summary

Following the VoIP architecture audit that identified the root cause of 6-second call drops (Python async handling RTP directly), this session focused on setting up FreeSWITCH as the proper media server infrastructure.

---

## Problem Statement

The previous Python SIP implementation had critical issues:
- **6-second call drops** - PBX sending BYE due to session timer issues
- **RTP timing problems** - Python async couldn't maintain precise 20ms packet timing
- **Scalability limitations** - Python handling RTP directly doesn't scale
- **AI voice path issues** - Audio could not be reliably injected into calls

---

## Architecture Decision

### Before (Problematic)
```
Caller ──SIP/RTP──► Python (sip_pbx_client.py) ──► AI
```

### After (Correct)
```
Caller ──SIP/RTP──► FreeSWITCH ──ESL──► Python (AI only)
                          │
                          └──TTS Audio──► Playback
```

**Key Insight:** Let FreeSWITCH handle SIP/RTP (it's designed for this), while Python focuses solely on AI processing (STT/LLM/TTS).

---

## What Was Implemented

### Phase 1: FreeSWITCH Configuration

Created complete Docker-based FreeSWITCH configuration in `backend/freeswitch_config/`:

| File | Purpose |
|------|---------|
| `vars.xml` | Global variables (PBX host, ports, ESL settings) |
| `sip_profiles/external.xml` | 3CX PBX gateway registration |
| `dialplan/public.xml` | Routes inbound calls to AI via WebSocket |
| `dialplan/default.xml` | Default context with echo test |
| `autoload_configs/event_socket.conf.xml` | ESL for Python control |
| `autoload_configs/modules.conf.xml` | Required modules including mod_audio_fork |

### Phase 2: Docker Compose Setup

Created `docker-compose-freeswitch.yml`:
- Uses `drachtio/drachtio-freeswitch-mrf:v1.10.1-full` image
- Configured: `network_mode: host` for direct PBX access
- Volume mounts for audio files and recordings
- Health check via `sofia status`

### Phase 3: Python ESL Client

Created `app/infrastructure/telephony/freeswitch_esl.py`:
- Async ESL connection management
- Event subscription and handling
- Call control (answer, hangup, transfer)
- Audio playback via `uuid_broadcast`
- Call origination via gateway
- Added `asyncio.Lock` for thread-safe socket operations

---

## Technical Challenges

### Docker Networking Issues

Initial attempts with Docker failed:
1. **Bridge network** - Container couldn't reach PBX on host subnet (192.168.1.6)
2. **Host network mode** - ESL port 8021 not accessible from Windows host
3. **WSL2 mirrored networking** - Enabled via `.wslconfig`, improved but still had issues

### ESL Race Condition

Identified and fixed race condition between API calls and event listener:
```python
# Added socket lock to prevent conflicts
self._socket_lock = asyncio.Lock()

async def api(self, command):
    async with self._socket_lock:
        await self._send_command(f"api {command}")
        response = await self._read_response()
```

---

## Files Created/Modified

| File | Status | Description |
|------|--------|-------------|
| `freeswitch_config/*` | NEW | Complete FreeSWITCH configuration |
| `docker-compose-freeswitch.yml` | NEW | Docker setup for FreeSWITCH |
| `app/infrastructure/telephony/freeswitch_esl.py` | NEW | ESL client |
| `app/infrastructure/telephony/freeswitch_audio_bridge.py` | NEW | Audio bridge |
| `app/api/v1/endpoints/freeswitch_bridge.py` | NEW | API endpoints |
| `app/api/v1/routes.py` | MODIFIED | Added freeswitch_bridge router |

---

## API Endpoints Created

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/sip/freeswitch/start` | POST | Connect to FreeSWITCH ESL |
| `/api/v1/sip/freeswitch/stop` | POST | Disconnect from ESL |
| `/api/v1/sip/freeswitch/status` | GET | Get registration status |
| `/api/v1/sip/freeswitch/call` | POST | Make outbound call |
| `/api/v1/sip/freeswitch/hangup/{uuid}` | POST | Hang up call |
| `/api/v1/sip/freeswitch/play/{uuid}` | POST | Play TTS or audio |
| `/ws/freeswitch-audio/{uuid}` | WS | Audio streaming |

---

## Next Steps

1. Resolve ESL socket accessibility from Windows
2. Test call origination via backend API
3. Integrate voice pipeline
