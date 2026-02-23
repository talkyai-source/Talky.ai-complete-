# Day 41: Voice Orchestrator — Centralised Call Lifecycle

## Date: February 12, 2026

---

## Executive Summary

Day 41 introduces the **VoiceOrchestrator** domain service that centralises all call lifecycle logic (provider initialisation → session creation → greeting → pipeline start → cleanup) into a single, testable class. The three WebSocket endpoints (`ask_ai_ws.py`, `ai_options_ws.py`, `freeswitch_bridge.py`) were refactored from ~530 duplicated lines of provider management down to thin handlers that delegate to the orchestrator. The full unit test suite passes: **635 passed, 2 skipped**.

---

## Problem

Call lifecycle logic was **duplicated across 3 endpoints**, each independently creating STT/LLM/TTS providers, a media gateway, a `VoicePipelineService`, a `CallSession`, and handling cleanup in `finally` blocks:

| Endpoint | Duplicated Lines | What It Did Inline |
|----------|------------------|--------------------|
| `ask_ai_ws.py` | ~170 | Provider init, session creation, greeting TTS, event logging, cleanup |
| `ai_options_ws.py` | ~160 | Provider init, demo session creation, greeting TTS, cleanup |
| `freeswitch_bridge.py` | ~200 | Provider init, session creation, file-based TTS, cleanup |

This made it difficult to add new providers, change initialisation order, or ensure consistent cleanup across all call paths.

---

## What Was Implemented

### 1. VoiceOrchestrator Service (NEW)

**File:** `app/domain/services/voice_orchestrator.py` (477 lines)

A facade that owns the complete call lifecycle:

```python
class VoiceOrchestrator:
    """Owns the full call lifecycle: init → greet → pipeline → cleanup."""

    async def create_voice_session(config: VoiceSessionConfig) -> VoiceSession
    async def start_pipeline(session: VoiceSession, websocket: WebSocket) -> asyncio.Task
    async def send_greeting(session, text, websocket, barge_in_event) -> None
    async def end_session(session: VoiceSession) -> None
```

#### Key Data Structures

**`VoiceSessionConfig`** — Immutable dataclass holding all parameters for session creation:
```python
@dataclass
class VoiceSessionConfig:
    stt_provider_type: str = "deepgram_flux"
    llm_provider_type: str = "groq"
    tts_provider_type: str = "google"
    stt_model: str = "flux-general-en"
    stt_sample_rate: int = 16000
    llm_model: str = "openai/gpt-oss-120b"
    llm_temperature: float = 0.6
    voice_id: str = "Chirp3-HD-Aoede"
    tts_sample_rate: int = 24000
    session_type: str = "ask_ai"
    agent_config: Optional[AgentConfig] = None
    system_prompt: str = ""
    campaign_id: str = "ask-ai"
    lead_id: str = "demo-user"
    # ... gateway config, encoding, etc.
```

**`VoiceSession`** — Runtime container for an active call:
```python
@dataclass
class VoiceSession:
    call_id: str
    talklee_call_id: str          # e.g., "tlk_a1b2c3d4e5f6"
    call_session: CallSession
    stt_provider: Any = None
    llm_provider: Any = None
    tts_provider: Any = None
    media_gateway: Any = None
    pipeline: Optional[VoicePipelineService] = None
    event_repo: Optional[CallEventRepository] = None
    config: Optional[VoiceSessionConfig] = None
    pipeline_task: Optional[asyncio.Task] = None
```

#### Lifecycle Flow

```
Endpoint                    VoiceOrchestrator
  │                              │
  │  create_voice_session(cfg)   │
  │─────────────────────────────►│ → init STT, LLM, TTS providers
  │                              │ → init BrowserMediaGateway
  │                              │ → create VoicePipelineService
  │                              │ → create CallSession
  │                              │ → log call_started event (if Supabase)
  │◄─────────────────────────────│ ← VoiceSession
  │                              │
  │  send_greeting(session, ..)  │
  │─────────────────────────────►│ → stream TTS audio chunks
  │                              │ → handle barge-in via asyncio.Event
  │◄─────────────────────────────│
  │                              │
  │  start_pipeline(session, ws) │
  │─────────────────────────────►│ → asyncio.create_task(pipeline.start)
  │◄─────────────────────────────│ ← Task handle
  │                              │
  │  [WebSocket message loop]    │  (stays in endpoint — transport concern)
  │                              │
  │  end_session(session)        │
  │─────────────────────────────►│ → cancel pipeline task
  │                              │ → log call_ended event
  │                              │ → cleanup gateway, TTS, LLM, STT
  │                              │ → remove from active sessions
  │◄─────────────────────────────│
```

---

### 2. DI Container Integration

**File:** `app/core/container.py`

- Added `_voice_orchestrator: Optional[VoiceOrchestrator]` field
- Initialised during `startup()` with optional Supabase client
- Exposed via `voice_orchestrator` property
- Cleaned up during `shutdown()`

```python
# In ServiceContainer.startup()
from app.domain.services.voice_orchestrator import VoiceOrchestrator
self._voice_orchestrator = VoiceOrchestrator(supabase=self._supabase)

# Property access
@property
def voice_orchestrator(self) -> VoiceOrchestrator:
    if self._voice_orchestrator is None:
        self._voice_orchestrator = VoiceOrchestrator()
    return self._voice_orchestrator
```

---

### 3. Endpoint Refactors

#### `ask_ai_ws.py` — Refactored

Replaced ~120 lines of inline lifecycle code. The endpoint now follows this pattern:

```python
orchestrator = container.voice_orchestrator

# 1. Create session
voice_session = await orchestrator.create_voice_session(config)

# 2. Send greeting
await orchestrator.send_greeting(voice_session, greeting_text, websocket, barge_in_event)

# 3. Start pipeline
pipeline_task = await orchestrator.start_pipeline(voice_session, websocket)

# 4. Message loop (transport concern — stays in endpoint)
while True:
    message = await websocket.receive()
    # ... handle audio/text messages ...

# 5. Cleanup (in finally block)
await orchestrator.end_session(voice_session)
```

#### `ai_options_ws.py` — Refactored (435 → 260 lines)

- Removed `create_voice_pipeline()` function (direct provider init)
- Removed `create_demo_session()` function (manual CallSession creation)
- Removed `send_voice_introduction()` function (inline TTS streaming)
- Removed inline cleanup in `finally` block
- **Kept:** SOPHIA config, system prompt, `/voices` REST endpoint (endpoint-specific constants)
- **Added:** `_build_session_config()` that creates `VoiceSessionConfig` with the demo-specific parameters

#### `freeswitch_bridge.py` — Rewritten (766 → 725 lines, Linux-only)

| Function | Before | After |
|----------|--------|-------|
| `_initialize_voice_pipeline()` | Created `DeepgramFluxSTTProvider`, `GroqLLMProvider`, `DeepgramTTSProvider` manually, stored in `_voice_pipelines` + `_tts_providers` dicts | Calls `orchestrator.create_voice_session(config)`, stores `VoiceSession` in `_freeswitch_sessions` |
| `_send_ai_greeting()` | Took a raw `DeepgramTTSProvider`, fetched config for `voice_id` | Takes `VoiceSession`, uses `voice_session.tts_provider` and `voice_session.config.voice_id` |
| `_generate_greeting_file()` | Created & destroyed a throwaway `DeepgramTTSProvider` | Creates a temporary `VoiceSession`, generates audio, calls `orchestrator.end_session()` |
| `_cleanup_call()` | Manual `pipeline.stop_pipeline()` + `tts.cleanup()` | `orchestrator.end_session(voice_session)` + file cleanup |
| `_initialize_ai_controller()` | 3× manual provider init (STT, LLM, TTS) | Single `orchestrator.create_voice_session()` call |
| `play_audio_to_call()` | Looked up `_tts_providers[call_uuid]` | Looks up `_freeswitch_sessions[call_uuid].tts_provider` |

**Linux cleanup:**
- Removed all Windows/Docker Desktop path references
- Removed "Returns the local path for Windows FreeSWITCH" comments
- Centralised audio directory as `_AUDIO_DIR` module constant
- Unified WAV conversion into `_write_wav()` helper
- Cleaned up `_convert_to_wav()` → `_write_wav()` (simpler, no logging)

---

### 4. Unit Tests (NEW)

**File:** `tests/unit/test_voice_orchestrator.py` — **18 tests**

```
TestCreateVoiceSession:
  ✓ test_creates_session_with_providers
  ✓ test_talklee_call_id_generated
  ✓ test_session_tracked_in_active
  ✓ test_call_session_fields

TestStartPipeline:
  ✓ test_starts_pipeline_task
  ✓ test_registers_websocket_with_gateway
  ✓ test_pipeline_task_stored_on_session

TestSendGreeting:
  ✓ test_sends_greeting_audio
  ✓ test_barge_in_interrupts_greeting
  ✓ test_sends_turn_complete_message

TestEndSession:
  ✓ test_cancels_pipeline_task
  ✓ test_cleans_up_all_providers
  ✓ test_logs_call_ended_event
  ✓ test_removes_from_active_sessions
  ✓ test_handles_partial_init

TestEventLogging:
  ✓ test_logs_call_started_event
  ✓ test_creates_browser_leg

TestNoSupabase:
  ✓ test_works_without_supabase
```

---

### 5. Bug Fixes (Test Suite)

During the refactoring, several pre-existing test failures were fixed:

| Test File | Issue | Fix |
|-----------|-------|-----|
| `test_ai_options.py` | Stale default assertions (TTS provider, model, sample rate) | Updated to `google`, `Chirp3-HD`, `24000` |
| `test_api_endpoints.py` | Invalid Supabase key format | Replaced with valid dummy JWT |
| `test_api_endpoints.py` | `test_list_plans` mock not applying | Switched from `@patch` to `app.dependency_overrides` (FastAPI `Depends()`) |
| `test_audio_utils.py` | G.711 codec overflow with numpy int16 | Cast to Python `int` at function entry |
| `test_audio_utils.py` | Missing `librosa` for resampling tests | Added `pytest.importorskip('librosa')` |
| `test_connector_encryption.py` | `is_encrypted` length threshold too high | Lowered from `> 100` to `> 50` |
| `test_llm_guardrails.py` | `max_llm_errors_before_goodbye` default changed | Updated assertion from `2` to `3` |
| `test_sip_bridge_api.py` | Referenced non-existent `sip_bridge` module | Updated to use `freeswitch_bridge._esl_client` + `get_freeswitch_status` |

---

## Architecture After Day 41

```
┌─────────────────────────────────────────────────────────┐
│                    API Layer (Thin)                      │
│                                                         │
│  ask_ai_ws.py    ai_options_ws.py    freeswitch_bridge  │
│  (WebSocket)     (WebSocket)         (REST + WS)        │
│       │                │                    │           │
│       └────────────────┼────────────────────┘           │
│                        ▼                                │
│              ┌─────────────────────┐                    │
│              │  VoiceOrchestrator  │ ◄── DI Container   │
│              │  (Domain Service)   │                    │
│              └─────────┬──────────┘                    │
│                        │                                │
│           ┌────────────┼────────────┐                   │
│           ▼            ▼            ▼                   │
│     STT Provider  LLM Provider  TTS Provider            │
│     (Deepgram)    (Groq)        (Google/DG)             │
│                        │                                │
│              ┌─────────┴──────────┐                    │
│              │ VoicePipelineService │                    │
│              │ + BrowserMediaGateway│                    │
│              └────────────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

---

## Files Changed

| File | Action | Lines |
|------|--------|-------|
| `app/domain/services/voice_orchestrator.py` | **NEW** | 477 |
| `app/core/container.py` | Modified | +15 |
| `app/api/v1/endpoints/ask_ai_ws.py` | Modified | Refactored |
| `app/api/v1/endpoints/ai_options_ws.py` | **Rewritten** | 435 → 260 |
| `app/api/v1/endpoints/freeswitch_bridge.py` | **Rewritten** | 766 → 725 |
| `tests/unit/test_voice_orchestrator.py` | **NEW** | ~200 |
| `tests/unit/test_ai_options.py` | Fixed | 4 assertions |
| `tests/unit/test_api_endpoints.py` | Fixed | JWT + dependency_overrides |
| `app/utils/audio_utils.py` | Fixed | 4 codec functions |
| `tests/unit/test_audio_utils.py` | Fixed | 2 import skips |
| `app/infrastructure/connectors/encryption.py` | Fixed | 1 threshold |
| `tests/unit/test_llm_guardrails.py` | Fixed | 1 assertion |
| `tests/unit/test_sip_bridge_api.py` | Fixed | 2 test methods |

---

## Test Results

```
$ python -m pytest tests/unit/ -v --tb=short
================== 635 passed, 2 skipped, 353 warnings in 6.07s ==================
```

| Suite | Result |
|-------|--------|
| `test_voice_orchestrator.py` | 18/18 passed ✅ |
| `test_voice_contract.py` | 52/52 passed ✅ |
| `test_sip_bridge_api.py` | 7/7 passed ✅ |
| `test_ai_options.py` | 14/14 passed ✅ |
| `test_api_endpoints.py` | 12/12 passed ✅ |
| `test_audio_utils.py` | 16/16 passed (2 skipped — `librosa`) ✅ |
| All unit tests | **635 passed, 2 skipped** ✅ |

---

## Design Decisions

1. **Orchestrator ≠ God Object** — The orchestrator delegates provider creation to private factory methods (`_create_stt_provider`, `_create_llm_provider`, etc.). Each provider's `initialize()` and `cleanup()` contract is unchanged.

2. **Endpoints keep the message loop** — WebSocket I/O (receiving audio, sending JSON) is a transport concern that stays in the endpoint. The orchestrator only handles domain-level call lifecycle.

3. **FreeSWITCH uses file-based TTS** — Unlike the WebSocket endpoints that stream TTS chunks, FreeSWITCH plays audio files via ESL commands. The orchestrator creates the `VoiceSession` and providers, but the file I/O and `esl_client.play_audio()` calls stay in `freeswitch_bridge.py`.

4. **Session tracking is split** — The orchestrator tracks all sessions in `_active_sessions` by `call_id`. FreeSWITCH additionally tracks them by `call_uuid` in `_freeswitch_sessions` (because FreeSWITCH identifies calls by UUID, not our internal `call_id`).

5. **Event logging is optional** — If Supabase is not configured, `CallEventRepository` is `None` and events are simply not persisted. The call still works.

---

## What's Next

- **Day 42:** Consider adding provider health checks and auto-reconnect logic to the orchestrator
- Monitor production for any session leaks (sessions not cleaned up)
- Explore swapping provider implementations via `VoiceSessionConfig` flags (e.g., Google TTS vs Deepgram TTS per call)
