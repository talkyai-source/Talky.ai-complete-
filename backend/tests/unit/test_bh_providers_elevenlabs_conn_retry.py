"""A connection error before the first audio byte must be retried like a
timeout / 429 / 5xx on the same request (chunks_yielded keeps it duplicate-safe)."""
from types import SimpleNamespace

import aiohttp
import pytest

from app.infrastructure.providers.provider_concurrency import reset_guards_for_tests
from app.infrastructure.tts import elevenlabs_tts
from app.infrastructure.tts.elevenlabs_tts import (
    ElevenLabsPartialAudioError,
    ElevenLabsTTSProvider,
)


class _Chunks:
    def __init__(self, chunks, raise_exc=None):
        self._chunks, self._raise, self._i = list(chunks), raise_exc, 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._chunks):
            if self._raise is not None:
                raise self._raise
            raise StopAsyncIteration
        self._i += 1
        return self._chunks[self._i - 1]


class _Resp:
    def __init__(self, chunks):
        self.status = 200
        self.headers = {}
        self.content = SimpleNamespace(iter_chunked=lambda n: chunks)

    async def text(self):
        return ""


class _CM:
    """Either yields a response or raises on connect."""

    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome

    async def __aexit__(self, *a):
        return False


class _Session:
    def __init__(self, outcomes):
        self._outcomes, self.post_calls = list(outcomes), 0

    def post(self, url, headers=None, params=None, json=None):
        o = self._outcomes[self.post_calls]
        self.post_calls += 1
        return _CM(o)


def _provider(session) -> ElevenLabsTTSProvider:
    p = ElevenLabsTTSProvider()
    p._session, p._api_key, p._pool = session, "test-key", None
    p._voice_id, p._sample_rate, p._model_id = "voice-1", 24000, "eleven_flash_v2_5"
    return p


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    reset_guards_for_tests()
    monkeypatch.setattr(elevenlabs_tts, "_EL_RETRY_BASE_DELAY", 0.0)
    yield
    reset_guards_for_tests()


async def _collect(p):
    return [c async for c in p.stream_synthesize("Hello there.", "voice-1", sample_rate=24000)]


@pytest.mark.asyncio
async def test_server_disconnect_before_audio_is_retried():
    session = _Session([
        aiohttp.ServerDisconnectedError("stale keep-alive"),
        _Resp(_Chunks([b"a" * 100, b"b" * 100])),
    ])
    out = await _collect(_provider(session))
    assert session.post_calls == 2
    assert len(out) == 2


@pytest.mark.asyncio
async def test_connection_error_retries_are_bounded_and_final_error_is_runtimeerror():
    session = _Session([aiohttp.ClientOSError("reset")] * (elevenlabs_tts._EL_MAX_RETRIES + 1))
    with pytest.raises(RuntimeError, match="ElevenLabs TTS"):
        await _collect(_provider(session))
    assert session.post_calls == elevenlabs_tts._EL_MAX_RETRIES + 1


@pytest.mark.asyncio
async def test_non_transient_client_error_is_not_retried():
    session = _Session([aiohttp.InvalidURL("bad url")])
    with pytest.raises(RuntimeError, match="ElevenLabs TTS error"):
        await _collect(_provider(session))
    assert session.post_calls == 1


@pytest.mark.asyncio
async def test_connection_error_after_partial_audio_is_not_retried():
    session = _Session([
        _Resp(_Chunks([b"first"], raise_exc=aiohttp.ClientOSError("reset mid-stream"))),
    ])
    with pytest.raises(ElevenLabsPartialAudioError):
        await _collect(_provider(session))
    assert session.post_calls == 1
