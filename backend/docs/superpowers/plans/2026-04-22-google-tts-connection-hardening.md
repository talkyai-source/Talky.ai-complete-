# Google TTS Connection Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Google TTS streaming provider survive the 5-second "continuous input required" abort and any other bidi-stream failure by falling back to Google's unary REST-style `synthesize_speech` whenever the stream fails before emitting any audio — so a call cannot go silent until hangup.

**Architecture:** Keep the existing gRPC `streaming_synthesize` as the fast path (100-300 ms first audio). Wrap each response-chunk read in an `asyncio.wait_for(..., timeout=8s)` so stalls abort promptly. Track a `first_chunk_yielded` sentinel. On pre-first-chunk failure, invoke `TextToSpeechAsyncClient.synthesize_speech` (unary) for the current sentence and slice the returned LINEAR16 buffer into the same `AudioChunk` framing. On post-first-chunk failure, raise — no replay.

**Tech Stack:** `google-cloud-texttospeech` (already a dep), `asyncio`, `numpy` for Int16→Float32 conversion, existing `CircuitBreaker` from `app/utils/resilience.py`, pytest with `pytest-asyncio`.

---

## File Structure

- Modify: `app/infrastructure/tts/google_tts_streaming.py` — split `stream_synthesize` into `_streaming_attempt` + `_rest_fallback_attempt`; add read-timeout + no-replay guard
- Create: `tests/unit/test_google_tts_streaming_hardening.py` — behavioral tests with a mocked `TextToSpeechAsyncClient`
- Create: `docs/stability/README.md` — index (done in Task 0)
- Create: `docs/stability/google_tts_connection_hardening.md` — design doc (done in Task 0)
- Create: `docs/stability/2026-04-22-google-tts-connection-hardening-execution.md` — execution log (Task 6)

---

### Task 0: Stability docs scaffold

Already completed before this plan was written:
- `docs/stability/README.md`
- `docs/stability/google_tts_connection_hardening.md`

---

### Task 1: Failing unit tests for the hardening

**Files:**
- Create: `tests/unit/test_google_tts_streaming_hardening.py`

- [ ] **Step 1: Write four failing tests covering the four paths**

```python
"""
Unit tests for GoogleTTSStreamingProvider hardening (2026-04-22).

These tests lock in the four failure modes the hardening fix addresses:
  1. Streaming succeeds → REST fallback never called (happy path unchanged).
  2. Streaming fails BEFORE first audio chunk → REST fallback yields audio.
  3. Streaming fails AFTER first audio chunk → raises; REST not called.
  4. Response-chunk read stalls > read_timeout → aborts; REST fallback yields.
"""
import asyncio
import numpy as np
import pytest

from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider


def _pcm16_bytes(n_samples: int) -> bytes:
    """Deterministic 16-bit PCM payload for tests."""
    arr = np.arange(n_samples, dtype=np.int16)
    return arr.tobytes()


class _FakeStreamResponse:
    def __init__(self, audio_content: bytes):
        self.audio_content = audio_content


class _FakeResponseStream:
    """Async iterator over pre-baked audio chunks, optionally raising mid-way."""

    def __init__(self, chunks, raise_after: int | None = None, stall_on_index: int | None = None):
        self._chunks = list(chunks)
        self._raise_after = raise_after
        self._stall_on_index = stall_on_index
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._raise_after is not None and self._i >= self._raise_after:
            raise RuntimeError("simulated Google stream abort (409)")
        if self._stall_on_index is not None and self._i == self._stall_on_index:
            await asyncio.sleep(30)  # well past the 8 s read timeout
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._i]
        self._i += 1
        return _FakeStreamResponse(chunk)


class _FakeUnaryResponse:
    def __init__(self, audio_content: bytes):
        self.audio_content = audio_content


class _FakeGoogleClient:
    """Minimal mock of google.cloud.texttospeech.TextToSpeechAsyncClient."""

    def __init__(self, streaming_behavior, unary_audio=None):
        self._streaming_behavior = streaming_behavior
        self._unary_audio = unary_audio
        self.unary_calls = 0
        self.streaming_calls = 0

    async def streaming_synthesize(self, requests):
        self.streaming_calls += 1
        # Drain the request generator so it isn't GC'd mid-test.
        async for _ in requests:
            pass
        if isinstance(self._streaming_behavior, Exception):
            raise self._streaming_behavior
        return self._streaming_behavior

    async def synthesize_speech(self, request):
        self.unary_calls += 1
        return _FakeUnaryResponse(self._unary_audio or b"")


async def _build_provider(fake_client, *, read_timeout: float = 8.0):
    prov = GoogleTTSStreamingProvider()
    prov._client = fake_client
    prov._initialized = True
    prov._default_voice = "en-US-Chirp3-HD-Leda"
    prov._default_language = "en-US"
    prov._sample_rate = 24000
    prov._speaking_rate = 1.0
    prov._response_read_timeout_s = read_timeout
    return prov


@pytest.mark.asyncio
async def test_streaming_happy_path_yields_without_rest_fallback():
    chunks = [_pcm16_bytes(100), _pcm16_bytes(200)]
    client = _FakeGoogleClient(
        streaming_behavior=_FakeResponseStream(chunks)
    )
    prov = await _build_provider(client)

    out = []
    async for c in prov.stream_synthesize("Hello world.", "Leda", sample_rate=24000):
        out.append(c)

    assert len(out) == 2
    assert client.streaming_calls == 1
    assert client.unary_calls == 0


@pytest.mark.asyncio
async def test_streaming_fails_pre_first_chunk_triggers_rest_fallback():
    # raise_after=0 → no chunks yielded before the error
    client = _FakeGoogleClient(
        streaming_behavior=_FakeResponseStream([], raise_after=0),
        unary_audio=_pcm16_bytes(4096),
    )
    prov = await _build_provider(client)

    out = []
    async for c in prov.stream_synthesize("Hello world.", "Leda", sample_rate=24000):
        out.append(c)

    assert client.unary_calls == 1, "REST fallback must fire when streaming yields zero chunks"
    assert len(out) >= 1, "REST fallback must emit at least one AudioChunk"


@pytest.mark.asyncio
async def test_streaming_fails_post_first_chunk_raises_no_replay():
    # First chunk yields, then error on second read.
    client = _FakeGoogleClient(
        streaming_behavior=_FakeResponseStream([_pcm16_bytes(100)], raise_after=1),
        unary_audio=_pcm16_bytes(4096),
    )
    prov = await _build_provider(client)

    emitted = []
    with pytest.raises(RuntimeError):
        async for c in prov.stream_synthesize("Hello.", "Leda", sample_rate=24000):
            emitted.append(c)

    assert len(emitted) == 1, "Caller must have received exactly the one pre-error chunk"
    assert client.unary_calls == 0, "Must NOT fallback once audio has been emitted — would replay"


@pytest.mark.asyncio
async def test_response_read_stall_aborts_stream_and_falls_back():
    # Stall on index 0 → times out before any chunk is yielded → fallback should fire.
    client = _FakeGoogleClient(
        streaming_behavior=_FakeResponseStream([_pcm16_bytes(100)], stall_on_index=0),
        unary_audio=_pcm16_bytes(4096),
    )
    prov = await _build_provider(client, read_timeout=0.2)

    out = []
    async for c in prov.stream_synthesize("Hello.", "Leda", sample_rate=24000):
        out.append(c)

    assert client.unary_calls == 1
    assert len(out) >= 1
```

- [ ] **Step 2: Run tests, expect all four to fail**

Run: `venv/bin/pytest tests/unit/test_google_tts_streaming_hardening.py -v`
Expected: FAILS (attribute errors or behavior mismatches — REST fallback path doesn't exist yet).

- [ ] **Step 3: Do not commit yet** (we commit after the implementation passes)

---

### Task 2: Implement `_response_read_timeout_s` + per-chunk `wait_for`

**Files:**
- Modify: `app/infrastructure/tts/google_tts_streaming.py`

- [ ] **Step 1: Add the read-timeout attribute in `__init__`**

Inside `GoogleTTSStreamingProvider.__init__`, after the circuit breaker block:

```python
# Per-chunk response read timeout. If Google's stream stalls on a
# chunk longer than this, abort promptly so the REST fallback can
# take over before the caller hears dead air.
self._response_read_timeout_s: float = 8.0
```

- [ ] **Step 2: Allow config override in `initialize`**

After the existing `self._speaking_rate = config.get(...)` line:

```python
self._response_read_timeout_s = float(
    config.get("response_read_timeout_s", 8.0)
)
```

- [ ] **Step 3: Wrap the `async for response in response_stream` chunk fetch in `wait_for`**

Replace the existing `async for response in response_stream:` loop (inside the retry body) with an explicit `__aiter__/__anext__` loop that wraps each `__anext__` call in `asyncio.wait_for`. This is required for the stall test.

(Full replacement happens in Task 3, alongside the split into streaming-attempt + fallback. Don't leave the file in a half-refactored state between tasks — implement Tasks 2 and 3 together, then verify.)

---

### Task 3: Split into `_streaming_attempt` + `_rest_fallback_attempt` and rewire `stream_synthesize`

**Files:**
- Modify: `app/infrastructure/tts/google_tts_streaming.py`

- [ ] **Step 1: Add the two private async generators**

After `_split_into_sentences`, add:

```python
async def _streaming_attempt(
    self,
    text: str,
    selected_voice: str,
    language_code: str,
    sample_rate: int,
    speaking_rate: float,
) -> AsyncIterator[AudioChunk]:
    """
    Single streaming pass with per-chunk read timeout. Raises on any
    failure. Yields AudioChunks in Float32 format.
    """
    streaming_config = StreamingSynthesizeConfig(
        voice=VoiceSelectionParams(
            name=selected_voice,
            language_code=language_code,
        ),
        streaming_audio_config=StreamingAudioConfig(
            audio_encoding=AudioEncoding.PCM,
            sample_rate_hertz=sample_rate,
            speaking_rate=speaking_rate,
        ),
    )

    async def _request_generator():
        yield StreamingSynthesizeRequest(streaming_config=streaming_config)
        for sentence in self._split_into_sentences(text):
            if sentence.strip():
                yield StreamingSynthesizeRequest(
                    input=StreamingSynthesisInput(text=sentence)
                )

    response_stream = await self._client.streaming_synthesize(
        requests=_request_generator()
    )

    aiter_stream = response_stream.__aiter__()
    while True:
        try:
            response = await asyncio.wait_for(
                aiter_stream.__anext__(),
                timeout=self._response_read_timeout_s,
            )
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            logger.warning(
                "google_tts_streaming: chunk read stall >%.1fs — "
                "aborting stream for REST fallback",
                self._response_read_timeout_s,
            )
            raise

        if not response.audio_content:
            continue

        int16_array = np.frombuffer(response.audio_content, dtype=np.int16)
        float32_data = (int16_array.astype(np.float32) / 32768.0).tobytes()
        yield AudioChunk(
            data=float32_data,
            sample_rate=sample_rate,
            channels=1,
        )


async def _rest_fallback_attempt(
    self,
    text: str,
    selected_voice: str,
    language_code: str,
    sample_rate: int,
    speaking_rate: float,
) -> AsyncIterator[AudioChunk]:
    """
    Unary SynthesizeSpeech fallback. Runs when the streaming path has
    failed before emitting any audio. Returns the entire buffer in one
    RPC, sliced into the same AudioChunk framing as the streaming path.
    """
    from google.cloud.texttospeech_v1.types import (
        SynthesizeSpeechRequest,
        SynthesisInput,
        AudioConfig,
    )

    request = SynthesizeSpeechRequest(
        input=SynthesisInput(text=text),
        voice=VoiceSelectionParams(
            name=selected_voice,
            language_code=language_code,
        ),
        audio_config=AudioConfig(
            audio_encoding=AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
            speaking_rate=speaking_rate,
        ),
    )

    response = await self._client.synthesize_speech(request=request)
    audio_bytes = response.audio_content or b""
    # SynthesizeSpeech with LINEAR16 returns a WAV container; strip the
    # 44-byte RIFF header so the PCM matches the streaming path.
    if len(audio_bytes) >= 44 and audio_bytes[:4] == b"RIFF":
        audio_bytes = audio_bytes[44:]

    if not audio_bytes:
        return

    int16_array = np.frombuffer(audio_bytes, dtype=np.int16)
    float32_data = (int16_array.astype(np.float32) / 32768.0).tobytes()

    chunk_size = 16384
    for i in range(0, len(float32_data), chunk_size):
        yield AudioChunk(
            data=float32_data[i:i + chunk_size],
            sample_rate=sample_rate,
            channels=1,
        )
```

- [ ] **Step 2: Rewrite `stream_synthesize` to use both helpers**

Replace the body of `stream_synthesize` (keeping the same signature) with:

```python
async def stream_synthesize(
    self,
    text: str,
    voice_id: str,
    sample_rate: int = 24000,
    **kwargs,
) -> AsyncIterator[AudioChunk]:
    if not self._initialized or not self._client:
        raise RuntimeError(
            "GoogleTTSStreamingProvider not initialized. Call initialize() first."
        )

    selected_voice = self._normalize_voice_id(voice_id)
    language_code = kwargs.get("language_code", self._default_language)
    speaking_rate = kwargs.get("speaking_rate", self._speaking_rate)

    logger.debug(
        "Streaming TTS: voice=%s, text_length=%d", selected_voice, len(text)
    )

    first_chunk_yielded = False
    streaming_err: Optional[Exception] = None

    try:
        async with self._circuit:
            async for chunk in self._streaming_attempt(
                text, selected_voice, language_code, sample_rate, speaking_rate
            ):
                first_chunk_yielded = True
                yield chunk
            return  # streaming finished cleanly

    except CircuitOpenError as co:
        logger.error("Google TTS circuit breaker open: %s", co)
        raise RuntimeError(f"TTS provider unavailable: {co}") from co

    except Exception as e:
        streaming_err = e
        if first_chunk_yielded:
            logger.warning(
                "google_tts_streaming: streaming failed post-first-chunk — "
                "raising (no replay): %s",
                e,
            )
            raise RuntimeError(
                f"Google TTS streaming interrupted after first chunk: {e}"
            ) from e

    # Pre-first-chunk failure: fall back to unary REST-style synthesis.
    logger.warning(
        "google_tts_streaming: streaming failed pre-first-chunk — "
        "falling back to REST for sentence (%d chars): %s",
        len(text), streaming_err,
    )
    async for chunk in self._rest_fallback_attempt(
        text, selected_voice, language_code, sample_rate, speaking_rate
    ):
        yield chunk
```

- [ ] **Step 3: Run the new tests, expect them to pass**

Run: `venv/bin/pytest tests/unit/test_google_tts_streaming_hardening.py -v`
Expected: 4/4 PASS.

- [ ] **Step 4: Run the existing Google TTS integration suite to confirm no regression**

Run: `venv/bin/pytest tests/integration/test_google_tts.py -v`
Expected: unchanged status (either passes if env is set, or skips).

---

### Task 4: Full regression sweep

- [ ] **Step 1: Run the full unit + targeted integration suite**

Run: `venv/bin/pytest tests/unit tests/integration/test_agent_intelligence_2026_04_22.py -v`
Expected: all green.

- [ ] **Step 2: If anything red, diagnose and fix before moving on**

---

### Task 5: Execution log

**Files:**
- Create: `docs/stability/2026-04-22-google-tts-connection-hardening-execution.md`

- [ ] **Step 1: Write the execution log mirroring `docs/prompt/2026-04-22-agent-intelligence-execution.md`**

Sections: §1 What was built, §2 How it works, §3 Invariants + test mapping, §4 Deviations from plan, §5 Task checklist, §6 Verification commands.

---

## Self-review notes

- No signature changes to `stream_synthesize` — caller (`voice_pipeline_service.synthesize_and_send_audio`) is untouched.
- Circuit breaker still wraps the streaming attempt, not the fallback. Rationale: streaming failures count toward the trip; the fallback is the escape hatch, and counting its failures too would double-count.
- Barge-in detection in the pipeline still runs between `async for audio_chunk` iterations — unaffected.
- Happy-path latency: one extra attribute read (`self._response_read_timeout_s`) per chunk. Sub-microsecond. No measurable impact.
