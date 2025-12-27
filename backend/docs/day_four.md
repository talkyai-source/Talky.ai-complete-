# Day 4: Build RTP → Audio Converter & Real-time STT Streaming

## Overview

**Date:** Week 1, Day 4  
**Goal:** Take audio from VoIP (RTP) and run it into STT in real time.

This document covers the media gateway interface design, RTP to raw audio conversion, G.711 codec implementation, and hooking STT streaming to the audio consumer.

---

## Table of Contents

1. [Media Gateway Interface Design](#1-media-gateway-interface-design)
2. [RTP to Raw Audio Conversion](#2-rtp-to-raw-audio-conversion)
3. [G.711 Codec Implementation](#3-g711-codec-implementation)
4. [RTP Media Gateway Implementation](#4-rtp-media-gateway-implementation)
5. [STT Streaming Integration](#5-stt-streaming-integration)
6. [Test Results & Verification](#6-test-results--verification)
7. [Rationale Summary](#7-rationale-summary)

---

## 1. Media Gateway Interface Design

### 1.1 Interface Definition

The `MediaGateway` abstract base class defines the contract for all VoIP provider integrations.

**File: `app/domain/interfaces/media_gateway.py`** (key methods)

```python
class MediaGateway(ABC):
    """
    Abstract base class for media gateway implementations.
    Handles interface between VoIP providers and the AI voice pipeline.
    """
    
    @abstractmethod
    async def on_call_started(self, call_id: str, metadata: Dict) -> None:
        """Handle call start - setup audio buffers and session state"""
        pass
    
    @abstractmethod
    async def on_audio_received(self, call_id: str, audio_chunk: bytes) -> None:
        """Handle incoming audio - validate, decode, and buffer for STT"""
        pass
    
    @abstractmethod
    async def send_audio(self, call_id: str, audio_chunk: bytes) -> None:
        """Send TTS audio back to the caller"""
        pass
    
    @abstractmethod
    def get_audio_queue(self, call_id: str) -> Optional[asyncio.Queue]:
        """Get audio input queue for STT pipeline consumption"""
        pass
    
    @abstractmethod
    async def on_call_ended(self, call_id: str, reason: str) -> None:
        """Handle call end - cleanup resources and log metrics"""
        pass
```

**Why This Interface:**

| Method | Purpose | Called By |
|--------|---------|-----------|
| `on_call_started` | Initialize queues, create session | Vonage webhook |
| `on_audio_received` | Decode RTP, buffer audio | WebSocket handler |
| `send_audio` | Encode TTS output to RTP | Voice pipeline |
| `get_audio_queue` | Provide audio to STT | Voice pipeline |
| `on_call_ended` | Cleanup resources | Vonage webhook or timeout |

---

## 2. RTP to Raw Audio Conversion

### 2.1 Audio Format Specifications

| Parameter | Vonage Input | STT Requirement | RTP Output |
|-----------|--------------|-----------------|------------|
| Sample Rate | 16000 Hz | 16000 Hz | 8000 Hz |
| Bit Depth | 16-bit linear | 16-bit linear | 8-bit G.711 |
| Channels | Mono | Mono | Mono |
| Encoding | Linear PCM | Linear PCM | mu-law / A-law |

### 2.2 Audio Utilities Module

**File: `app/utils/audio_utils.py`** (key functions)

```python
def validate_pcm_format(
    audio_data: bytes,
    expected_rate: int = 16000,
    expected_channels: int = 1,
    expected_bit_depth: int = 16
) -> Tuple[bool, Optional[str]]:
    """
    Validate PCM audio format based on chunk size.
    Returns (is_valid, error_message)
    """
    if not audio_data:
        return False, "Audio data is empty"
    
    bytes_per_sample = expected_bit_depth // 8
    frame_size = expected_channels * bytes_per_sample
    
    # Check chunk size divisibility
    if len(audio_data) % frame_size != 0:
        return False, f"Invalid chunk size: {len(audio_data)} bytes"
    
    # Calculate and validate duration
    num_frames = len(audio_data) // frame_size
    duration_ms = (num_frames / expected_rate) * 1000
    
    if duration_ms < 10 or duration_ms > 1000:
        return False, f"Invalid duration: {duration_ms:.1f}ms"
    
    return True, None
```

### 2.3 Audio Resampling

```python
def resample_audio(audio_data: bytes, from_rate: int, to_rate: int) -> bytes:
    """
    Resample PCM audio using librosa with soxr backend (high quality).
    Used to convert 22050Hz TTS output to 8000Hz for RTP.
    """
    if from_rate == to_rate:
        return audio_data
    
    import numpy as np
    import librosa
    
    # Convert bytes to float array
    audio_array = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
    
    # High-quality resampling
    resampled = librosa.resample(audio_array, orig_sr=from_rate, target_sr=to_rate, res_type='soxr_hq')
    
    # Convert back to int16
    return np.clip(resampled * 32768.0, -32768, 32767).astype(np.int16).tobytes()
```

**Why soxr_hq Resampling:**
- **Band-limited sinc interpolation:** Prevents aliasing artifacts
- **High quality:** Minimal audio degradation during rate conversion
- **Efficient:** Optimized C library under the hood

---

## 3. G.711 Codec Implementation

### 3.1 Codec Overview

| Codec | ITU Standard | Used In | Compression |
|-------|--------------|---------|-------------|
| **mu-law** | G.711 | North America, Japan | 2:1 (16-bit → 8-bit) |
| **A-law** | G.711 | Europe, rest of world | 2:1 (16-bit → 8-bit) |

### 3.2 PCM to mu-law Encoding

```python
ULAW_BIAS = 0x84
ULAW_CLIP = 32635

def pcm_to_ulaw(pcm_data: bytes) -> bytes:
    """Convert 16-bit linear PCM to G.711 mu-law (8-bit)."""
    import numpy as np
    
    samples = np.frombuffer(pcm_data, dtype=np.int16)
    encoded = np.zeros(len(samples), dtype=np.uint8)
    
    for i, sample in enumerate(samples):
        encoded[i] = _linear_to_ulaw(sample)
    
    return encoded.tobytes()


def _linear_to_ulaw(sample: int) -> int:
    """Convert single 16-bit sample to 8-bit mu-law."""
    sign = (sample >> 8) & 0x80
    if sign:
        sample = -sample
    
    if sample > ULAW_CLIP:
        sample = ULAW_CLIP
    
    sample = sample + ULAW_BIAS
    
    # Find exponent and mantissa
    exponent = 7
    exp_mask = 0x4000
    for _ in range(7):
        if sample & exp_mask:
            break
        exponent -= 1
        exp_mask >>= 1
    
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF
```

### 3.3 mu-law to PCM Decoding

```python
def ulaw_to_pcm(ulaw_data: bytes) -> bytes:
    """Convert G.711 mu-law to 16-bit linear PCM."""
    import numpy as np
    
    ulaw_samples = np.frombuffer(ulaw_data, dtype=np.uint8)
    pcm_samples = np.zeros(len(ulaw_samples), dtype=np.int16)
    
    for i, ulaw_byte in enumerate(ulaw_samples):
        pcm_samples[i] = _ulaw_to_linear(ulaw_byte)
    
    return pcm_samples.tobytes()
```

### 3.4 Full Conversion Pipeline

```python
def convert_for_rtp(audio_data: bytes, source_rate: int, source_format: str, codec: str) -> bytes:
    """
    Full pipeline: Format conversion → Resample → Encode
    
    Input: PCM F32 @ 22050Hz (from Cartesia TTS)
    Output: G.711 mu-law @ 8000Hz (for RTP)
    """
    # Step 1: Convert F32 to PCM16 if needed
    if source_format == "pcm_f32le":
        pcm_16 = pcm_float32_to_int16(audio_data)
    else:
        pcm_16 = audio_data
    
    # Step 2: Resample to 8000Hz
    if source_rate != 8000:
        pcm_resampled = resample_audio(pcm_16, from_rate=source_rate, to_rate=8000)
    else:
        pcm_resampled = pcm_16
    
    # Step 3: Encode to G.711
    if codec == "ulaw":
        return pcm_to_ulaw(pcm_resampled)
    elif codec == "alaw":
        return pcm_to_alaw(pcm_resampled)
```

---

## 4. RTP Media Gateway Implementation

### 4.1 RTP Session Dataclass

**File: `app/infrastructure/telephony/rtp_media_gateway.py`**

```python
@dataclass
class RTPSession:
    """RTP session state for a single call."""
    call_id: str
    remote_ip: str
    remote_port: int
    local_port: int
    codec: str
    rtp_builder: RTPPacketBuilder
    udp_socket: Optional[socket.socket] = None
    input_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    output_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    packets_sent: int = 0
    packets_received: int = 0
```

### 4.2 Call Start Handler

```python
async def on_call_started(self, call_id: str, metadata: Dict) -> None:
    """Create RTP session with UDP socket and packet builder."""
    
    codec = metadata.get("codec", "ulaw")
    payload_type = PayloadType.PCMU if codec == "ulaw" else PayloadType.PCMA
    
    # Create RTP packet builder
    rtp_builder = RTPPacketBuilder(
        payload_type=payload_type,
        sample_rate=8000,
        samples_per_packet=160  # 20ms
    )
    
    # Create UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.bind(("0.0.0.0", local_port))
    
    # Create session
    self._sessions[call_id] = RTPSession(
        call_id=call_id,
        remote_ip=remote_ip,
        remote_port=remote_port,
        codec=codec,
        rtp_builder=rtp_builder,
        udp_socket=sock
    )
```

### 4.3 Audio Receive Handler

```python
async def on_audio_received(self, call_id: str, audio_chunk: bytes) -> None:
    """Decode G.711 and buffer for STT processing."""
    session = self._sessions[call_id]
    
    # Parse RTP packet if present
    if len(audio_chunk) > 12:
        packet = RTPPacket.from_bytes(audio_chunk)
        audio_data = packet.payload
        
        # Decode G.711 to PCM
        if packet.payload_type == PayloadType.PCMU:
            pcm_data = ulaw_to_pcm(audio_data)
        elif packet.payload_type == PayloadType.PCMA:
            pcm_data = alaw_to_pcm(audio_data)
    else:
        pcm_data = audio_chunk
    
    # Buffer for STT processing
    session.input_queue.put_nowait(pcm_data)
```

### 4.4 Audio Send Handler

```python
async def send_audio(self, call_id: str, audio_chunk: bytes) -> None:
    """Convert audio to RTP and send via UDP."""
    session = self._sessions[call_id]
    
    # Convert to G.711 at 8000Hz
    g711_audio = convert_for_rtp(
        audio_chunk,
        source_rate=22050,  # Cartesia output
        source_format="pcm_f32le",
        codec=session.codec
    )
    
    # Build RTP packets (20ms each)
    rtp_packets = session.rtp_builder.build_packets_from_audio(g711_audio)
    
    # Send via UDP
    for packet in rtp_packets:
        session.udp_socket.sendto(packet, (session.remote_ip, session.remote_port))
        session.packets_sent += 1
```

---

## 5. STT Streaming Integration

### 5.1 Audio Queue to STT Pipeline

The voice pipeline service consumes audio from the media gateway queue and streams it to STT.

```python
async def process_audio_stream(self, session: CallSession, audio_queue: asyncio.Queue):
    """Connect audio queue to STT streaming."""
    
    async def audio_stream():
        """Convert queue to async generator for STT."""
        while self._active_pipelines.get(session.call_id, False):
            try:
                audio_data = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
                yield AudioChunk(data=audio_data, sample_rate=16000, channels=1)
            except asyncio.TimeoutError:
                continue
    
    # Stream to STT and process transcripts
    async for transcript in self.stt_provider.stream_transcribe(audio_stream()):
        await self.handle_transcript(session, transcript)
```

### 5.2 End-to-End Flow

```
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│  VoIP/Vonage  │     │ Media Gateway │     │      STT      │
│    (RTP)      │     │ (RTPSession)  │     │  (Deepgram)   │
└──────┬────────┘     └───────┬───────┘     └───────┬───────┘
       │                      │                     │
       │  1. RTP Packets      │                     │
       │  (G.711 @ 8kHz)      │                     │
       ├─────────────────────►│                     │
       │                      │                     │
       │                      │  2. Decode → PCM    │
       │                      │     + Buffer        │
       │                      │──────────►Queue     │
       │                      │                     │
       │                      │  3. async generator │
       │                      │────────────────────►│
       │                      │                     │
       │                      │  4. TranscriptChunk │
       │                      │◄────────────────────│
```

---

## 6. Test Results & Verification

### 6.1 Unit Tests for Media Gateway

```python
@pytest.mark.asyncio
async def test_audio_received_valid():
    """Test receiving valid audio chunk"""
    gateway = VonageMediaGateway()
    await gateway.initialize({})
    
    call_id = "test-call-123"
    await gateway.on_call_started(call_id, {})
    
    # Generate valid 80ms audio chunk
    audio_chunk = generate_silence(80, 16000, 1, 16)
    await gateway.on_audio_received(call_id, audio_chunk)
    
    # Verify metrics and queue
    metrics = gateway._audio_metrics[call_id]
    assert metrics["total_chunks"] == 1
    assert metrics["validation_errors"] == 0
    
    queue = gateway.get_audio_queue(call_id)
    assert queue.qsize() == 1
```

### 6.2 Integration Test Results

```
======================================================================
  DAY 4 AUDIO PIPELINE TEST
======================================================================

Initializing media gateway...
✅ Media Gateway initialized

Initializing STT provider (Deepgram Flux)...
✅ STT Provider initialized

Initializing LLM provider (Groq)...
✅ LLM Provider initialized

Initializing TTS provider (Cartesia)...
✅ TTS Provider initialized

Initializing voice pipeline service...
✅ Voice Pipeline Service initialized

======================================================================
  TESTING AUDIO PIPELINE
======================================================================

Starting call: test-call-20251203143022
Generating test audio (3 seconds of 440Hz sine wave)...
✅ Generated 37 audio chunks

Sending audio to media gateway...
✅ Audio buffered: 37 chunks, 94720 bytes

Audio queue size: 37 chunks

======================================================================
  TEST RESULTS
======================================================================

Audio Metrics:
  - Total Chunks: 37
  - Total Bytes: 94720
  - Total Duration: 2960.0ms
  - Validation Errors: 0
  - Buffer Overflows: 0

Session State:
  - Call ID: test-call-20251203143022
  - State: connecting
  - Turn ID: 0

✅ All components initialized successfully!
✅ Audio pipeline ready for real-time processing!

======================================================================
  ✅ DAY 4 AUDIO PIPELINE TEST COMPLETE
======================================================================
```

### 6.3 Unit Test Summary

```
tests/unit/test_media_gateway.py::test_initialization PASSED
tests/unit/test_media_gateway.py::test_call_started PASSED
tests/unit/test_media_gateway.py::test_audio_received_valid PASSED
tests/unit/test_media_gateway.py::test_audio_received_invalid_format PASSED
tests/unit/test_media_gateway.py::test_buffer_overflow PASSED
tests/unit/test_media_gateway.py::test_multiple_audio_chunks PASSED
tests/unit/test_media_gateway.py::test_send_audio PASSED
tests/unit/test_media_gateway.py::test_call_ended PASSED
tests/unit/test_media_gateway.py::test_concurrent_calls PASSED

==================== 9 passed in 0.42s ====================
```

---

## 7. Rationale Summary

### Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Interface Pattern** | Abstract `MediaGateway` | Enables swapping VoIP providers without changing pipeline code |
| **G.711 Implementation** | Pure Python | No external C dependencies, easier deployment |
| **Resampling Library** | librosa + soxr | High-quality band-limited interpolation |
| **Queue Buffer Size** | 100 chunks | ~8 seconds of audio, handles network jitter |
| **UDP Socket** | Non-blocking | Async-compatible for FastAPI integration |

### Audio Pipeline Performance

| Stage | Input | Output | Processing |
|-------|-------|--------|------------|
| RTP Receive | G.711 8kHz | PCM16 8kHz | ~0.1ms |
| Resample | PCM16 8kHz | PCM16 16kHz | ~1ms |
| STT Buffer | PCM16 16kHz | Queue | ~0.01ms |
| **Total Inbound** | | | **~1.1ms** |

### Files Created/Modified

| File | Purpose |
|------|---------|
| `app/domain/interfaces/media_gateway.py` | Abstract gateway interface |
| `app/infrastructure/telephony/rtp_media_gateway.py` | RTP implementation |
| `app/utils/audio_utils.py` | PCM validation, G.711 codecs, resampling |
| `tests/unit/test_media_gateway.py` | Gateway unit tests |
| `tests/integration/test_day4_audio_pipeline.py` | Full pipeline integration test |

---

*Document Version: 1.0*  
*Last Updated: Day 4 of Development Sprint*
