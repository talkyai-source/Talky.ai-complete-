# Starting Telephony Stack for Real PBX Calls

## Architecture

```
Your Softphone (e.g., Zoiper, Linphone)
    ↓ SIP REGISTER/INVITE
OpenSIPS (SIP Edge - Port 15060)
    ↓ SIP signaling
RTPEngine (Media Relay)
    ↓ SRTP encrypted audio
Asterisk (B2BUA - Port 5070)
    ↓ ARI + External Media
Backend AI (Port 8000)
    ↓ STT → LLM → TTS
    ↓ Audio back through same path
Your Softphone (hears AI voice)
```

## Steps to Start

### 1. Start Telephony Services

```bash
cd telephony/deploy/docker
docker-compose -f docker-compose.telephony.yml up -d
```

This starts:
- OpenSIPS (SIP proxy)
- Asterisk (B2BUA)
- RTPEngine (media relay)
- Kamailio (backup SIP proxy)
- FreeSWITCH (backup B2BUA)

### 2. Configure Your Softphone

**SIP Account Settings:**
- **Server**: `<YOUR_SERVER_IP>:15060`
- **Username**: `1001` (or any extension)
- **Password**: (configure in OpenSIPS auth)
- **Transport**: UDP
- **Codec**: G.711 μ-law (PCMU)

### 3. Start Backend Telephony Bridge

```bash
curl -X POST "http://localhost:8000/api/v1/sip/telephony/start?adapter_type=asterisk"
```

### 4. Make a Call

From your softphone, dial extension `750` (AI test extension)

Or make an outbound call via API:
```bash
curl -X POST "http://localhost:8000/api/v1/sip/telephony/call?destination=1002&caller_id=1001"
```

## What Happens

1. Your softphone registers to OpenSIPS
2. You dial extension 750
3. OpenSIPS routes to Asterisk
4. Asterisk creates ExternalMedia channel
5. Backend AI answers and starts conversation
6. You hear AI voice through your softphone
7. Natural conversation with < 1 second latency

## Difference from Browser Call

| Feature | Browser Call | PBX Call |
|---------|-------------|----------|
| **Protocol** | WebSocket | SIP/RTP |
| **Codec** | Raw PCM | G.711 μ-law |
| **Quality** | Variable | Toll quality |
| **Device** | Browser only | Any SIP phone |
| **Production** | Demo/test | Production-ready |
| **Encryption** | TLS | SRTP |
| **NAT Traversal** | N/A | Built-in |

