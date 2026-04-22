"""
Unit tests for GoogleTTSStreamingProvider hardening (2026-04-22).

Lock in the four paths the hardening fix defines:
  1. Streaming succeeds                   -> REST fallback never called.
  2. Streaming fails pre-first-chunk      -> REST fallback yields audio.
  3. Streaming fails post-first-chunk     -> raises; REST not called (no replay).
  4. Response-chunk read stalls > timeout -> aborts; REST fallback yields.
"""
import asyncio
from typing import List, Optional

import numpy as np
import pytest

from app.infrastructure.tts.google_tts_streaming import GoogleTTSStreamingProvider


def _pcm16_bytes(n_samples: int) -> bytes:
    arr = np.arange(n_samples, dtype=np.int16)
    return arr.tobytes()


class _FakeStreamResponse:
    def __init__(self, audio_content: bytes):
        self.audio_content = audio_content


class _FakeResponseStream:
    """Async iterator over pre-baked audio chunks. Can raise or stall."""

    def __init__(
        self,
        chunks: List[bytes],
        raise_after: Optional[int] = None,
        stall_on_index: Optional[int] = None,
    ):
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
            await asyncio.sleep(30)  # past any reasonable read_timeout
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._i]
        self._i += 1
        return _FakeStreamResponse(chunk)


class _FakeUnaryResponse:
    def __init__(self, audio_content: bytes):
        self.audio_content = audio_content


class _FakeGoogleClient:
    """Minimal mock of TextToSpeechAsyncClient for both RPCs we use."""

    def __init__(self, streaming_behavior, unary_audio: Optional[bytes] = None):
        self._streaming_behavior = streaming_behavior
        self._unary_audio = unary_audio or b""
        self.unary_calls = 0
        self.streaming_calls = 0

    async def streaming_synthesize(self, requests):
        self.streaming_calls += 1
        # Drain the async request generator (normally gRPC does this).
        async for _ in requests:
            pass
        if isinstance(self._streaming_behavior, Exception):
            raise self._streaming_behavior
        return self._streaming_behavior

    async def synthesize_speech(self, request):
        self.unary_calls += 1
        return _FakeUnaryResponse(self._unary_audio)


def _build_provider(fake_client, *, read_timeout: float = 8.0) -> GoogleTTSStreamingProvider:
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
        streaming_behavior=_FakeResponseStream(chunks),
    )
    prov = _build_provider(client)

    out = []
    async for c in prov.stream_synthesize("Hello world.", "Leda", sample_rate=24000):
        out.append(c)

    assert len(out) == 2
    assert client.streaming_calls == 1
    assert client.unary_calls == 0


@pytest.mark.asyncio
async def test_streaming_fails_pre_first_chunk_triggers_rest_fallback():
    client = _FakeGoogleClient(
        streaming_behavior=_FakeResponseStream([], raise_after=0),
        unary_audio=_pcm16_bytes(4096),
    )
    prov = _build_provider(client)

    out = []
    async for c in prov.stream_synthesize("Hello world.", "Leda", sample_rate=24000):
        out.append(c)

    assert client.unary_calls == 1, "REST fallback must fire when streaming yields zero chunks"
    assert len(out) >= 1, "REST fallback must emit at least one AudioChunk"


@pytest.mark.asyncio
async def test_streaming_fails_post_first_chunk_raises_no_replay():
    client = _FakeGoogleClient(
        streaming_behavior=_FakeResponseStream(
            [_pcm16_bytes(100)], raise_after=1
        ),
        unary_audio=_pcm16_bytes(4096),
    )
    prov = _build_provider(client)

    emitted = []
    with pytest.raises(RuntimeError):
        async for c in prov.stream_synthesize("Hello.", "Leda", sample_rate=24000):
            emitted.append(c)

    assert len(emitted) == 1, "Caller must have received exactly the one pre-error chunk"
    assert client.unary_calls == 0, "Must NOT fall back once audio has been emitted"


@pytest.mark.asyncio
async def test_response_read_stall_aborts_stream_and_falls_back():
    client = _FakeGoogleClient(
        streaming_behavior=_FakeResponseStream(
            [_pcm16_bytes(100)], stall_on_index=0
        ),
        unary_audio=_pcm16_bytes(4096),
    )
    prov = _build_provider(client, read_timeout=0.2)

    out = []
    async for c in prov.stream_synthesize("Hello.", "Leda", sample_rate=24000):
        out.append(c)

    assert client.unary_calls == 1
    assert len(out) >= 1
