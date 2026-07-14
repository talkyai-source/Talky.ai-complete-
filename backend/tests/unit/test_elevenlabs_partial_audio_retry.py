"""
Unit tests for the ElevenLabs "no full-text retry after partial audio" fix.

Bug this locks in: a read timeout used to retry by re-POSTing the FULL
`text` from scratch even when some audio had already reached the caller
for THIS synthesis — the caller would hear the sentence restart, appended
after what already played (duplicated/garbled speech). The fix tracks
whether any chunk has been yielded; once true, a later timeout ends the
stream and raises `ElevenLabsPartialAudioError` instead of retrying.

Two paths are locked in:
  1. Timeout BEFORE any audio  -> retries exactly as before (unaffected).
  2. Timeout AFTER first chunk -> no re-POST; raises ElevenLabsPartialAudioError.
"""
from types import SimpleNamespace

import pytest

from app.infrastructure.providers.provider_concurrency import reset_guards_for_tests
from app.infrastructure.tts.elevenlabs_tts import (
    ElevenLabsPartialAudioError,
    ElevenLabsTTSProvider,
)


class _FakeChunkStream:
    """Fake for `response.content.iter_chunked(n)`.

    Yields `chunks` in order; once `len(chunks)` have been yielded, raises
    `raise_exc` (if given) instead of ending the stream.
    """

    def __init__(self, chunks, raise_exc=None):
        self._chunks = list(chunks)
        self._raise_exc = raise_exc
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            if self._raise_exc is not None:
                raise self._raise_exc
            raise StopAsyncIteration
        chunk = self._chunks[self._i]
        self._i += 1
        return chunk


class _FakeResponse:
    def __init__(self, status, chunk_stream, error_text=""):
        self.status = status
        self.headers = {}
        self.content = SimpleNamespace(iter_chunked=lambda n: chunk_stream)
        self._error_text = error_text

    async def text(self):
        return self._error_text


class _FakePostCM:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    """Records every POST and hands back scripted responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.post_calls = 0

    def post(self, url, headers=None, params=None, json=None):
        response = self._responses[self.post_calls]
        self.post_calls += 1
        return _FakePostCM(response)


def _build_provider(fake_session) -> ElevenLabsTTSProvider:
    prov = ElevenLabsTTSProvider()
    prov._session = fake_session
    prov._api_key = "test-key"
    prov._pool = None
    prov._voice_id = "voice-1"
    prov._sample_rate = 24000
    prov._model_id = "eleven_flash_v2_5"
    return prov


@pytest.fixture(autouse=True)
def _clean_guards():
    reset_guards_for_tests()
    yield
    reset_guards_for_tests()


@pytest.mark.asyncio
async def test_timeout_before_any_audio_still_retries():
    """Q2: a timeout with ZERO chunks yielded must retry exactly as before."""
    import asyncio as _asyncio

    attempt_1 = _FakeResponse(200, _FakeChunkStream([], raise_exc=_asyncio.TimeoutError()))
    attempt_2 = _FakeResponse(200, _FakeChunkStream([b"a" * 100, b"b" * 100]))
    session = _FakeSession([attempt_1, attempt_2])
    prov = _build_provider(session)

    out = []
    async for c in prov.stream_synthesize("Hello there.", "voice-1", sample_rate=24000):
        out.append(c)

    assert session.post_calls == 2, "pre-audio timeout must still re-POST and retry"
    assert len(out) == 2, "the retried attempt's audio must reach the caller"


@pytest.mark.asyncio
async def test_timeout_after_first_chunk_does_not_retry_and_raises_partial_error():
    """Q1: once audio has been yielded, a timeout must NOT re-POST the full
    text — it must end the stream and raise a distinguishable error."""
    import asyncio as _asyncio

    attempt_1 = _FakeResponse(
        200,
        _FakeChunkStream([b"first-chunk-audio"], raise_exc=_asyncio.TimeoutError()),
    )
    session = _FakeSession([attempt_1])  # only ONE response scripted — a second
    # POST would raise IndexError, proving no retry occurred if the test passes.
    prov = _build_provider(session)

    emitted = []
    with pytest.raises(ElevenLabsPartialAudioError):
        async for c in prov.stream_synthesize("Hello there.", "voice-1", sample_rate=24000):
            emitted.append(c)

    assert session.post_calls == 1, "must NOT re-POST once audio already reached the caller"
    assert len(emitted) == 1, "the caller must have kept the chunk that already played"
