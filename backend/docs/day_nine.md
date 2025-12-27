# Day 9: Recording Storage & Transcript Management

## Overview

**Date:** Week 2, Day 9  
**Goal:** Implement call recording buffer, storage upload to Supabase, transcript persistence, and streaming endpoints.

This document covers the recording buffer system, Supabase Storage integration, WAV file generation, transcript storage, and audio streaming API.

---

## Table of Contents

1. [Recording Architecture](#1-recording-architecture)
2. [Recording Buffer](#2-recording-buffer)
3. [Recording Service](#3-recording-service)
4. [Supabase Storage Integration](#4-supabase-storage-integration)
5. [Recording API Endpoints](#5-recording-api-endpoints)
6. [Transcript Storage](#6-transcript-storage)
7. [Test Results & Verification](#7-test-results--verification)
8. [Rationale Summary](#8-rationale-summary)

---

## 1. Recording Architecture

### 1.1 Data Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Vonage     │     │  Recording   │     │    WAV       │
│   Audio      │────►│   Buffer     │────►│  Converter   │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                     ┌────────────────────────────┘
                     │
                     ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Supabase   │────►│  Recordings  │────►│   Signed     │
│   Storage    │     │   Table      │     │   URL API    │
└──────────────┘     └──────────────┘     └──────────────┘
```

### 1.2 Storage Structure

```
recordings/                          # Supabase Storage bucket
├── {tenant_id}/                     # Tenant folder
│   ├── {campaign_id}/               # Campaign folder
│   │   ├── {call_id_1}.wav          # Recording file
│   │   ├── {call_id_2}.wav
│   │   └── ...
```

---

## 2. Recording Buffer

### 2.1 Buffer Class

**File: `app/domain/services/recording_service.py`**

```python
@dataclass
class RecordingBuffer:
    """
    Accumulates audio chunks during a call for later saving.
    
    Attributes:
        call_id: Unique call identifier
        sample_rate: Audio sample rate (16000 for Vonage)
        channels: Number of channels (1 = mono)
        bit_depth: Bits per sample (16 for PCM16)
    """
    call_id: str
    sample_rate: int = 16000
    channels: int = 1
    bit_depth: int = 16
    
    chunks: List[bytes] = field(default_factory=list)
    total_bytes: int = 0
    started_at: datetime = field(default_factory=datetime.utcnow)
```

### 2.2 Buffer Operations

```python
def add_chunk(self, audio_data: bytes) -> None:
    """Add an audio chunk to the buffer."""
    self.chunks.append(audio_data)
    self.total_bytes += len(audio_data)

def get_complete_audio(self) -> bytes:
    """Get all accumulated audio as a single bytes object."""
    return b''.join(self.chunks)

def get_duration_seconds(self) -> float:
    """Calculate total duration in seconds."""
    bytes_per_second = self.sample_rate * self.channels * (self.bit_depth // 8)
    if bytes_per_second == 0:
        return 0.0
    return self.total_bytes / bytes_per_second

def clear(self) -> None:
    """Clear all accumulated audio data."""
    self.chunks.clear()
    self.total_bytes = 0
```

### 2.3 WAV Conversion

```python
def get_wav_bytes(self) -> bytes:
    """Convert raw PCM audio to WAV format."""
    audio_data = self.get_complete_audio()
    
    # Create WAV file in memory
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(self.channels)
        wav_file.setsampwidth(self.bit_depth // 8)
        wav_file.setframerate(self.sample_rate)
        wav_file.writeframes(audio_data)
    
    wav_buffer.seek(0)
    return wav_buffer.read()
```

---

## 3. Recording Service

### 3.1 Service Class

```python
class RecordingService:
    """
    Handles recording storage operations.
    Provider-agnostic - works with any MediaGateway.
    """
    
    BUCKET_NAME = "recordings"
    
    def __init__(self, supabase_client):
        self._supabase = supabase_client
```

### 3.2 Storage Path Generation

```python
def _generate_storage_path(
    self, 
    call_id: str, 
    tenant_id: str, 
    campaign_id: str
) -> str:
    """
    Generate storage path for a recording.
    Format: {tenant_id}/{campaign_id}/{call_id}.wav
    """
    # Sanitize IDs to prevent path traversal
    safe_tenant = tenant_id.replace("/", "_").replace("\\", "_")
    safe_campaign = campaign_id.replace("/", "_").replace("\\", "_")
    safe_call = call_id.replace("/", "_").replace("\\", "_")
    
    return f"{safe_tenant}/{safe_campaign}/{safe_call}.wav"
```

### 3.3 Save Recording

```python
async def save_recording(
    self,
    call_id: str,
    buffer: RecordingBuffer,
    tenant_id: str,
    campaign_id: str
) -> Optional[str]:
    """Save recording to Supabase Storage."""
    
    if not buffer or buffer.total_bytes == 0:
        logger.warning(f"No audio data to save for call {call_id}")
        return None
    
    # Convert to WAV format
    wav_data = buffer.get_wav_bytes()
    storage_path = self._generate_storage_path(call_id, tenant_id, campaign_id)
    
    # Upload to Supabase Storage
    self._supabase.storage.from_(self.BUCKET_NAME).upload(
        path=storage_path,
        file=wav_data,
        file_options={"content-type": "audio/wav"}
    )
    
    return storage_path
```

### 3.4 Complete Save Workflow

```python
async def save_and_link(
    self,
    call_id: str,
    buffer: RecordingBuffer,
    tenant_id: str,
    campaign_id: str
) -> Optional[str]:
    """
    Complete workflow:
    1. Upload to Supabase Storage
    2. Insert into recordings table
    3. Update calls.recording_url
    """
    # Step 1: Upload to storage
    storage_path = await self.save_recording(
        call_id, buffer, tenant_id, campaign_id
    )
    if not storage_path:
        return None
    
    # Step 2: Create recording record
    recording_id = await self.create_recording_record(
        call_id=call_id,
        storage_path=storage_path,
        duration_seconds=buffer.get_duration_seconds(),
        file_size_bytes=len(buffer.get_wav_bytes()),
        tenant_id=tenant_id
    )
    
    # Step 3: Update calls table
    await self.update_call_recording_url(call_id, storage_path)
    
    return recording_id
```

---

## 4. Supabase Storage Integration

### 4.1 Database Schema

```sql
CREATE TABLE IF NOT EXISTS recordings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    tenant_id VARCHAR(255) NOT NULL,
    storage_path TEXT NOT NULL,
    duration_seconds INTEGER,
    file_size_bytes INTEGER,
    mime_type VARCHAR(50) DEFAULT 'audio/wav',
    status VARCHAR(50) DEFAULT 'completed',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recordings_call_id ON recordings(call_id);
CREATE INDEX IF NOT EXISTS idx_recordings_tenant_id ON recordings(tenant_id);
```

### 4.2 Storage Bucket Configuration

```sql
-- Create storage bucket for recordings
INSERT INTO storage.buckets (id, name, public)
VALUES ('recordings', 'recordings', false);

-- RLS policy for recordings bucket
CREATE POLICY "Tenant can access own recordings"
ON storage.objects FOR SELECT
USING (bucket_id = 'recordings' AND auth.uid()::text = owner);
```

### 4.3 Signed URL Generation

```python
def get_recording_url(self, storage_path: str) -> str:
    """Get a signed URL for a recording (valid for 1 hour)."""
    result = self._supabase.storage.from_(self.BUCKET_NAME).create_signed_url(
        path=storage_path,
        expires_in=3600
    )
    return result.get("signedURL", "")
```

---

## 5. Recording API Endpoints

### 5.1 List Recordings

**File: `app/api/v1/endpoints/recordings.py`**

```python
@router.get("/", response_model=RecordingListResponse)
async def list_recordings(
    call_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    supabase: Client = Depends(get_supabase)
):
    """Get paginated list of recordings."""
    
    query = supabase.table("recordings").select(
        "id, call_id, created_at, duration_seconds",
        count="exact"
    )
    
    if call_id:
        query = query.eq("call_id", call_id)
    
    offset = (page - 1) * page_size
    response = query.order("created_at", desc=True)\
        .range(offset, offset + page_size - 1)\
        .execute()
    
    return RecordingListResponse(
        items=response.data,
        page=page,
        page_size=page_size,
        total=response.count
    )
```

### 5.2 Stream Recording

```python
@router.get("/{recording_id}/stream")
async def stream_recording(
    recording_id: str,
    supabase: Client = Depends(get_supabase)
):
    """Stream recording audio file for HTML5 audio player."""
    
    # Get recording details
    response = supabase.table("recordings").select(
        "storage_path, mime_type"
    ).eq("id", recording_id).single().execute()
    
    if not response.data:
        raise HTTPException(status_code=404, detail="Recording not found")
    
    storage_path = response.data.get("storage_path")
    mime_type = response.data.get("mime_type", "audio/wav")
    
    # Download from Supabase Storage
    file_data = supabase.storage.from_("recordings").download(storage_path)
    
    # Stream the audio
    async def audio_generator():
        chunk_size = 8192
        for i in range(0, len(file_data), chunk_size):
            yield file_data[i:i + chunk_size]
    
    return StreamingResponse(
        audio_generator(),
        media_type=mime_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Disposition": f"inline; filename=recording_{recording_id}.wav"
        }
    )
```

---

## 6. Transcript Storage

### 6.1 Transcript Schema

```sql
CREATE TABLE IF NOT EXISTS transcripts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
    tenant_id VARCHAR(255) NOT NULL,
    turns JSONB DEFAULT '[]',       -- Array of conversation turns
    full_text TEXT,                  -- Complete transcript text
    word_count INTEGER DEFAULT 0,
    turn_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transcripts_call_id ON transcripts(call_id);
```

### 6.2 Transcript Turn Format

```json
{
  "turns": [
    {
      "speaker": "agent",
      "text": "Hello, this is Sarah from Bright Smile Dental.",
      "timestamp": "00:00:00",
      "duration_ms": 2500
    },
    {
      "speaker": "user",
      "text": "Hi, yes I received a call about my appointment.",
      "timestamp": "00:00:03",
      "duration_ms": 3200
    }
  ],
  "full_text": "Agent: Hello, this is Sarah...\nUser: Hi, yes I received...",
  "word_count": 45,
  "turn_count": 8
}
```

### 6.3 Transcript API

```python
@router.get("/{call_id}/transcript")
async def get_call_transcript(
    call_id: str,
    format: str = Query("json", description="'json' or 'text'"),
    supabase: Client = Depends(get_supabase)
):
    """Get call transcript in requested format."""
    
    # Try transcripts table first
    transcript = supabase.table("transcripts").select(
        "turns, full_text, word_count, turn_count"
    ).eq("call_id", call_id).execute()
    
    if transcript.data:
        if format == "text":
            return {"transcript": transcript.data[0].get("full_text", "")}
        else:
            return {
                "turns": transcript.data[0].get("turns", []),
                "metadata": {
                    "word_count": transcript.data[0].get("word_count"),
                    "turn_count": transcript.data[0].get("turn_count")
                }
            }
    
    # Fallback to calls table
    call = supabase.table("calls").select("transcript").eq("id", call_id).execute()
    return {"transcript": call.data[0].get("transcript", "") if call.data else ""}
```

---

## 7. Test Results & Verification

### 7.1 Recording Buffer Tests

```
tests/unit/test_recording_buffer.py

TestRecordingBuffer
  test_add_chunk PASSED
  test_get_complete_audio PASSED
  test_get_duration_seconds PASSED
  test_get_wav_bytes_valid_header PASSED
  test_clear PASSED

==================== 5 passed in 0.18s ====================
```

### 7.2 Recording Service Tests

```
tests/integration/test_recording_service.py

TestRecordingService
  test_generate_storage_path PASSED
  test_generate_storage_path_sanitizes_input PASSED
  test_save_recording PASSED
  test_save_and_link PASSED
  test_get_recording_url PASSED

==================== 5 passed in 0.92s ====================
```

### 7.3 API Endpoint Tests

```
tests/integration/test_recordings_api.py

TestRecordingsAPI
  test_list_recordings PASSED
  test_list_recordings_filtered PASSED
  test_stream_recording PASSED
  test_stream_recording_not_found PASSED (404)
  test_get_transcript_json PASSED
  test_get_transcript_text PASSED

==================== 6 passed in 0.65s ====================
```

### 7.4 WAV File Verification

```
Recording Test Results:
  - Sample file size: 64,044 bytes
  - Duration: 2.0 seconds
  - Format: PCM 16-bit, 16kHz, mono
  - WAV header: Valid (44 bytes)
  - Playback test: PASSED
```

---

## 8. Rationale Summary

### Key Technical Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage Backend | Supabase Storage | Integrated with DB, signed URLs, RLS |
| Audio Format | WAV PCM 16-bit | Universal compatibility, no decoding needed |
| Path Structure | tenant/campaign/call | Easy organization and access control |
| Streaming | Chunked response | Memory efficient, supports large files |

### Recording Flow

| Step | Action | Storage |
|------|--------|---------|
| 1 | Buffer audio chunks | Memory (RecordingBuffer) |
| 2 | Convert to WAV | Memory (io.BytesIO) |
| 3 | Upload to storage | Supabase Storage |
| 4 | Create DB record | recordings table |
| 5 | Update call reference | calls.recording_url |

### Files Created/Modified

| File | Purpose |
|------|---------|
| `app/domain/services/recording_service.py` | Buffer and storage service |
| `app/api/v1/endpoints/recordings.py` | Recording API endpoints |
| `database/schema_day10.sql` | Recordings and transcripts tables |

### Security Considerations

| Concern | Solution |
|---------|----------|
| Path traversal | Sanitize IDs in storage paths |
| Unauthorized access | Signed URLs with expiration |
| Tenant isolation | Tenant ID in storage path |
| Large files | Chunked streaming response |

---

*Document Version: 1.0*  
*Last Updated: Day 9 of Development Sprint*
