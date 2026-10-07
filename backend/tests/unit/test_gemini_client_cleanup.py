"""Closing a voice provider releases both SDK-owned transports."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.infrastructure.llm.gemini import GeminiLLMProvider


async def test_cleanup_closes_both_transports_once_and_releases_reference():
    client = SimpleNamespace(aio=SimpleNamespace(aclose=AsyncMock()), close=Mock())
    provider = GeminiLLMProvider()
    provider._client = client
    await provider.cleanup()
    await provider.cleanup()
    client.aio.aclose.assert_awaited_once_with()
    client.close.assert_called_once_with()
    assert provider._client is None


async def test_sync_transport_still_closes_after_async_close_failure():
    client = SimpleNamespace(aio=SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("close failed"))), close=Mock())
    provider = GeminiLLMProvider()
    provider._client = client
    with pytest.raises(RuntimeError, match="close failed"):
        await provider.cleanup()
    client.close.assert_called_once_with()
    assert provider._client is None
