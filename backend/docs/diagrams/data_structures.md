# WebSocket Data Structures

## Message Envelope Structure

### Binary Frame (Audio)

**Production Format (Optimized):**

```
┌─────────────────────────────────────────────────────┐
│ Header (32 bytes)                                   │
├─────────────────────────────────────────────────────┤
│ - Magic Number: 0x544B4149 (4 bytes) "TKAI"        │
│ - Version: 0x01 (1 byte)                            │
│ - Message Type: 0x01 = AUDIO_CHUNK (1 byte)        │
│ - Direction: 0x00 = inbound, 0x01 = outbound       │
│ - Sequence Number: uint32 (4 bytes)                │
│ - Timestamp: uint64 (8 bytes, Unix ms)             │
│ - Sample Rate: uint32 (4 bytes)                    │
│ - Channels: uint8 (1 byte)                         │
│ - Data Length: uint32 (4 bytes)                    │
│ - Reserved: (7 bytes)                              │
├─────────────────────────────────────────────────────┤
│ Audio Data (PCM linear16)                          │
│ - Variable length                                  │
│ - Typically 1280-3200 bytes (80-200ms @ 16kHz)    │
└─────────────────────────────────────────────────────┘
```

**Development Format (JSON with base64):**

```json
{
  "type": "audio_chunk",
  "call_id": "550e8400-e29b-41d4-a716-446655440000",
  "direction": "inbound",
  "data": "AAABAAACAAADAAA...",  // base64 encoded PCM
  "sample_rate": 16000,
  "channels": 1,
  "timestamp": "2025-12-03T20:00:05.123Z",
  "sequence": 42
}
```

### Text Frame (Control Messages)

**Standard JSON Format:**

```json
{
  "type": "transcript_chunk",
  "call_id": "550e8400-e29b-41d4-a716-446655440000",
  "data": {
    "text": "Hello, how can I help you?",
    "is_final": true,
    "confidence": 0.95
  },
  "timestamp": "2025-12-03T20:00:05.456Z"
}
```

---

## Message Size Limits

| Message Type | Typical Size | Maximum Size | Frequency |
|--------------|--------------|--------------|-----------|
| AUDIO_CHUNK (inbound) | 1-3 KB | 64 KB | 12-20/sec |
| AUDIO_CHUNK (outbound) | 1-3 KB | 64 KB | 12-20/sec |
| TRANSCRIPT_CHUNK | 100-500 bytes | 4 KB | 1-3/sec |
| TURN_END | 150-300 bytes | 2 KB | 0.1-0.3/sec |
| LLM_START/END | 100-200 bytes | 2 KB | 0.1-0.3/sec |
| TTS_START/END | 100-200 bytes | 2 KB | 0.1-0.3/sec |
| SESSION_START | 300-600 bytes | 4 KB | Once per call |
| SESSION_END | 150-300 bytes | 2 KB | Once per call |
| ERROR | 200-400 bytes | 2 KB | Rare |
| PING/PONG | 100-150 bytes | 1 KB | 1/30sec |

---

## Bandwidth Estimation

### Audio Streaming (16kHz, mono, linear16)

**Calculation:**
- Sample rate: 16,000 Hz
- Bit depth: 16 bits = 2 bytes
- Channels: 1 (mono)
- **Raw bitrate:** 16,000 × 2 = 32,000 bytes/sec = 32 KB/s = 256 kbps

**With Protocol Overhead:**
- WebSocket frame header: ~10 bytes per frame
- Audio chunk header: 32 bytes (custom header)
- Chunk size: 1600 bytes (100ms @ 16kHz)
- Chunks per second: 10
- **Overhead per second:** (10 + 32) × 10 = 420 bytes/sec
- **Total bitrate:** 32,000 + 420 = 32,420 bytes/sec ≈ 260 kbps

**Bidirectional (inbound + outbound):**
- **Total:** 260 kbps × 2 = 520 kbps

### Control Messages

**Typical Message Flow (per second):**
- TRANSCRIPT_CHUNK: 2 messages × 300 bytes = 600 bytes
- Other control messages: 1 message × 200 bytes = 200 bytes
- **Total:** 800 bytes/sec = 6.4 kbps

### Total Bandwidth per Call

| Component | Bandwidth |
|-----------|-----------|
| Audio (bidirectional) | 520 kbps |
| Control messages | 6.4 kbps |
| WebSocket overhead | ~20 kbps |
| **Total per call** | **~550 kbps** |

### Concurrent Calls

| Concurrent Calls | Total Bandwidth |
|------------------|-----------------|
| 10 | 5.5 Mbps |
| 100 | 55 Mbps |
| 1,000 | 550 Mbps |
| 10,000 | 5.5 Gbps |

---

## Audio Format Specifications

### Vonage Inbound Audio

**Format:** PCM 16-bit linear, 16kHz, mono

```
Content-Type: audio/l16;rate=16000
Sample Rate: 16000 Hz
Bit Depth: 16 bits (signed integer)
Channels: 1 (mono)
Byte Order: Little-endian
Encoding: Linear PCM (no compression)
```

**Sample Calculation:**
- 1 second of audio = 16,000 samples × 2 bytes = 32,000 bytes
- 100ms chunk = 1,600 samples × 2 bytes = 3,200 bytes
- 50ms chunk = 800 samples × 2 bytes = 1,600 bytes

### Cartesia TTS Output

**Format:** PCM 32-bit float, configurable sample rate

```
Default Output: pcm_f32le
Sample Rate: 16000 Hz (configurable: 8000, 16000, 22050, 24000, 44100)
Bit Depth: 32 bits (float)
Channels: 1 (mono)
Byte Order: Little-endian
```

**Conversion to Vonage Format:**
```python
import numpy as np

def convert_f32le_to_l16(audio_f32: bytes, sample_rate: int = 16000) -> bytes:
    """
    Convert PCM f32le (Cartesia output) to PCM l16 (Vonage input)
    
    Args:
        audio_f32: Audio data as 32-bit float PCM
        sample_rate: Sample rate (should match Vonage: 16000)
    
    Returns:
        Audio data as 16-bit linear PCM
    """
    # Convert bytes to numpy array of float32
    audio_array = np.frombuffer(audio_f32, dtype=np.float32)
    
    # Clip to [-1.0, 1.0] range
    audio_array = np.clip(audio_array, -1.0, 1.0)
    
    # Convert to 16-bit integer range [-32768, 32767]
    audio_int16 = (audio_array * 32767).astype(np.int16)
    
    # Convert to bytes
    return audio_int16.tobytes()
```

---

## Pydantic Model Schemas

### AudioChunkMessage

```python
class AudioChunkMessage(BaseModel):
    type: Literal[MessageType.AUDIO_CHUNK] = MessageType.AUDIO_CHUNK
    call_id: str
    direction: MessageDirection  # "inbound" | "outbound"
    data: bytes  # Raw PCM audio
    sample_rate: int = 16000
    channels: int = 1
    timestamp: datetime
    sequence: int  # >= 0
    
    class Config:
        json_encoders = {
            bytes: lambda v: v.hex()
        }
```

**Example Usage:**
```python
# Create audio chunk
chunk = AudioChunkMessage(
    call_id="550e8400-e29b-41d4-a716-446655440000",
    direction=MessageDirection.INBOUND,
    data=audio_bytes,
    sequence=42
)

# Serialize to JSON (for logging)
json_str = chunk.model_dump_json()

# Send as binary WebSocket frame
await websocket.send_bytes(chunk.data)
```

### TranscriptChunkMessage

```python
class TranscriptChunkMessage(BaseModel):
    type: Literal[MessageType.TRANSCRIPT_CHUNK] = MessageType.TRANSCRIPT_CHUNK
    call_id: str
    text: str
    is_final: bool = False
    confidence: Optional[float] = None  # 0.0 to 1.0
    timestamp: datetime
```

**Example Usage:**
```python
# Create transcript chunk
transcript = TranscriptChunkMessage(
    call_id="550e8400-e29b-41d4-a716-446655440000",
    text="Hello, how are you?",
    is_final=True,
    confidence=0.95
)

# Send as text WebSocket frame
await websocket.send_json(transcript.model_dump())
```

---

## Binary Protocol Implementation

### Header Packing (Python)

```python
import struct
from datetime import datetime

MAGIC_NUMBER = 0x544B4149  # "TKAI"
VERSION = 0x01
MESSAGE_TYPE_AUDIO = 0x01
DIRECTION_INBOUND = 0x00
DIRECTION_OUTBOUND = 0x01

def pack_audio_header(
    sequence: int,
    timestamp: datetime,
    sample_rate: int,
    channels: int,
    data_length: int,
    direction: str
) -> bytes:
    """
    Pack audio chunk header (32 bytes)
    
    Format: >IBBBIIQIIB7x
    - > : Big-endian
    - I : uint32 (magic number)
    - B : uint8 (version)
    - B : uint8 (message type)
    - B : uint8 (direction)
    - I : uint32 (sequence)
    - Q : uint64 (timestamp in milliseconds)
    - I : uint32 (sample rate)
    - I : uint8 (channels)
    - B : uint32 (data length)
    - 7x: 7 reserved bytes
    """
    direction_byte = DIRECTION_INBOUND if direction == "inbound" else DIRECTION_OUTBOUND
    timestamp_ms = int(timestamp.timestamp() * 1000)
    
    header = struct.pack(
        '>IBBBIIQIIB7x',
        MAGIC_NUMBER,
        VERSION,
        MESSAGE_TYPE_AUDIO,
        direction_byte,
        sequence,
        timestamp_ms,
        sample_rate,
        channels,
        data_length
    )
    
    return header

def unpack_audio_header(header: bytes) -> dict:
    """Unpack audio chunk header"""
    (
        magic,
        version,
        msg_type,
        direction,
        sequence,
        timestamp_ms,
        sample_rate,
        channels,
        data_length
    ) = struct.unpack('>IBBBIIQIIB7x', header[:32])
    
    if magic != MAGIC_NUMBER:
        raise ValueError(f"Invalid magic number: {hex(magic)}")
    
    return {
        "version": version,
        "message_type": msg_type,
        "direction": "inbound" if direction == DIRECTION_INBOUND else "outbound",
        "sequence": sequence,
        "timestamp": datetime.fromtimestamp(timestamp_ms / 1000),
        "sample_rate": sample_rate,
        "channels": channels,
        "data_length": data_length
    }
```

### Sending Binary Audio

```python
async def send_audio_chunk(
    websocket: WebSocket,
    audio_data: bytes,
    call_id: str,
    sequence: int,
    direction: str = "outbound"
):
    """Send audio chunk as binary WebSocket frame"""
    # Pack header
    header = pack_audio_header(
        sequence=sequence,
        timestamp=datetime.utcnow(),
        sample_rate=16000,
        channels=1,
        data_length=len(audio_data),
        direction=direction
    )
    
    # Combine header + audio data
    frame = header + audio_data
    
    # Send as binary frame
    await websocket.send_bytes(frame)
```

### Receiving Binary Audio

```python
async def receive_audio_chunk(websocket: WebSocket) -> dict:
    """Receive audio chunk from binary WebSocket frame"""
    # Receive binary frame
    frame = await websocket.receive_bytes()
    
    # Extract header (first 32 bytes)
    header_bytes = frame[:32]
    audio_data = frame[32:]
    
    # Unpack header
    header = unpack_audio_header(header_bytes)
    
    # Validate data length
    if len(audio_data) != header["data_length"]:
        raise ValueError(f"Data length mismatch: expected {header['data_length']}, got {len(audio_data)}")
    
    return {
        **header,
        "audio_data": audio_data
    }
```

---

## Memory Management

### Buffer Sizes

```python
# Audio buffer configuration
AUDIO_BUFFER_SIZE = 100  # Number of chunks to buffer
AUDIO_CHUNK_SIZE_MS = 80  # Milliseconds per chunk
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2

# Calculate buffer memory
chunk_size_bytes = (SAMPLE_RATE * AUDIO_CHUNK_SIZE_MS // 1000) * BYTES_PER_SAMPLE
buffer_memory = AUDIO_BUFFER_SIZE * chunk_size_bytes

print(f"Chunk size: {chunk_size_bytes} bytes")
print(f"Buffer memory: {buffer_memory / 1024:.2f} KB")
# Output:
# Chunk size: 2560 bytes
# Buffer memory: 250.00 KB
```

### Concurrent Call Memory

```python
# Per-call memory estimate
per_call_memory = {
    "audio_input_buffer": 250,   # KB
    "audio_output_buffer": 250,  # KB
    "transcript_buffer": 50,     # KB
    "session_state": 10,         # KB
    "websocket_overhead": 20,    # KB
}

total_per_call = sum(per_call_memory.values())
print(f"Memory per call: {total_per_call} KB")

# 1000 concurrent calls
concurrent_calls = 1000
total_memory_mb = (total_per_call * concurrent_calls) / 1024
print(f"Memory for {concurrent_calls} calls: {total_memory_mb:.2f} MB")
# Output:
# Memory per call: 580 KB
# Memory for 1000 calls: 566.41 MB
```

---

## Performance Optimization

### Chunking Strategy

**Optimal Chunk Size:**
- **Too small (< 50ms):** High overhead, more WebSocket frames
- **Too large (> 200ms):** Increased latency, delayed processing
- **Recommended:** 80-100ms chunks

**Calculation:**
```python
def calculate_chunk_size(sample_rate: int, chunk_ms: int) -> int:
    """Calculate chunk size in bytes"""
    samples_per_chunk = (sample_rate * chunk_ms) // 1000
    bytes_per_chunk = samples_per_chunk * 2  # 16-bit = 2 bytes
    return bytes_per_chunk

# Examples
print(calculate_chunk_size(16000, 50))   # 1600 bytes
print(calculate_chunk_size(16000, 80))   # 2560 bytes
print(calculate_chunk_size(16000, 100))  # 3200 bytes
```

### Compression

**Note:** Audio data is NOT compressed for real-time streaming

**Reasons:**
1. **Latency:** Compression/decompression adds 10-50ms
2. **CPU:** Compression is CPU-intensive
3. **Bandwidth:** 550 kbps per call is acceptable
4. **Quality:** PCM maintains audio quality for STT/TTS

**When to compress:**
- Storing recordings (use Opus, MP3)
- Bandwidth-constrained networks (use Opus codec)
- Not for real-time streaming

---

## Validation Rules

### Message Validation

```python
from pydantic import field_validator

class AudioChunkMessage(BaseModel):
    # ... fields ...
    
    @field_validator('sample_rate')
    @classmethod
    def validate_sample_rate(cls, v):
        valid_rates = [8000, 16000, 22050, 24000, 44100]
        if v not in valid_rates:
            raise ValueError(f"Sample rate must be one of {valid_rates}")
        return v
    
    @field_validator('channels')
    @classmethod
    def validate_channels(cls, v):
        if v not in [1, 2]:
            raise ValueError("Channels must be 1 (mono) or 2 (stereo)")
        return v
    
    @field_validator('data')
    @classmethod
    def validate_data_length(cls, v):
        if len(v) > 65536:  # 64 KB
            raise ValueError("Audio chunk too large (max 64 KB)")
        if len(v) == 0:
            raise ValueError("Audio chunk cannot be empty")
        return v
```

---

## Testing Data

### Sample Audio Chunk (Hex)

```
Header (32 bytes):
54 4B 41 49  01 01 00 00  00 00 00 2A  00 00 01 8B
3E 4F 5A 10  00 00 3E 80  01 00 00 0A  00 00 00 00
00 00 00

Audio Data (10 bytes, example):
00 01 00 02 00 03 00 04 00 05
```

### Sample JSON Messages

**SESSION_START:**
```json
{
  "type": "session_start",
  "call_id": "test-call-001",
  "campaign_id": "campaign-001",
  "lead_id": "lead-001",
  "system_prompt": "You are a helpful assistant.",
  "voice_id": "voice-001",
  "language": "en",
  "timestamp": "2025-12-03T20:00:00.000Z"
}
```

**TRANSCRIPT_CHUNK:**
```json
{
  "type": "transcript_chunk",
  "call_id": "test-call-001",
  "text": "Hello world",
  "is_final": true,
  "confidence": 0.95,
  "timestamp": "2025-12-03T20:00:01.234Z"
}
```
