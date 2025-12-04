# WebSocket Message Flow Diagrams

## 1. Successful Call Flow

```mermaid
sequenceDiagram
    participant V as Vonage
    participant WS as WebSocket Handler
    participant STT as STT Provider
    participant LLM as LLM Provider
    participant TTS as TTS Provider

    V->>WS: Connect (wss://backend/ws/voice/{call_id})
    WS->>V: Connection Accepted
    
    V->>WS: SESSION_START
    Note over WS: Create CallSession<br/>Initialize providers
    WS->>V: ACK
    
    rect rgb(200, 220, 250)
    Note over V,TTS: Conversation Turn 1
    
    V->>WS: AUDIO_CHUNK (inbound, seq=1)
    V->>WS: AUDIO_CHUNK (inbound, seq=2)
    V->>WS: AUDIO_CHUNK (inbound, seq=3)
    
    WS->>STT: Stream audio chunks
    STT->>WS: TRANSCRIPT_CHUNK (interim, "Hello")
    STT->>WS: TRANSCRIPT_CHUNK (interim, "Hello, how")
    STT->>WS: TRANSCRIPT_CHUNK (final, "Hello, how are you?")
    
    Note over STT: End-of-turn detected
    STT->>WS: TURN_END (turn_id=1)
    
    WS->>LLM: LLM_START (turn_id=1)
    Note over LLM: Process with context
    LLM->>WS: Token stream: "I'm"
    LLM->>WS: Token stream: " doing"
    LLM->>WS: Token stream: " great!"
    WS->>LLM: LLM_END (turn_id=1)
    
    WS->>TTS: TTS_START (turn_id=1, "I'm doing great!")
    TTS->>WS: AUDIO_CHUNK (outbound, seq=1)
    WS->>V: AUDIO_CHUNK (outbound, seq=1)
    TTS->>WS: AUDIO_CHUNK (outbound, seq=2)
    WS->>V: AUDIO_CHUNK (outbound, seq=2)
    TTS->>WS: AUDIO_CHUNK (outbound, seq=3)
    WS->>V: AUDIO_CHUNK (outbound, seq=3)
    WS->>TTS: TTS_END (turn_id=1)
    end
    
    rect rgb(200, 250, 220)
    Note over V,TTS: Conversation Turn 2
    
    V->>WS: AUDIO_CHUNK (inbound, seq=4)
    V->>WS: AUDIO_CHUNK (inbound, seq=5)
    
    WS->>STT: Stream audio chunks
    STT->>WS: TRANSCRIPT_CHUNK (final, "Goodbye")
    STT->>WS: TURN_END (turn_id=2)
    
    WS->>LLM: LLM_START (turn_id=2)
    LLM->>WS: Token stream: "Goodbye!"
    WS->>LLM: LLM_END (turn_id=2)
    
    WS->>TTS: TTS_START (turn_id=2, "Goodbye!")
    TTS->>WS: AUDIO_CHUNK (outbound, seq=4)
    WS->>V: AUDIO_CHUNK (outbound, seq=4)
    WS->>TTS: TTS_END (turn_id=2)
    end
    
    V->>WS: SESSION_END (reason=hangup)
    Note over WS: Persist to database<br/>Clean up session
    WS->>V: ACK
    WS->>V: Close connection
```

---

## 2. Error Handling Flow

```mermaid
sequenceDiagram
    participant V as Vonage
    participant WS as WebSocket Handler
    participant STT as STT Provider

    V->>WS: AUDIO_CHUNK (inbound)
    WS->>STT: Stream audio
    
    Note over STT: Connection Error
    STT--xWS: Error: Connection timeout
    
    WS->>V: ERROR (error_code=STT_TIMEOUT,<br/>recoverable=true)
    
    Note over WS: Retry with backoff
    WS->>STT: Reconnect
    
    alt Retry Successful
        STT->>WS: Connection restored
        WS->>V: INFO (stt_recovered)
        V->>WS: AUDIO_CHUNK (inbound)
        WS->>STT: Stream audio
        STT->>WS: TRANSCRIPT_CHUNK
        Note over V,STT: Continue normally
    else Retry Failed (3 attempts)
        STT--xWS: Connection failed
        WS->>V: ERROR (error_code=STT_FAILED,<br/>recoverable=false)
        WS->>V: SESSION_END (reason=error)
        WS->>V: Close connection
    end
```

---

## 3. Barge-In (Interruption) Flow

```mermaid
sequenceDiagram
    participant V as Vonage
    participant WS as WebSocket Handler
    participant STT as STT Provider
    participant TTS as TTS Provider

    Note over WS,TTS: AI is speaking
    WS->>TTS: TTS_START (turn_id=1)
    TTS->>WS: AUDIO_CHUNK (outbound, seq=1)
    WS->>V: AUDIO_CHUNK (outbound, seq=1)
    TTS->>WS: AUDIO_CHUNK (outbound, seq=2)
    WS->>V: AUDIO_CHUNK (outbound, seq=2)
    
    Note over V: User starts speaking<br/>(interruption)
    V->>WS: AUDIO_CHUNK (inbound, seq=10)
    
    Note over WS: Detect barge-in<br/>user_speaking=true<br/>ai_speaking=true
    WS->>TTS: Cancel synthesis
    WS->>V: TTS_END (interrupted=true)
    
    Note over WS: Switch to listening mode<br/>ai_speaking=false
    V->>WS: AUDIO_CHUNK (inbound, seq=11)
    V->>WS: AUDIO_CHUNK (inbound, seq=12)
    
    WS->>STT: Stream new audio
    STT->>WS: TRANSCRIPT_CHUNK (interim, "Wait")
    STT->>WS: TRANSCRIPT_CHUNK (final, "Wait, I have a question")
    STT->>WS: TURN_END (turn_id=2)
    
    Note over WS: Process new user input
```

---

## 4. Heartbeat (Keep-Alive) Flow

```mermaid
sequenceDiagram
    participant V as Vonage
    participant WS as WebSocket Handler

    Note over V,WS: Call in progress<br/>No activity for 30s
    
    WS->>V: PING (call_id)
    
    alt Connection Alive
        V->>WS: PONG (call_id)
        Note over WS: Connection healthy<br/>Reset timeout
    else No Response (5s timeout)
        Note over WS: Connection assumed dead
        WS->>WS: Clean up session
        WS->>WS: Log error
        Note over WS: Attempt graceful close
        WS->>V: Close connection
    end
    
    Note over V,WS: 30 seconds later
    WS->>V: PING (call_id)
    V->>WS: PONG (call_id)
```

---

## 5. Multi-Turn Conversation with Latency Tracking

```mermaid
sequenceDiagram
    participant V as Vonage
    participant WS as WebSocket Handler
    participant AI as AI Pipeline

    Note over V,AI: Turn 1
    
    rect rgb(255, 240, 240)
    Note over V,WS: Audio Ingress (t0)
    V->>WS: AUDIO_CHUNK
    end
    
    rect rgb(240, 255, 240)
    Note over WS,AI: STT Processing (t1)
    WS->>AI: Stream to STT
    AI->>WS: TRANSCRIPT_CHUNK
    AI->>WS: TURN_END
    Note over WS: Δt_stt = t1 - t0 = 260ms
    end
    
    rect rgb(240, 240, 255)
    Note over WS,AI: LLM Processing (t2)
    WS->>AI: LLM_START
    AI->>WS: Token stream
    WS->>AI: LLM_END
    Note over WS: Δt_llm = t2 - t1 = 450ms
    end
    
    rect rgb(255, 255, 240)
    Note over WS,AI: TTS Processing (t3)
    WS->>AI: TTS_START
    AI->>WS: AUDIO_CHUNK
    Note over WS: Δt_tts = t3 - t2 = 120ms
    end
    
    rect rgb(255, 240, 255)
    Note over WS,V: Audio Egress (t4)
    WS->>V: AUDIO_CHUNK
    Note over WS: Total latency = t4 - t0 = 830ms
    end
    
    Note over WS: Log latency metrics<br/>to monitoring system
```

---

## 6. Session Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Connecting: WebSocket connect
    
    Connecting --> Active: SESSION_START received
    Connecting --> Closed: Connection failed
    
    Active --> Listening: Waiting for user
    Active --> Processing: Audio received
    Active --> Speaking: TTS playing
    
    Listening --> Processing: AUDIO_CHUNK received
    Processing --> Listening: TURN_END + response sent
    Speaking --> Listening: TTS_END
    Speaking --> Processing: Barge-in detected
    
    Active --> Ending: SESSION_END received
    Active --> Ending: Error (unrecoverable)
    Active --> Ending: Timeout (no activity)
    
    Ending --> Closed: Cleanup complete
    Closed --> [*]
    
    note right of Active
        Session state in Redis
        Heartbeat every 30s
        Latency tracking
    end note
    
    note right of Ending
        Persist to database
        Release resources
        Close WebSocket
    end note
```

---

## 7. Error Recovery Decision Tree

```mermaid
graph TD
    A[Error Detected] --> B{Component?}
    
    B -->|STT| C{Retry Count < 3?}
    B -->|LLM| D{Retry Count < 3?}
    B -->|TTS| E{Retry Count < 3?}
    B -->|Network| F{Connection Lost?}
    
    C -->|Yes| G[Exponential Backoff<br/>Wait 2^n seconds]
    C -->|No| H[Send ERROR<br/>recoverable=false]
    
    D -->|Yes| I[Exponential Backoff<br/>Wait 2^n seconds]
    D -->|No| J[Send ERROR<br/>recoverable=false]
    
    E -->|Yes| K[Exponential Backoff<br/>Wait 2^n seconds]
    E -->|No| L[Send ERROR<br/>recoverable=false]
    
    F -->|Yes| M[Wait for reconnect<br/>30s timeout]
    F -->|No| N[Send ERROR<br/>recoverable=true]
    
    G --> O[Retry STT]
    I --> P[Retry LLM]
    K --> Q[Retry TTS]
    
    O --> R{Success?}
    P --> S{Success?}
    Q --> T{Success?}
    
    R -->|Yes| U[Continue Call]
    R -->|No| C
    
    S -->|Yes| U
    S -->|No| D
    
    T -->|Yes| U
    T -->|No| E
    
    H --> V[SESSION_END]
    J --> V
    L --> V
    
    M --> W{Reconnected?}
    W -->|Yes| X[Resume Session]
    W -->|No| V
    
    N --> Y[Continue with<br/>degraded service]
    
    V --> Z[Close Connection]
    Z --> AA[Cleanup]
```

---

## Message Format Examples

### SESSION_START
```json
{
  "type": "session_start",
  "call_id": "550e8400-e29b-41d4-a716-446655440000",
  "campaign_id": "campaign-123",
  "lead_id": "lead-456",
  "system_prompt": "You are a helpful sales assistant for Acme Corp.",
  "voice_id": "6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
  "language": "en",
  "timestamp": "2025-12-03T20:00:00.000Z"
}
```

### TRANSCRIPT_CHUNK
```json
{
  "type": "transcript_chunk",
  "call_id": "550e8400-e29b-41d4-a716-446655440000",
  "text": "Hello, how are you?",
  "is_final": true,
  "confidence": 0.95,
  "timestamp": "2025-12-03T20:00:05.123Z"
}
```

### TURN_END
```json
{
  "type": "turn_end",
  "call_id": "550e8400-e29b-41d4-a716-446655440000",
  "turn_id": 1,
  "full_transcript": "Hello, how are you?",
  "timestamp": "2025-12-03T20:00:05.456Z"
}
```

### ERROR
```json
{
  "type": "error",
  "call_id": "550e8400-e29b-41d4-a716-446655440000",
  "error_code": "STT_TIMEOUT",
  "error_message": "Speech-to-text service timed out after 5 seconds",
  "component": "stt",
  "recoverable": true,
  "timestamp": "2025-12-03T20:00:10.789Z"
}
```

### SESSION_END
```json
{
  "type": "session_end",
  "call_id": "550e8400-e29b-41d4-a716-446655440000",
  "reason": "hangup",
  "duration_seconds": 125.5,
  "timestamp": "2025-12-03T20:02:05.500Z"
}
```

---

## Implementation Notes

### Sequence Number Management

```python
class AudioSequencer:
    def __init__(self):
        self.inbound_seq = 0
        self.outbound_seq = 0
        self.missing_packets = set()
    
    def next_inbound(self) -> int:
        self.inbound_seq += 1
        return self.inbound_seq
    
    def next_outbound(self) -> int:
        self.outbound_seq += 1
        return self.outbound_seq
    
    def check_sequence(self, received_seq: int, direction: str) -> bool:
        """Check for missing packets"""
        expected = self.inbound_seq + 1 if direction == "inbound" else self.outbound_seq + 1
        
        if received_seq != expected:
            # Packet loss detected
            for i in range(expected, received_seq):
                self.missing_packets.add(i)
            return False
        return True
```

### Latency Tracking

```python
class LatencyTracker:
    def __init__(self):
        self.timestamps = {}
    
    def mark(self, event: str, call_id: str):
        """Mark timestamp for an event"""
        key = f"{call_id}:{event}"
        self.timestamps[key] = time.time()
    
    def measure(self, start_event: str, end_event: str, call_id: str) -> float:
        """Measure latency between two events in milliseconds"""
        start_key = f"{call_id}:{start_event}"
        end_key = f"{call_id}:{end_event}"
        
        if start_key not in self.timestamps or end_key not in self.timestamps:
            return 0.0
        
        return (self.timestamps[end_key] - self.timestamps[start_key]) * 1000
```
