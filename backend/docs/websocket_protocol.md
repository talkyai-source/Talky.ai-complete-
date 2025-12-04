# WebSocket Protocol Specification
## Talky.ai Voice Streaming

### Protocol Choice: WebSocket

**Decision:** WebSocket over TCP  
**Alternative Considered:** gRPC over HTTP/2

#### Rationale:

1. **Vonage Native Support**: Vonage Voice API uses WebSocket for WebRTC audio streaming
   - Vonage establishes WebSocket connection to our server
   - Audio format: PCM 16-bit linear, 16kHz (`audio/l16;rate=16000`)
   - No need for protocol translation layer

2. **Bidirectional Streaming**: Full-duplex communication without blocking
   - Receive audio from user (ingress)
   - Send audio to user (egress)
   - Both happen simultaneously over single connection

3. **FastAPI Built-in Support**: Native WebSocket support in FastAPI
   - `websocket.receive_bytes()` for binary audio frames
   - `websocket.send_bytes()` for outbound audio
   - `websocket.receive_json()` / `websocket.send_json()` for control messages
   - No additional server configuration needed

4. **Browser Compatibility**: Future-proof for web-based calling
   - WebSocket universally supported in browsers
   - Works seamlessly with WebRTC
   - No additional client libraries needed

5. **Debugging Simplicity**: Standard WebSocket tools available
   - Chrome DevTools WebSocket inspector
   - Wireshark protocol analysis
   - Simple text-based control messages (JSON)

#### When gRPC Would Be Better:

- Microservices with internal service-to-service communication
- Need for strong typing and code generation across services
- High-throughput batch processing
- Multiple internal services requiring RPC

**Our Use Case:** Single FastAPI server handling external WebSocket connections from Vonage → **WebSocket is optimal**.

---

## Connection Flow

```
┌─────────┐                    ┌──────────────┐                    ┌─────────┐
│ Vonage  │                    │   FastAPI    │                    │   AI    │
│ Gateway │                    │   Backend    │                    │ Pipeline│
└────┬────┘                    └──────┬───────┘                    └────┬────┘
     │                                │                                 │
     │  1. Outbound Call Initiated    │                                 │
     ├───────────────────────────────▶│                                 │
     │                                │                                 │
     │  2. WebSocket Connection       │                                 │
     │     wss://backend/ws/voice/{id}│                                 │
     ├───────────────────────────────▶│                                 │
     │                                │                                 │
     │  3. Connection Accepted        │                                 │
     │◀───────────────────────────────┤                                 │
     │                                │                                 │
     │  4. SESSION_START (JSON)       │                                 │
     ├───────────────────────────────▶│                                 │
     │                                │                                 │
     │  5. Audio Stream (Inbound)     │  6. STT Processing              │
     ├───────────────────────────────▶├────────────────────────────────▶│
     │     Binary Frames (PCM 16kHz)  │                                 │
     │                                │  7. TRANSCRIPT_CHUNK            │
     │                                │◀────────────────────────────────┤
     │                                │                                 │
     │                                │  8. TURN_END                    │
     │                                │◀────────────────────────────────┤
     │                                │                                 │
     │                                │  9. LLM_START                   │
     │                                ├────────────────────────────────▶│
     │                                │                                 │
     │                                │  10. Response Tokens            │
     │                                │◀────────────────────────────────┤
     │                                │                                 │
     │                                │  11. LLM_END                    │
     │                                │◀────────────────────────────────┤
     │                                │                                 │
     │                                │  12. TTS_START                  │
     │                                ├────────────────────────────────▶│
     │                                │                                 │
     │  13. Audio Stream (Outbound)   │  14. Audio Chunks               │
     │◀───────────────────────────────┤◀────────────────────────────────┤
     │     Binary Frames (PCM 16kHz)  │                                 │
     │                                │                                 │
     │                                │  15. TTS_END                    │
     │                                │◀────────────────────────────────┤
     │                                │                                 │
     │  16. SESSION_END (JSON)        │                                 │
     ├───────────────────────────────▶│                                 │
     │                                │                                 │
     │  17. Connection Closed         │                                 │
     │◀───────────────────────────────┤                                 │
```

---

## Message Types

### Binary Messages (Audio Data)

**Type:** `AUDIO_CHUNK`  
**Frame Type:** Binary WebSocket frame  
**Direction:** Bidirectional (inbound from Vonage, outbound to Vonage)

**Format:**
- Raw PCM audio data (16-bit linear, 16kHz, mono)
- Chunk size: 1280-3200 bytes (80-200ms @ 16kHz)
- Sequence numbers for ordering
- Timestamps for latency measurement

**Vonage Specification:**
- Format: `audio/l16;rate=16000`
- Sample rate: 16000 Hz
- Bit depth: 16-bit
- Channels: 1 (mono)
- Encoding: Linear PCM

### Text Messages (Control & Events)

**Frame Type:** Text WebSocket frame (JSON)  
**Direction:** Bidirectional

**Message Types:**

1. **SESSION_START** - Initialize call session
2. **SESSION_END** - Terminate call session
3. **TRANSCRIPT_CHUNK** - Partial/final transcript from STT
4. **TURN_END** - User finished speaking
5. **LLM_START** - LLM processing started
6. **LLM_END** - LLM response complete
7. **TTS_START** - TTS synthesis started
8. **TTS_END** - TTS synthesis complete
9. **ERROR** - Error notification
10. **PING** - Heartbeat ping
11. **PONG** - Heartbeat pong

---

## Latency Budget

| Component | Target | Maximum | Notes |
|-----------|--------|---------|-------|
| Network (WebSocket) | 20-50ms | 100ms | TCP overhead + routing |
| STT (Deepgram Flux) | 200-260ms | 300ms | Turn detection latency |
| LLM (Groq) | 300-500ms | 800ms | First token generation |
| TTS (Cartesia) | 90-150ms | 200ms | Time to first audio |
| **Total Round-Trip** | **610-960ms** | **1400ms** | User stops → AI starts speaking |

**Goal:** Keep total latency under 1 second for natural conversation flow.

---

## Bandwidth Estimation

**Audio Streaming (16kHz, mono, linear16):**
- Bitrate: 16,000 Hz × 2 bytes = 32 KB/s = 256 kbps
- With WebSocket overhead: ~280 kbps per direction
- **Total for bidirectional:** ~560 kbps

**Control Messages:**
- Frequency: ~1-5 messages per second
- Average size: 200 bytes
- Bandwidth: ~1-5 KB/s = 8-40 kbps

**Total Bandwidth per Call:** ~600 kbps (0.6 Mbps)

**Concurrent Calls:**
- 100 concurrent calls: ~60 Mbps
- 1000 concurrent calls: ~600 Mbps

---

## Security

### Connection Security

- **Protocol:** `wss://` (WebSocket Secure)
- **TLS Version:** TLS 1.2 or higher
- **Certificate:** Valid SSL certificate required
- **SNI:** Server Name Indication included in Client Hello (Vonage requirement)

### Authentication

- **Call ID Validation:** Verify call_id matches active Vonage call
- **Campaign Authorization:** Verify campaign_id belongs to tenant
- **Token-Based Auth:** Optional JWT token in WebSocket connection headers

### Data Protection

- **Audio Encryption:** TLS encryption for all audio data
- **PII Handling:** Transcripts may contain PII, handle according to privacy policy
- **Logging:** Sanitize logs to remove sensitive information

---

## Error Handling

### Connection Errors

**Scenario:** WebSocket connection drops during call

**Handling:**
1. Vonage will attempt reconnection
2. Session state preserved in Redis
3. Resume from last known state
4. If reconnection fails after 30s, end call

### Component Errors

**Scenario:** STT/LLM/TTS service failure

**Handling:**
1. Send `ERROR` message to Vonage
2. If `recoverable=true`, retry with exponential backoff
3. If `recoverable=false`, send `SESSION_END` and close connection
4. Log error for monitoring

### Timeout Handling

**Scenario:** No activity for extended period

**Handling:**
1. Send `PING` every 30 seconds
2. Expect `PONG` within 5 seconds
3. If no `PONG`, assume connection dead
4. Close connection and clean up session

---

## Message Size Limits

| Message Type | Typical Size | Maximum Size |
|--------------|--------------|--------------|
| AUDIO_CHUNK | 1-3 KB | 64 KB |
| TRANSCRIPT_CHUNK | 100-500 bytes | 4 KB |
| CONTROL_MESSAGE | 50-200 bytes | 2 KB |
| ERROR_MESSAGE | 100-300 bytes | 2 KB |

**WebSocket Frame Limit:** 64 KB per frame (configurable)

---

## Implementation Notes

### FastAPI WebSocket Handler

```python
from fastapi import WebSocket, WebSocketDisconnect
from app.domain.models import parse_message, MessageType

@router.websocket("/ws/voice/{call_id}")
async def voice_stream(websocket: WebSocket, call_id: str):
    await websocket.accept()
    
    try:
        while True:
            # Receive message (auto-detects binary vs text)
            message = await websocket.receive()
            
            if "bytes" in message:
                # Binary audio frame
                audio_data = message["bytes"]
                # Process audio...
                
            elif "text" in message:
                # JSON control message
                data = json.loads(message["text"])
                msg = parse_message(data["type"], data)
                # Handle control message...
                
    except WebSocketDisconnect:
        # Clean up session
        pass
```

### Vonage NCCO Configuration

```json
[
  {
    "action": "connect",
    "endpoint": [
      {
        "type": "websocket",
        "uri": "wss://backend.talky.ai/ws/voice/{call_id}",
        "content-type": "audio/l16;rate=16000",
        "headers": {
          "Authorization": "Bearer {jwt_token}"
        }
      }
    ]
  }
]
```

---

## Testing Strategy

### Unit Tests

- Message schema validation (Pydantic models)
- Message parsing (parse_message function)
- Serialization/deserialization

### Integration Tests

- WebSocket connection establishment
- Binary frame transmission
- JSON message exchange
- Error handling flows

### Load Tests

- Concurrent connections (100, 1000 calls)
- Bandwidth utilization
- Latency under load
- Connection stability

---

## Monitoring & Metrics

### Key Metrics

- **Connection Metrics:**
  - Active connections count
  - Connection duration
  - Reconnection rate
  
- **Latency Metrics:**
  - WebSocket round-trip time
  - STT latency
  - LLM latency
  - TTS latency
  - Total round-trip latency
  
- **Error Metrics:**
  - Connection errors
  - Component failures
  - Timeout rate
  
- **Bandwidth Metrics:**
  - Bytes sent/received
  - Messages per second
  - Bandwidth per call

### Logging

- Structured JSON logs
- Correlation ID (call_id)
- Timestamp precision (milliseconds)
- Sanitized content (no PII in logs)

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-12-03 | Initial protocol specification |

---

## References

1. [FastAPI WebSocket Documentation](https://fastapi.tiangolo.com/advanced/websockets/)
2. [Vonage Voice API WebSocket](https://developer.vonage.com/en/voice/voice-api/guides/websockets)
3. [Pydantic v2 Documentation](https://docs.pydantic.dev/latest/)
4. [WebSocket Protocol RFC 6455](https://tools.ietf.org/html/rfc6455)
