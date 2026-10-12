"""The Cartesia API key travels in the WS URL query string; a rejected
handshake (401/429/5xx) stringifies that URL. It must never reach a log line
or an exception message."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import aiohttp
import pytest
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from app.infrastructure.tts.cartesia import CartesiaTTSProvider

SECRET = "sk_car_SUPERSECRETKEY123"


def _handshake_error(status: int = 401) -> aiohttp.WSServerHandshakeError:
    url = URL(f"wss://api.cartesia.ai/tts/websocket?api_key={SECRET}&cartesia_version=2024-06-10")
    info = aiohttp.RequestInfo(url, "GET", CIMultiDictProxy(CIMultiDict()), url)
    return aiohttp.WSServerHandshakeError(info, (), status=status, message="Invalid response status")


class _RejectingCM:
    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *a):
        return False


def _provider(exc) -> CartesiaTTSProvider:
    p = CartesiaTTSProvider()
    p._api_key = SECRET
    session = MagicMock()
    # ws_connect is awaited on the persistent path and used as a CM on the
    # transient path; support both.
    class _Result(_RejectingCM):
        def __await__(self_inner):
            async def _raise():
                raise exc
            return _raise().__await__()
    session.ws_connect = lambda *a, **k: _Result(exc)
    p._session = session
    return p


def test_fixture_error_really_contains_the_key():
    assert SECRET in str(_handshake_error())


@pytest.mark.asyncio
async def test_persistent_ws_connect_failure_does_not_log_the_key(caplog):
    p = _provider(_handshake_error(429))
    with caplog.at_level(logging.DEBUG):
        await p._get_or_open_ws("call-1")
    assert "cartesia_ws_connect_failed" in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_transient_ws_failure_exception_text_does_not_contain_the_key():
    p = _provider(_handshake_error(401))
    with pytest.raises(RuntimeError) as exc_info:
        async for _ in p._stream_transient({"context_id": "c"}, 24000):
            pass
    err = exc_info.value
    assert SECRET not in str(err)
    # The chained original (full URL) must not ride along into tracebacks
    # / Sentry either.
    assert err.__cause__ is None
    assert err.__suppress_context__ is True
    assert "401" in str(err)


@pytest.mark.asyncio
async def test_preview_websocket_failure_does_not_leak_the_key(caplog):
    p = _provider(_handshake_error(401))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError) as exc_info:
            async for _ in p.stream_synthesize_websocket("hi", "voice-1"):
                pass
    assert SECRET not in str(exc_info.value)
    assert SECRET not in caplog.text
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


def test_redact_key_masks_value_but_keeps_context():
    from app.infrastructure.tts.cartesia import _redact_key
    out = _redact_key(f"401, url='wss://h/p?api_key={SECRET}&cartesia_version=1'")
    assert SECRET not in out
    assert "api_key=***&cartesia_version=1" in out
