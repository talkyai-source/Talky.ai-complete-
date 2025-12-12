# Day 10 – Call Logs, Transcripts & Recording Storage

This document describes the Day 10 implementation: comprehensive call logging, recording storage, and transcript persistence.

## Overview

Day 10 adds full traceability to every call:
- **Recordings** are saved to Supabase Storage and linked to call records
- **Transcripts** are persisted as both structured JSON and plain text
- **Provider-agnostic** approach works with both Vonage and RTP gateways

## Architecture

```
                    ┌─────────────────────────┐
                    │   WebSocket Endpoint    │
                    └───────────┬─────────────┘
                                │
           ┌────────────────────┼────────────────────┐
           │                    │                    │
    ┌──────▼──────┐     ┌──────▼──────┐     ┌──────▼──────┐
    │   Vonage    │     │     RTP     │     │    Voice    │
    │   Gateway   │     │   Gateway   │     │   Pipeline  │
    │  (16kHz)    │     │   (8kHz)    │     │   Service   │
    └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
           │                   │                    │
           │          ┌────────▼────────┐          │
           │          │  Recording      │          │
           └─────────►│  Buffer         │          │
                      └────────┬────────┘          │
                               │                   │
                      ┌────────▼────────┐  ┌──────▼──────┐
                      │  Recording      │  │ Transcript  │
                      │  Service        │  │  Service    │
                      └────────┬────────┘  └──────┬──────┘
                               │                  │
                      ┌────────▼────────┐  ┌─────▼──────┐
                      │  Supabase       │  │  Supabase  │
                      │  Storage        │  │  Database  │
                      └─────────────────┘  └────────────┘
```

## Database Schema

### New Columns (calls table)
| Column | Type | Description |
|--------|------|-------------|
| `external_call_uuid` | VARCHAR(100) | Provider call UUID for webhook matching |
| `transcript_json` | JSONB | Structured transcript as JSON array |

### New Columns (recordings table)
| Column | Type | Description |
|--------|------|-------------|
| `tenant_id` | VARCHAR(255) | Multi-tenant isolation |
| `status` | VARCHAR(50) | Upload status tracking |

### New Table: transcripts
| Column | Type | Description |
|--------|------|-------------|
| `call_id` | UUID FK | Reference to calls table |
| `turns` | JSONB | Array of {role, content, timestamp} |
| `full_text` | TEXT | Plain text for search |
| `word_count` | INTEGER | Total word count |
| `turn_count` | INTEGER | Number of turns |

## API Endpoints

### GET /calls/{call_id}/transcript
Returns transcript in requested format.

**Query Parameters:**
- `format`: `json` (default) or `text`

**Response (JSON format):**
```json
{
  "format": "json",
  "turns": [
    {"role": "user", "content": "Hello", "timestamp": "..."},
    {"role": "assistant", "content": "Hi there!", "timestamp": "..."}
  ],
  "metadata": {
    "word_count": 10,
    "turn_count": 2
  }
}
```

## Key Components

### RecordingBuffer
Accumulates audio chunks during a call. Sample rate aware:
- Vonage: 16kHz
- RTP: 8kHz

### RecordingService
Handles storage operations:
- `save_recording()` - Upload to Supabase Storage
- `save_and_link()` - Upload + create DB record + update calls table

### TranscriptService
Handles transcript persistence:
- `accumulate_turn()` - Buffer user/assistant turns
- `save_transcript()` - Persist to database

## File Changes

| File | Changes |
|------|---------|
| `database/schema_day10.sql` | Schema updates |
| `app/domain/interfaces/media_gateway.py` | +recording buffer methods |
| `app/domain/services/recording_service.py` | NEW - Recording storage |
| `app/domain/services/transcript_service.py` | NEW - Transcript persistence |
| `app/infrastructure/telephony/vonage_media_gateway.py` | +recording buffer |
| `app/infrastructure/telephony/rtp_media_gateway.py` | +recording buffer |
| `app/domain/services/voice_pipeline_service.py` | +transcript accumulation |
| `app/api/v1/endpoints/websockets.py` | +save on call end |
| `app/api/v1/endpoints/calls.py` | +transcript endpoint |

## Testing

```bash
cd backend
python -m pytest tests/unit/test_day10.py -v
```
